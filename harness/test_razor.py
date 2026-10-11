"""Tests for harness/razor.py (Razor profile hotkeys) and the viz's /api/razor routes,
on a temp Assistant dir built here (never the install dir).

- replace_hotkeys: byte-exact round trip (BOM, CRLF, tabs, &amp;), edits stay inside
  the <hotkeys> block, <hotkeys /> <-> filled <-> empty, two blocks refused
- validate: modifier keys, mod range, non-bool send, duplicate combo/name, control
  characters, size cap; mouse keys never pass through
- in_use / lock_reason: every reason branch
- save: stale version refused (file untouched), backup of the old bytes, no-op save
- catalog: scripts, macros, dress lists, agents, Magery spells (circle-relative ids),
  skills, Outlands commands, inferred actions, missing lang ids
- routes: GET summary/profile (no passwords in the body), POST save, stale, unknown,
  bad key, locked while a client runs with no proxy sessions

Run: python harness/test_razor.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import razor  # noqa: E402
import viz_server  # noqa: E402
from test_viz import _free_ports  # noqa: E402

FAILURES = []

BOM = b"\xef\xbb\xbf"
A_XML = (BOM + "\r\n".join([
    '<?xml version="1.0" encoding="utf-8"?>',
    "<profile>",
    '\t<property name="ShowWelcome" type="System.Boolean">False</property>',
    "\t<passwords />",
    "\t<agents>",
    '\t\t<Organizer-1 enabled="False" />',
    '\t\t<Organizer-2 enabled="False" />',
    "\t</agents>",
    "\t<dresslists>",
    '\t\t<list name="PvP" undressbag="0" />',
    "\t</dresslists>",
    "\t<hotkeys>",
    '\t\t<key mod="0" key="71" send="False">L:1058</key>',
    '\t\t<key mod="2" key="70" send="True">Play Script: Pull &amp; Demo</key>',
    '\t\t<key mod="1" key="86" send="False">L:2120</key>',
    "\t</hotkeys>",
    "\t<friends />",
    "</profile>",
    "",
]).encode())
B_XML = (BOM + "\r\n".join([
    '<?xml version="1.0" encoding="utf-8"?>',
    "<profile>",
    "\t<passwords />",
    "\t<hotkeys />",
    "</profile>",
    "",
]).encode())
LANG = {1058: "Last Target", 1059: None, 1210: "Items", 1211: "Targets", 1212: "Agents", 1213: "Dress",
        1214: "Macros", 1215: "Spells", 1216: "Skills", 1217: "Misc", 1218: "Friends", 1219: "Scripts",
        1220: "Outlands Commands", 1251: "1st", 1252: "2nd", 1291: "Use Items", 2101: "Stop Script",
        2120: "Target Self Then Last", 2121: "Target Closest Then Last", 2249: "Menus", 2527: "Pouch",
        1084: "Hello", 2097: "World"}


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def make_dir() -> str:
    d = tempfile.mkdtemp(prefix="razor_test_")
    for sub in ("Profiles", "Language", "Scripts", os.path.join("Macros", "Sub")):
        os.makedirs(os.path.join(d, sub))
    with open(os.path.join(d, "Profiles", "A.xml"), "wb") as f:
        f.write(A_XML)
    with open(os.path.join(d, "Profiles", "B.xml"), "wb") as f:
        f.write(B_XML)
    with open(os.path.join(d, "Profiles", "chars.lst"), "w") as f:
        f.write("0x104A60=A\nnonsense\n0x2550DB=B\n")
    with open(os.path.join(d, "Language", "Razor_lang.enu"), "w", newline="") as f:
        f.write("// Razor language file\r\n[controls]\r\nMainForm::tabs=Tabs\r\n[strings]\r\n")
        f.write("".join(f"{k}={v}\r\n" for k, v in LANG.items() if v is not None))
    with open(os.path.join(d, "spells.def"), "w") as f:
        f.write("# Flag | SpellNo | Circle | Name | Words | Regs\n"
                "H | 1 | 1 | Clumsy | Uus Jux | BM | NS\n"
                "B | 3 | 2 | Cure | An Nox | GA | GI\n"
                "H | 1 | 10 | Animate Dead | Uus Corp | DB | GD\n")
    with open(os.path.join(d, "outlandscommands.def"), "w") as f:
        f.write("# Command | SubMenu | LanguageStringId\n[Pouch | 1000 | 2527\n[Gone | 1000 | 2599\n")
    open(os.path.join(d, "Scripts", "Dig.razor"), "w").close()
    open(os.path.join(d, "Macros", "Sub", "Heal.macro"), "w").close()
    return d


def bad(raw) -> str | None:
    try:
        razor.validate(raw)
    except ValueError as e:
        return str(e)
    return None


def test_replace():
    a = razor.parse_bindings(A_XML)
    check("parse A: 3 bindings, & unescaped",
          [b.name for b in a] == ["L:1058", "Play Script: Pull & Demo", "L:2120"] and a[1].send and a[1].mod == 2, a)
    check("round trip is byte-exact", razor.replace_hotkeys(A_XML, a) == A_XML)
    moved = [a[0], razor.Binding(a[1].name, 4, 72, False), a[2]]
    new = razor.replace_hotkeys(A_XML, moved)
    i, j = A_XML.index(b"<hotkeys>"), A_XML.index(b"</hotkeys>") + len(b"</hotkeys>")
    k = new.index(b"</hotkeys>") + len(b"</hotkeys>")
    check("edit: bytes outside the block unchanged", new[:i] == A_XML[:i] and new[k:] == A_XML[j:])
    check("edit: BOM kept, CRLF only", new.startswith(BOM) and b"\n" not in new.replace(b"\r\n", b""))
    check("edit: & stays escaped, new line written",
          b'\t\t<key mod="4" key="72" send="False">Play Script: Pull &amp; Demo</key>\r\n' in new)
    check("edit: parses back", razor.parse_bindings(new) == moved)
    two = [razor.Binding("L:1058", 0, 65, False), razor.Binding("Dress: X", 2, -1, False)]
    filled = razor.replace_hotkeys(B_XML, two)
    check("B: <hotkeys /> filled", razor.parse_bindings(filled) == two and b"\t\t<key mod=\"2\" key=\"-1\"" in filled)
    check("B: emptied again is the original bytes", razor.replace_hotkeys(filled, []) == B_XML)
    dup = A_XML.replace(b"\t<friends />", b"\t<hotkeys />")
    try:
        razor.replace_hotkeys(dup, a)
        check("two <hotkeys> -> ValueError", False)
    except ValueError as e:
        check("two <hotkeys> -> ValueError", "2 <hotkeys>" in str(e), e)


def test_validate():
    ok = {"name": "L:1058", "mod": 0, "key": 71, "send": False}
    check("valid binding passes", bad([ok]) is None)
    check("key 17 refused", bad([{**ok, "key": 17}]) == "bindings[0].key: 17 can't be bound", bad([{**ok, "key": 17}]))
    check("mod 8 refused", (bad([{**ok, "mod": 8}]) or "").startswith("bindings[0].mod"))
    check("send 'False' refused", (bad([{**ok, "send": "False"}]) or "").startswith("bindings[0].send"))
    check("bool mod refused", (bad([{**ok, "mod": True}]) or "").startswith("bindings[0].mod"))
    e = bad([ok, {**ok, "name": "L:1059", "mod": 2, "key": 70}, {**ok, "name": "x", "mod": 2, "key": 70}])
    check("duplicate combo refused", e == "bindings[2]: Ctrl+F duplicates bindings[1]", e)
    e = bad([ok, {**ok, "key": 72}])
    check("duplicate name refused", e == "bindings[1].name: 'L:1058' is bound twice", e)
    check("control character refused", (bad([{**ok, "name": "a\tb"}]) or "").startswith("bindings[0].name"))
    check("padded name refused", (bad([{**ok, "name": " a"}]) or "").startswith("bindings[0].name"))
    many = [{"name": f"n{i}", "mod": i % 8, "key": 65 + i // 8 % 26, "send": False} for i in range(1001)]
    check("1001 bindings refused", (bad(many) or "").startswith("bindings: 1001"))
    out = razor.validate([{**ok, "key": -3, "send": True, "label": "x", "known": True}])
    check("mouse key forces send=False, extras ignored", out == [razor.Binding("L:1058", 0, -3, False)], out)


def test_in_use():
    cm = {0x104A60: "A", 0x2550DB: "B"}
    logan = {"serial": "0x00104A60", "name": "Logan Wolf"}
    cases = [
        (None, None, False, "can't list running game clients"),
        (0, None, True, "no game client running"),
        (1, None, False, "a game client is running but the proxy's characters are unavailable"),
        (2, [logan], False, "2 game client(s) running but 1 character(s) logged in through the proxy"),
        (1, [{"serial": "0x00000001", "name": "Stranger"}], False, "Stranger has no profile in chars.lst"),
        (1, [{"serial": None, "name": None}], False, "a character has no profile in chars.lst"),
        (1, [logan], True, "in use by Logan Wolf"),
    ]
    for clients, sessions, known, reason in cases:
        u = razor.in_use(cm, clients, sessions)
        check(f"in_use({clients}, {sessions and len(sessions)}): {reason}", (u.known, u.reason) == (known, reason),
              (u.known, u.reason))
    u = razor.in_use(cm, 1, [logan])
    check("in_use maps profile -> names", u.by == {"A": ["Logan Wolf"]}, u.by)
    r = razor.lock_reason(u, "A")
    check("lock_reason: in-use profile", r is not None and r.startswith("in use by Logan Wolf: Razor rewrites"), r)
    check("lock_reason: free profile", razor.lock_reason(u, "B") is None)
    check("lock_reason: unknown -> every profile", razor.lock_reason(razor.in_use(cm, None, None), "B")
          == "can't list running game clients")


def test_save():
    d, bk = make_dir(), tempfile.mkdtemp(prefix="razor_bk_")
    path = os.path.join(d, "Profiles", "A.xml")
    a = razor.parse_bindings(A_XML)
    try:
        razor.save(d, "A", "0" * 64, a[:1], bk)
        check("stale version -> Stale", False)
    except razor.Stale:
        check("stale version -> Stale", True)
    check("stale: file unchanged", open(path, "rb").read() == A_XML)
    try:
        razor.save(d, "../A", razor.version(A_XML), a, bk)
        check("unknown profile name -> UnknownProfile", False)
    except razor.UnknownProfile:
        check("unknown profile name -> UnknownProfile", True)
    res = razor.save(d, "A", razor.version(A_XML), a[:1], bk, now=lambda: 1_700_000_000)
    new = open(path, "rb").read()
    check("save: file rewritten", razor.parse_bindings(new) == a[:1])
    check("save: new version returned", res["version"] == razor.version(new), res)
    files = os.listdir(bk)
    check("save: one backup with the old bytes", len(files) == 1 and files[0].startswith("A_")
          and open(os.path.join(bk, files[0]), "rb").read() == A_XML, files)
    check("save: no tmp left", not os.path.exists(path + ".uoh-tmp"))
    res2 = razor.save(d, "A", res["version"], a[:1], bk, now=lambda: 1_700_000_000)
    check("identical save: no backup, same version", res2 == {"version": res["version"], "backup": None}, res2)
    check("identical save: no new file", len(os.listdir(bk)) == 1)
    razor.save(d, "A", res["version"], a, bk, now=lambda: 1_700_000_000)
    check("same-second backup gets a _2 suffix", any(f.endswith("_2.xml") for f in os.listdir(bk)), os.listdir(bk))
    shutil.rmtree(d)
    shutil.rmtree(bk)


def test_catalog():
    d = make_dir()
    root = ET.fromstring(A_XML)
    bound = razor.bound_names(d)
    cat = razor.catalog(d, root, [(1, "Anatomy"), (46, "Mining")], bound)
    by = {a["name"]: a for a in cat}
    for n in ("Play Script: Dig", "Play: Sub\\Heal", "Dress: PvP", "Undress: PvP", "Toggle: PvP",
              "Organizer Agent-02", "Set Organizer HotBag-02", "Restock Agent-10"):
        check(f"catalog has {n!r}", n in by)
    check("catalog: organizers stop at the profile's max", "Organizer Agent-03" not in by)
    check("catalog: Magery spell, circle-relative id",
          by.get("L:3002011", {}).get("path") == ["Spells", "1st"] and by["L:3002011"]["label"] == "Clumsy"
          and by.get("L:3002021", {}).get("label") == "Cure", [by.get("L:3002011"), by.get("L:3002021")])
    check("catalog: no Necro spell from spells.def", not any(a["label"] == "Animate Dead" for a in cat))
    check("catalog: outlands command with its submenu",
          by.get("L:2527") == {"name": "L:2527", "label": "Pouch", "path": ["Outlands Commands", "Menus"],
                               "confirmed": True}, by.get("L:2527"))
    check("catalog: outlands command missing from lang skipped", "L:2599" not in by)
    check("catalog: skills from the injected list",
          by.get("L:1044061", {}).get("label") == "Anatomy" and "L:1044106" in by)
    check("catalog: bound inferred action confirmed", by.get("L:2120", {}).get("confirmed") is True)
    check("catalog: unbound inferred action unconfirmed", by.get("L:2121", {}).get("confirmed") is False)
    check("catalog: static id missing from lang skipped", "L:1059" not in by)
    order = []
    for a in cat:
        c = a["path"][0] if a["path"] else ""
        if not order or order[-1] != c:
            order.append(c)
    check("catalog: categories contiguous, in Razor order",
          order == ["Targets", "Agents", "Dress", "Macros", "Spells", "Skills", "Scripts", "Outlands Commands"],
          order)
    check("catalog: no duplicate names", len(by) == len(cat))
    check("catalog without a profile root: no agents/dress", "Dress: PvP" not in
          {a["name"] for a in razor.catalog(d, None, [], bound)})
    shutil.rmtree(d)


class StubFeed:
    subscribers = []

    def health(self):
        return {}


def http(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_routes():
    d, bk, tmp = make_dir(), tempfile.mkdtemp(prefix="razor_bk_"), tempfile.mkdtemp(prefix="razor_srv_")
    port, = _free_ports(1)
    srv = viz_server.VizServer(("127.0.0.1", port), StubFeed(), os.path.join(ROOT, "viz", "dist"),
                               os.path.join(tmp, "harness.db"), None, nystul_db=os.path.join(tmp, "nystul.db"),
                               razor_dir=d, razor_backups=bk, razor_clients=lambda: 0)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        code, body = http("GET", base + "/api/razor")
        s = json.loads(body)
        names = [p["name"] for p in s.get("profiles", [])]
        check("GET /api/razor lists A and B, unlocked", code == 200 and names == ["A", "B"]
              and not any(p["locked"] for p in s["profiles"]) and s["clients"] == 0, body[:300])
        check("summary: chars.lst serials and binding counts",
              s["profiles"][0]["characters"] == ["0x00104A60"] and s["profiles"][0]["bindings"] == 3)
        code, body = http("GET", base + "/api/razor/profile?name=A")
        doc = json.loads(body)
        check("GET profile A: labelled bindings + actions", code == 200 and doc["bindings"][0]["label"] == "Last Target"
              and doc["bindings"][0]["known"] and len(doc["actions"]) > 10, body[:300])
        check("GET profile A: unknown script binding flagged",
              doc["bindings"][1]["known"] is False and doc["bindings"][1]["label"] == "Play Script: Pull & Demo")
        check("GET profile: no passwords in the body", "passwords" not in body)
        code, _ = http("GET", base + "/api/razor/profile?name=" + urllib.parse.quote("../A"))
        check("GET unknown profile -> 404", code == 404, code)
        new = [{"name": "L:1058", "mod": 0, "key": 72, "send": False}]
        code, body = http("POST", base + "/api/razor/profile", {"name": "A", "version": doc["version"], "bindings": new})
        r = json.loads(body)
        check("POST valid -> 200 + backup", code == 200 and r["ok"] and r["backup"], body)
        saved = open(os.path.join(d, "Profiles", "A.xml"), "rb").read()
        check("POST: file updated", b'<key mod="0" key="72" send="False">L:1058</key>' in saved)
        code, body = http("POST", base + "/api/razor/profile", {"name": "A", "version": doc["version"], "bindings": new})
        check("POST old version -> 409 stale", code == 409 and json.loads(body).get("stale") is True, body)
        code, _ = http("POST", base + "/api/razor/profile", {"name": "Nope", "version": "x", "bindings": new})
        check("POST unknown profile -> 404", code == 404, code)
        code, body = http("POST", base + "/api/razor/profile",
                          {"name": "A", "version": r["version"], "bindings": [{**new[0], "key": 17}]})
        check("POST bad key -> 400", code == 400 and "can't be bound" in body, body)
        srv.razor_clients = lambda: 1     # a client runs, and a replay-type feed has no sessions
        code, body = http("POST", base + "/api/razor/profile", {"name": "B", "version": "x", "bindings": new})
        check("POST while in-use unknown -> 409 locked", code == 409 and json.loads(body).get("locked") is True, body)
        s = json.loads(http("GET", base + "/api/razor")[1])
        check("summary: every profile locked when in-use is unknown",
              all(p["locked"] for p in s["profiles"]) and s["in_use"]["known"] is False, s["in_use"])
    finally:
        srv.shutdown()
        srv.server_close()
        for p in (d, bk, tmp):
            shutil.rmtree(p, ignore_errors=True)


if __name__ == "__main__":
    for t in (test_replace, test_validate, test_in_use, test_save, test_catalog, test_routes):
        print(t.__name__)
        t()
    print("FAILED: " + ", ".join(FAILURES) if FAILURES else "ALL PASS")
    sys.exit(1 if FAILURES else 0)
