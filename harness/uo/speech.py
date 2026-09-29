"""Speech keyword table (speech.mul) and 0xAD keyword encoding.

The stock client scans every outgoing unicode speech line against the
speech.mul keyword table. If any keyword matches, the 0xAD packet switches to
the *encoded* form (type |= 0xC0, a packed keyword-id list, UTF-8 text);
otherwise it is plain UTF-16BE. Servers (RunUO lineage) trigger NPC behaviour
such as the banker's "bank" response off those ids, so the harness must emit
exactly what the client would.

Ported from upstream ClassicUO-main:
  src/ClassicUO.Assets/SpeechesLoader.cs      Load / IsMatch / GetKeywords /
                                              SpeechEntry
  src/ClassicUO.Client/Network/OutgoingPackets.cs:992-1033
                                              keyword nibble packing
The Outlands client's own encoder (decompiled Send_UnicodeSpeechRequest
@ 0x140151c20) has the same structure, and a live stock-client capture
(logs/session_20260929_161433) of "bank" is reproduced byte-for-byte:
`ad 0016 c0 02b2 0003 454e5500 | 00 20 02 00 20 | 62616e6b 00`.

speech.mul is only read (never written); the install dir is off-limits for
writes (AGENTS.md).
"""
import struct

SPEECH_MUL_PATH = "C:/Program Files (x86)/Ultima Online Outlands/speech.mul"

_CACHE = {}  # path -> tuple[SpeechEntry, ...]


class SpeechEntry:
    """One speech.mul record (port of ClassicUO SpeechEntry)."""
    __slots__ = ("keyword_id", "keywords", "check_start", "check_end")

    def __init__(self, id_: int, keyword: str):
        # C#: KeywordID = (short) id
        self.keyword_id = id_ - 0x10000 if id_ & 0x8000 else id_
        # C#: keyword.Split('*', StringSplitOptions.RemoveEmptyEntries)
        self.keywords = tuple(k for k in keyword.split("*") if k)
        self.check_start = len(keyword) > 0 and keyword[0] == "*"
        self.check_end = len(keyword) > 0 and keyword[-1] == "*"


def _parse(data: bytes):
    entries = []
    pos, end = 0, len(data)
    while pos < end:
        if pos + 4 > end:
            raise ValueError(f"speech.mul truncated record header at {pos}")
        id_, length = struct.unpack_from(">HH", data, pos)
        pos += 4
        if length > 0:
            if pos + length > end:
                raise ValueError(f"speech.mul truncated record text at {pos}")
            # .NET Encoding.UTF8.GetString replaces invalid bytes with U+FFFD
            text = data[pos:pos + length].decode("utf-8", "replace")
            pos += length
            entries.append(SpeechEntry(id_, text))
    return tuple(entries)


def load(path: str = None):
    """Return the parsed speech.mul entries (file order), cached per path."""
    path = path or SPEECH_MUL_PATH
    entries = _CACHE.get(path)
    if entries is None:
        with open(path, "rb") as f:
            entries = _parse(f.read())
        _CACHE[path] = entries
    return entries


def _fold(s: str) -> str:
    """Per-char case fold that keeps string length, so indices found in the
    folded text are valid in the original (needed for the boundary check).

    [INFERENCE] Approximates .NET InvariantCultureIgnoreCase; identical for
    ASCII and simple case pairs. ICU/NLS ignorable-character handling is not
    modelled."""
    out = []
    for c in s:
        lc = c.lower()
        out.append(lc if len(lc) == 1 else c)
    return "".join(out)


def _is_boundary(c: str) -> bool:
    # C#: char.IsWhiteSpace(c) || !char.IsLetter(c)
    return c.isspace() or not c.isalpha()


def is_match(text: str, entry: SpeechEntry) -> bool:
    """Port of SpeechesLoader.IsMatch."""
    ftext = _fold(text)
    n = len(text)
    for kw in entry.keywords:
        k = len(kw)
        if k > n or k == 0:
            continue
        fkw = _fold(kw)
        # IndexOf(kw, 0, kw.Length): kw must occupy the first k chars
        if not entry.check_start and not ftext.startswith(fkw):
            continue
        # IndexOf(kw, n - k): kw must occupy the last k chars
        if not entry.check_end and not ftext.endswith(fkw):
            continue
        idx = ftext.find(fkw)
        while idx >= 0:
            if ((idx - 1 < 0 or _is_boundary(text[idx - 1])) and
                    (idx + k >= n or _is_boundary(text[idx + k]))):
                return True
            idx = ftext.find(fkw, idx + 1)
    return False


def get_keywords(text: str, path: str = None) -> list:
    """Keyword ids the stock client attaches to `text` (port of
    SpeechesLoader.GetKeywords): one id per matching speech.mul entry,
    duplicates kept, sorted by KeywordID. [] means unencoded speech."""
    text = text.strip(" ")  # C#: TrimStart(' ').TrimEnd(' ')
    ids = [e.keyword_id for e in load(path) if is_match(text, e)]
    ids.sort()
    return ids


def encode_keywords(ids) -> bytes:
    """Pack keyword ids exactly as OutgoingPackets.cs:992-1033 does:
    12-bit count, then 12-bit ids, nibble-packed big-endian, low-nibble
    zero-padded to a whole byte."""
    ids = list(ids)
    n = len(ids)
    out = bytearray([(n >> 4) & 0xFF])
    pending = n & 15
    flag = False
    for kid in ids:
        if flag:
            out.append((kid >> 4) & 0xFF)
            pending = kid & 15
        else:
            out.append(((pending << 4) | ((kid >> 8) & 15)) & 0xFF)
            out.append(kid & 0xFF)
        flag = not flag
    if not flag:
        out.append((pending << 4) & 0xFF)
    return bytes(out)
