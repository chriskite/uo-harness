import re, collections

STRINGS = r"C:/Users/chris/uo-harness/strings.txt"
OUT = r"C:/Users/chris/uo-harness/grep_hits.txt"

cats = {
    "anticheat_core": r"anti.?cheat|speedhack|auto.?click|auto.?keyboard|macro.?detect|bot.?detect|input.?inject|synthetic.?input",
    "detect_monitor": r"\bdetect|watchdog|sentinel|monitor(ing)?\b|heartbeat|keep.?alive|challenge|integrity|verif",
    "ban_enforce": r"\bban|\bkick|violation|suspicious|flagged|penalt|jail|captcha",
    "screen_capture": r"screenshot|screen.?shot|capture.?screen|bitblt|printwindow|getdc\b|gdiplus",
    "proc_enum": r"toolhelp|enumprocess|enumwindows|getwindowtext|getclassname|queryfullprocess|process32|module32",
    "dbg_antidbg": r"debugger|x64dbg|olly|windbg|cheatengine|cheat engine|process ?hacker|dnspy|wireshark|fiddler|procmon",
    "input_api": r"sendinput|keybd_event|mouse_event|getasynckeystate|setwindowshookex|blockinput|interception",
    "net_endpoints": r"https?://[a-z0-9.-]+|sentry|bugsnag|appcenter|discord(app)?\.com|api\.|\.onion",
    "outlands_types": r"outlands[A-Za-z.]*(?:guard|shield|warden|secur|protect|safe|report|cheat|watch)|(?:guard|shield|warden|secur|protect|safe|report|cheat|watch)[A-Za-z.]*outlands",
    "crypto_proto": r"blowfish|huffman|twofish|md5|sha1|sha256|xxhash|crc32",
    "razor_ac": r"razor.*(?:restrict|limit|block|disabl|deny)|(?:restrict|limit|block|disabl|deny).*razor",
}

lines = open(STRINGS, encoding="utf-8", errors="replace").read().splitlines()
hits = collections.defaultdict(list)
for ln in lines:
    try:
        off, enc, s = ln.split(" ", 2)
    except ValueError:
        continue
    for cat, pat in cats.items():
        if re.search(pat, s, re.I):
            hits[cat].append((off, s.strip()))

out = open(OUT, "w", encoding="utf-8")
for cat in cats:
    out.write(f"\n===== {cat} ({len(hits[cat])}) =====\n")
    seen = set()
    for off, s in hits[cat]:
        if s in seen or len(s) > 300:
            continue
        seen.add(s)
        out.write(f"{off}  {s}\n")
        if len(seen) >= 120:
            out.write("... truncated ...\n")
            break
out.close()
print({c: len(hits[c]) for c in cats})

# context dump around the two key offsets in the ORIGINAL exe
import io
for label, lo, hi in [("SPEEDHACK_CLUSTER", 0x1BE0100, 0x1BE0400), ("CAPTCHA_GUMP", 0x189DB00, 0x189DD00)]:
    print(f"\n--- context {label} ---")
    for ln in lines:
        try:
            off = int(ln.split(" ", 1)[0], 16)
        except ValueError:
            continue
        if lo <= off <= hi:
            print(ln[:200])
