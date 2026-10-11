"""Razor (the Outlands Razor CE fork) profile hotkeys: read, catalog, lock state, save.

Backs the viz Razor page (/api/razor*). Facts this relies on (docs/NOTES.md "Razor profiles and hotkeys"):
- Profiles/<name>.xml is UTF-8 with a BOM, CRLF, tab-indented. Hotkeys live in one
  <hotkeys> block of <key mod= key= send=>NAME</key> lines (or <hotkeys />).
  mod: Alt=1, Ctrl=2, Shift=4. key: a Win32 VK code, or -1..-5 for the mouse.
  NAME is `L:<id>` (a Razor_lang id or client cliloc) or a plain string.
- Profiles also hold <passwords>: raw profile XML is never returned or logged.
- Razor reads a profile when it loads it and rewrites it from memory on logout,
  client exit and profile switch, so a profile in use must not be edited
  (in_use/lock_reason). save() rewrites only the <hotkeys> block, bytes outside
  it stay identical, and backs the old file up first.

Writing the install dir is a user-approved exception to AGENTS.md Constraint 1
covering Profiles/*.xml only (grant_razor_profiles.ps1 gives write access).
"""
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from xml.sax.saxutils import escape

import uomap
from uo import cliloc

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSISTANT_DIR = os.path.join(uomap.INSTALL, "ClassicUO", "Data", "Plugins", "Assistant")
CATALOG_PATH = os.path.join(HERE, "data", "razor_hotkeys.json")
BACKUP_DIR = os.path.join(ROOT, "logs", "razor_backups")

MOD_ALT, MOD_CTRL, MOD_SHIFT = 1, 2, 4
MOUSE_KEYS = {-1: "Wheel Up", -2: "Wheel Down", -3: "Middle Button", -4: "X Button 1", -5: "X Button 2"}
# Shift/Ctrl/Alt, the Win keys, Menu and the L/R modifier keys.
UNBINDABLE = frozenset({16, 17, 18, 91, 92, 93, 160, 161, 162, 163, 164, 165})
MAX_BINDINGS = 1000
_SAVE_LOCK = threading.Lock()
_BOM = b"\xef\xbb\xbf"
_HOTKEYS_RE = re.compile(r"<hotkeys\s*/>|<hotkeys>.*?</hotkeys>", re.S)

# Category lang ids in Razor's Hot Keys tree order (root = 0).
CAT_ITEMS, CAT_TARGETS, CAT_AGENTS, CAT_DRESS, CAT_MACROS = 1210, 1211, 1212, 1213, 1214
CAT_SPELLS, CAT_SKILLS, CAT_MISC, CAT_FRIENDS, CAT_SCRIPTS, CAT_OUTLANDS = (
    1215, 1216, 1217, 1218, 1219, 1220)
CATEGORIES = (0, CAT_ITEMS, CAT_TARGETS, CAT_AGENTS, CAT_DRESS, CAT_MACROS, CAT_SPELLS,
              CAT_SKILLS, CAT_MISC, CAT_FRIENDS, CAT_SCRIPTS, CAT_OUTLANDS)
# Magery: L:<base + (circle - 1) * 8 + number>; spells.def numbers spells 1-8 within a circle
# (upstream Spell.GetID), so the ids run 3002011 (Clumsy) .. 3002074.
SPELL_NAME_BASE = 3002010
SKILL_NAME_BASE = 1044060   # L:<base + skill index>
CIRCLE_LANG_BASE = 1250     # lang[1250 + circle] = "1st".."8th"
SUBMENU_LANG_BASE = 1249    # lang[1249 + SubMenu] = Outlands command subcategory


@dataclass(frozen=True)
class Binding:
    name: str
    mod: int
    key: int
    send: bool

    def to_json(self) -> dict:
        return {"name": self.name, "mod": self.mod, "key": self.key, "send": self.send}


