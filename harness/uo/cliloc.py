"""Cliloc string table (Cliloc.enu) and argument formatting.

Servers send most system text as a cliloc number plus a tab-separated
argument string (S2C 0xC1 / 0xCC). The number is the stable identifier the
harness matches on; the rendered text is for humans (timelines, the
visualizer). The world model keeps only the number and raw args, so it never
depends on the install dir.

Ported from upstream ClassicUO-main src/ClassicUO.Assets/ClilocLoader.cs:
  ReadCliloc  header i32 + i16, then records `i32 number | u8 flag |
              i16 length | utf8 text` (all LE)
  Translate   `~N_NAME~` -> arg N (1-based, args split on '\\t'); an arg
              `#1234` is replaced by cliloc 1234
The Outlands Cliloc.enu (2026-09-29) is stored uncompressed (byte 3 != 0x8E);
a BWT-compressed table is rejected instead of misread.

Cliloc.enu is only read (never written); the install dir is off-limits for
writes (AGENTS.md).
"""
import re
import struct

CLILOC_PATH = "C:/Program Files (x86)/Ultima Online Outlands/Cliloc.enu"

_CACHE = {}  # path -> dict[int, str]
_PLACEHOLDER = re.compile(r"~(\d+)[^~]*~")


def parse(data: bytes) -> dict:
    """{number: text} from raw Cliloc bytes."""
    if len(data) > 3 and data[3] == 0x8E:
        raise ValueError("BWT-compressed cliloc table is not supported")
    table, p, n = {}, 6, len(data)
    while p + 7 <= n:
        number, _flag, length = struct.unpack_from("<iBh", data, p)
        p += 7
        if length < 0 or p + length > n:
            raise ValueError(f"cliloc record {number} overruns the file at {p}")
        table[number] = data[p:p + length].decode("utf-8", "replace")
        p += length
    return table


def load(path: str = CLILOC_PATH) -> dict:
    """The cliloc table at path (cached per path)."""
    table = _CACHE.get(path)
    if table is None:
        with open(path, "rb") as f:
            table = parse(f.read())
        _CACHE[path] = table
    return table


def translate(table: dict, number: int, args: str = "") -> str:
    """Render cliloc `number` with its '\\t'-separated args, like the client.
    Unknown numbers render as `#<number>` so they stay greppable."""
    base = table.get(number)
    if base is None:
        return f"#{number}" + (f" [{args}]" if args else "")
    parts = (args or "").split("\t")

    def sub(m):
        i = int(m.group(1)) - 1
        a = parts[i] if 0 <= i < len(parts) else ""
        if len(a) > 1 and a[0] == "#" and a[1:].isdigit():
            a = table.get(int(a[1:]), "")
        return a

    return _PLACEHOLDER.sub(sub, base)