class Stale(Exception):
    """The profile changed on disk since the client loaded it."""


class UnknownProfile(KeyError):
    pass


_LANG_CACHE: dict = {}


def lang(assistant_dir) -> dict[int, str]:
    """{id: text} from Language/Razor_lang.enu (cached per path and mtime)."""
    path = os.path.join(assistant_dir, "Language", "Razor_lang.enu")
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        return {}
    hit = _LANG_CACHE.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    with open(path, "rb") as f:
        text = f.read().decode("utf-8", errors="replace")
    table = {}
    for line in text.split("\n"):
        m = re.match(r"^(\d+)=(.*)$", line.rstrip("\r"))
        if m:
            table[int(m.group(1))] = m.group(2)
    _LANG_CACHE[path] = (mtime, table)
    return table


def chars(assistant_dir) -> dict[int, str]:
    """{character serial: profile name} from Profiles/chars.lst."""
    try:
        with open(os.path.join(assistant_dir, "Profiles", "chars.lst"), "rb") as f:
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return {}
    out = {}
    for line in text.splitlines():
        serial, sep, name = line.strip().partition("=")
        if not sep or not name:
            continue
        try:
            out[int(serial, 16)] = name
        except ValueError:
            continue
    return out


def profile_names(assistant_dir) -> list[str]:
    try:
        files = os.listdir(os.path.join(assistant_dir, "Profiles"))
    except OSError:
        return []
    return sorted((f[:-4] for f in files if f.lower().endswith(".xml")), key=str.lower)


def profile_path(assistant_dir, name) -> str:
    """Path of an existing profile; a client-supplied name only becomes a path here."""
    if name not in profile_names(assistant_dir):
        raise UnknownProfile(name)
    return os.path.join(assistant_dir, "Profiles", name + ".xml")


def version(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_bindings(data: bytes) -> list[Binding]:
    """The <hotkeys> of profile bytes, in file order. Raises ValueError on bad XML."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ValueError(str(e)) from None
    hk = root.find("hotkeys")
    if hk is None:
        return []
    out = []
    for el in hk.findall("key"):
        name = (el.text or "").strip()
        if not name and el.get("loc"):
            name = f"L:{el.get('loc')}"
        if not name:
            name = el.get("name") or ""
        try:
            mod, key = int(el.get("mod", "")), int(el.get("key", ""))
        except ValueError:
            continue
        if not name:
            continue
        out.append(Binding(name, mod, key, (el.get("send") or "").lower() == "true"))
    return out


def render_hotkeys(bindings, indent: str, nl: str) -> str:
    """The <hotkeys> element text, starting at `<hotkeys` (its first-line indent is the file's)."""
    if not bindings:
        return "<hotkeys />"
    lines = [f'{indent}\t<key mod="{b.mod}" key="{b.key}" send="{"True" if b.send else "False"}">'
             f'{escape(b.name)}</key>' for b in bindings]
    return "<hotkeys>" + nl + nl.join(lines) + nl + indent + "</hotkeys>"


def replace_hotkeys(data: bytes, bindings) -> bytes:
    """Profile bytes with the <hotkeys> block replaced; every other byte unchanged."""
    bom = data.startswith(_BOM)
    text = data[len(_BOM):].decode("utf-8") if bom else data.decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    matches = list(_HOTKEYS_RE.finditer(text))
    if len(matches) != 1:
        raise ValueError(f"profile has {len(matches)} <hotkeys> elements")
    m = matches[0]
    line_start = text.rfind("\n", 0, m.start()) + 1
    prefix = text[line_start:m.start()]
    indent = prefix if prefix.strip() == "" else ""
    out = text[:m.start()] + render_hotkeys(bindings, indent, nl) + text[m.end():]
    return (_BOM if bom else b"") + out.encode("utf-8")


_VK_NAMES = {8: "Backspace", 9: "Tab", 13: "Enter", 19: "Pause", 20: "Caps Lock", 27: "Esc",
             32: "Space", 33: "PgUp", 34: "PgDn", 35: "End", 36: "Home", 37: "Left", 38: "Up",
             39: "Right", 40: "Down", 45: "Ins", 46: "Del"}


def key_label(key: int) -> str:
    if key in MOUSE_KEYS:
        return MOUSE_KEYS[key]
    if 65 <= key <= 90 or 48 <= key <= 57:
        return chr(key)
    if 112 <= key <= 135:
        return f"F{key - 111}"
    if 96 <= key <= 105:
        return f"Num {key - 96}"
    return _VK_NAMES.get(key, f"VK {key}")


def combo_label(mod: int, key: int) -> str:
    parts = [n for f, n in ((MOD_CTRL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift")) if mod & f]
    return "+".join(parts + [key_label(key)])


def validate(raw) -> list[Binding]:
    """Client-supplied bindings → Bindings. Raises ValueError naming the index and field."""
    if not isinstance(raw, list):
        raise ValueError("bindings must be a list")
    if len(raw) > MAX_BINDINGS:
        raise ValueError(f"bindings: {len(raw)} entries (max {MAX_BINDINGS})")
    out, names, combos = [], {}, {}
    for i, b in enumerate(raw):
        where = f"bindings[{i}]"
        if not isinstance(b, dict):
            raise ValueError(f"{where}: not an object")
        name, mod, key, send = b.get("name"), b.get("mod"), b.get("key"), b.get("send")
        if (not isinstance(name, str) or not 1 <= len(name) <= 200 or name != name.strip()
                or any(ord(c) < 0x20 for c in name)):
            raise ValueError(f"{where}.name: {name!r} is not a valid action name")
        if not isinstance(mod, int) or isinstance(mod, bool) or not 0 <= mod <= 7:
            raise ValueError(f"{where}.mod: {mod!r} is not a modifier set (0-7)")
        if not isinstance(key, int) or isinstance(key, bool) or not (-5 <= key <= -1 or 1 <= key <= 254):
            raise ValueError(f"{where}.key: {key!r} is not a key code")
        if key in UNBINDABLE:
            raise ValueError(f"{where}.key: {key} can't be bound")
        if not isinstance(send, bool):
            raise ValueError(f"{where}.send: {send!r} is not true/false")
        if name in names:
            raise ValueError(f"{where}.name: {name!r} is bound twice")
        if (mod, key) in combos:
            raise ValueError(f"{where}: {combo_label(mod, key)} duplicates bindings[{combos[(mod, key)]}]")
        names[name] = i
        combos[(mod, key)] = i
        out.append(Binding(name, mod, key, False if key < 0 else send))
    return out


def bound_names(assistant_dir) -> set[str]:
    """NAMEs bound in any profile (proves an inferred action is registered)."""
    out = set()
    for name in profile_names(assistant_dir):
        try:
            with open(os.path.join(assistant_dir, "Profiles", name + ".xml"), "rb") as f:
                out.update(b.name for b in parse_bindings(f.read()))
        except (OSError, ValueError):
            continue
    return out


def action_skills() -> list[tuple[int, str]]:
    """(skill index, label) of the skills Razor offers a Use hotkey for (action flag set)."""
    try:
        idx = uomap.action_skills()
        names = uomap.skill_names()
        table = cliloc.load()
    except OSError:
        return []
    return [(i, table.get(SKILL_NAME_BASE + i) or names[i]) for i in idx]


def _rel_files(folder, ext) -> list[str]:
    """`sub\\name` (no extension) of every *ext file under folder, case-insensitively sorted."""
    out = []
    for dirpath, _dirs, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(ext):
                rel = os.path.relpath(os.path.join(dirpath, f[:-len(ext)]), folder)
                out.append(rel.replace("/", "\\"))
    return sorted(out, key=str.lower)


def _def_rows(path):
    """Non-comment `|`-separated rows of a Razor .def file ([] when missing)."""
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append([c.strip() for c in line.split("|")])
    return rows


def _agent_max(agents, prefix, default):
    ns = []
    if agents is not None:
        for el in agents:
            if el.tag.startswith(prefix):
                try:
                    ns.append(int(el.tag[len(prefix):]))
                except ValueError:
                    pass
    return max(ns) if ns else default


def catalog(assistant_dir, root, skills, bound: set[str]) -> list[dict]:
    """Ordered actions [{name, label, path, confirmed}] for one profile (root = its XML root)."""
    tr = lang(assistant_dir)
    buckets: dict[int, list[dict]] = {c: [] for c in CATEGORIES}

    def cat_path(cat):
        return [] if cat == 0 else [tr.get(cat, f"#{cat}")]

    def add(cat, name, label, path=None, confirmed=True):
        buckets.setdefault(cat, []).append({"name": name, "label": label,
                                            "path": cat_path(cat) if path is None else path,
                                            "confirmed": confirmed})

    with open(CATALOG_PATH, encoding="utf-8") as f:
        groups = json.load(f)["groups"]
    for g in groups:
        cat, sub = g["cat"], g["sub"]
        path = cat_path(cat) + ([tr.get(sub, f"#{sub}")] if sub else [])
        for item in g["items"]:
            if isinstance(item, int):
                if item not in tr:
                    continue
                name, label = f"L:{item}", tr[item]
            else:
                name = label = item
            add(cat, name, label, path, not g.get("inferred") or name in bound)

    agents = root.find("agents") if root is not None else None
    for n in range(1, _agent_max(agents, "Organizer-", 20) + 1):
        add(CAT_AGENTS, f"Organizer Agent-{n:02d}", f"Organizer Agent-{n:02d}")
        add(CAT_AGENTS, f"Set Organizer HotBag-{n:02d}", f"Set Organizer HotBag-{n:02d}")
    for n in range(1, _agent_max(agents, "Restock-", 10) + 1):
        add(CAT_AGENTS, f"Restock Agent-{n:02d}", f"Restock Agent-{n:02d}")
        add(CAT_AGENTS, f"Set Restock HotBag-{n:02d}", f"Set Restock HotBag-{n:02d}")

    if root is not None:
        for el in root.findall("dresslists/list"):
            x = el.get("name")
            if x:
                for verb in ("Dress", "Undress", "Toggle"):
                    add(CAT_DRESS, f"{verb}: {x}", f"{verb}: {x}")

    for rel in _rel_files(os.path.join(assistant_dir, "Macros"), ".macro"):
        add(CAT_MACROS, f"Play: {rel}", f"Play: {rel}")

    for row in _def_rows(os.path.join(assistant_dir, "spells.def")):
        try:
            no, circle, label = int(row[1]), int(row[2]), row[3]
        except (IndexError, ValueError):
            continue
        if 1 <= circle <= 8:
            add(CAT_SPELLS, f"L:{SPELL_NAME_BASE + (circle - 1) * 8 + no}", label,
                cat_path(CAT_SPELLS) + [tr.get(CIRCLE_LANG_BASE + circle, f"Circle {circle}")])

    for i, label in skills:
        add(CAT_SKILLS, f"L:{SKILL_NAME_BASE + i}", label)

    if root is not None:
        friends = root.find("friends")
        for el in (friends if friends is not None else []):
            x = el.get("name")
            if x:
                for verb in ("Add Target To", "Toggle Group", "Add All Mobiles", "Add All Humanoids"):
                    name = f"{verb}: {x}"
                    add(CAT_FRIENDS, name, name, confirmed=name in bound)

    for rel in _rel_files(os.path.join(assistant_dir, "Scripts"), ".razor"):
        add(CAT_SCRIPTS, f"Play Script: {rel}", f"Play Script: {rel}")

    for row in _def_rows(os.path.join(assistant_dir, "outlandscommands.def")):
        if not row or not row[0].startswith("["):
            continue
        try:
            sub, lid = int(row[1]), int(row[2])
        except (IndexError, ValueError):
            continue
        if lid in tr:
            sublabel = tr.get(SUBMENU_LANG_BASE + sub, f"#{SUBMENU_LANG_BASE + sub}")
            add(CAT_OUTLANDS, f"L:{lid}", tr[lid], cat_path(CAT_OUTLANDS) + [sublabel])

    out, seen = [], set()
    for cat in buckets:
        for a in buckets[cat]:
            if a["name"] not in seen:
                seen.add(a["name"])
                out.append(a)
    return out


def client_count() -> int | None:
    """Running ClassicUO.exe processes, or None when tasklist can't be run."""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ClassicUO.exe", "/NH", "/FO", "CSV"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return sum(1 for line in out.splitlines() if line.startswith('"ClassicUO.exe"'))


@dataclass
class InUse:
    known: bool
    by: dict[str, list[str]]   # profile name -> character names using it
    reason: str


def in_use(char_map: dict[int, str], clients: int | None, sessions: list[dict] | None) -> InUse:
    """Which profiles Razor has loaded; unknown (every profile locks) when it can't be told."""
    if clients is None:
        return InUse(False, {}, "can't list running game clients")
    if clients == 0:
        return InUse(True, {}, "no game client running")
    if sessions is None:
        return InUse(False, {}, "a game client is running but the proxy's characters are unavailable")
    if len(sessions) < clients:
        return InUse(False, {}, f"{clients} game client(s) running but {len(sessions)} "
                                f"character(s) logged in through the proxy")
    by: dict[str, list[str]] = {}
    names = []
    for s in sessions:
        name, serial = s.get("name"), s.get("serial")
        profile = None
        if serial is not None:
            try:
                profile = char_map.get(int(serial, 16))
            except (TypeError, ValueError):
                profile = None
        if profile is None:
            return InUse(False, {}, f"{name or 'a character'} has no profile in chars.lst")
        label = name or serial
        by.setdefault(profile, []).append(label)
        names.append(label)
    return InUse(True, by, "in use by " + ", ".join(names))


def lock_reason(u: InUse, profile: str) -> str | None:
    if not u.known:
        return u.reason
    if profile in u.by:
        return (f"in use by {', '.join(u.by[profile])}: Razor rewrites this profile when that "
                f"character logs out or the client closes")
    return None


def probe_writable(assistant_dir) -> str | None:
    """None when the Profiles folder is writable, else the error."""
    p = os.path.join(assistant_dir, "Profiles", ".uoh-write-probe")
    try:
        open(p, "wb").close()
        os.remove(p)
    except OSError as e:
        return f"{type(e).__name__}: {e}"
    return None


def save(assistant_dir, name, expected_version, bindings, backup_dir, now=time.time) -> dict:
    """Rewrite profile `name`'s hotkeys if it still has `expected_version`; back up the old file."""
    with _SAVE_LOCK:
        path = profile_path(assistant_dir, name)
        with open(path, "rb") as f:
            data = f.read()
        if version(data) != expected_version:
            raise Stale(name)
        new = replace_hotkeys(data, bindings)
        if new == data:
            return {"version": version(data), "backup": None}
        os.makedirs(backup_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(now()))
        backup = os.path.join(backup_dir, f"{name}_{stamp}.xml")
        n = 2
        while os.path.exists(backup):
            backup = os.path.join(backup_dir, f"{name}_{stamp}_{n}.xml")
            n += 1
        with open(backup, "xb") as f:
            f.write(data)
        tmp = path + ".uoh-tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(new)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        try:
            rel = os.path.relpath(backup, ROOT).replace("\\", "/")
        except ValueError:   # another drive
            rel = backup.replace("\\", "/")
        return {"version": version(new), "backup": rel}
