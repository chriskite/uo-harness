"""The Aspect Mastery gump (docs/NOTES.md "Aspects"; wiki Aspect_Mastery).

Saying "[aspect" opens gump 0x907FC735 (live 2026-10-04, Outland Dan). Its top
row shows the character's Arcane Essence "Charges" (each activation costs 5),
then three sections, top to bottom: weapon, spellbook, armor. Each section shows
the aspect selected for it (name, "Tier N", "xp/maxxp", the active tier level),
the aspect's icon in its hue, two arrows that step through the aspects (left,
right), and "Activate" with its button. Activating takes two presses: the first
gets "Click again to confirm." and the same gump again, the second
"<Aspect> aspect armor activated." Activation applies the aspect to the gear
worn in that slot (every armor piece for armor). Gear that leaves the
character's backpack/body (dropped, put in the bank, death, stolen) loses its
aspect; the name drops "<aspect> aspect" and the hue reverts (the shadowhide
chest: 2086 with Harvest, 2406 without).

The flows run over an IO like escape.py's (`send(pkt)` raises on a proxy
refusal; `poll()` -> (state, world events since the last poll)) and a
humanize.Human for the pauses, so ctl (`act aspect`) and the lumber runner (its
suit check before heading out) share them.
"""
import re
import time

import actions

ASPECT_GUMP = 0x907FC735
SECTIONS = ("weapon", "spellbook", "armor")       # top to bottom
CONFIRM_TEXT = "Click again to confirm."
HARVEST_HUE = 2086                # Harvest aspect armor and the menu's Harvest icon (live 2026-10-04)
# The layers of a suit of armor (Outland Dan's six studded pieces, live 2026-10-04): legs, helmet,
# gloves, gorget, chest, arms. Every piece must be worn and aspected for the armor aspect to work.
ARMOR_LAYERS = {0x04: "legs", 0x06: "helmet", 0x07: "gloves", 0x0A: "gorget", 0x0D: "chest", 0x13: "arms"}
WAIT_S = 4.0                      # a press -> the server's answer (the gump comes back in ~50 ms live)
MAX_STEPS = 30                    # arrow presses while looking for an aspect (23 aspects + Chromatic)
_TOKEN = re.compile(r"\{([^}]*)\}")
_TIER = re.compile(r"^Tier (\d+)$")
_XP = re.compile(r"^(\d[\d,]*)/(\d[\d,]*)xp$")


class AspectError(Exception):
    """The menu flow couldn't be done (the message says why)."""


def _num(s: str) -> int:
    return int(s.replace(",", ""))


def parse(layout: str, lines) -> dict:
    """{charges, warn_below, sections: {weapon|spellbook|armor: {aspect, tier, xp,
    xp_max, active_tier, hue, activate, prev, next}}} from the gump. Button ids are
    read from the layout (live: armor Activate 17, arrows 14/15)."""
    lines = list(lines or [])
    texts, buttons, icons = [], [], []
    for t in _TOKEN.findall(layout or ""):
        f = t.split()
        if not f:
            continue
        kind = f[0].lower()
        if kind == "text" and len(f) >= 5 and int(f[4]) < len(lines):
            texts.append((int(f[1]), int(f[2]), lines[int(f[4])].strip()))
        elif kind == "button" and len(f) >= 8:
            buttons.append((int(f[1]), int(f[2]), int(f[7])))
        elif kind == "tilepichue" and len(f) >= 5:
            icons.append((int(f[1]), int(f[2]), int(f[4])))
    out = {"charges": None, "warn_below": None, "sections": {}}
    top = sorted((y, x, s) for x, y, s in texts if y < 60)
    for i, (y, x, s) in enumerate(top):
        nxt = next((t for t in top[i + 1:] if t[2].replace(",", "").isdigit()), None)
        if s == "Charges" and nxt:
            out["charges"] = _num(nxt[2])
        elif s == "Warn When Below" and nxt:
            out["warn_below"] = _num(nxt[2])
    heads = sorted(y for x, y, s in texts if s == "Activate")
    for name, ay in zip(SECTIONS, heads):
        band = [(x, y, s) for x, y, s in texts if ay - 10 <= y <= ay + 40]
        sec = {"aspect": None, "tier": None, "xp": None, "xp_max": None, "active_tier": None, "hue": None,
               "activate": None, "prev": None, "next": None}
        left = sorted((x, y, s) for x, y, s in band if x < 260)
        sec["aspect"] = next((s for x, y, s in left if not _TIER.match(s) and s != "Activate"), None)
        tiers = sorted((x, s) for x, y, s in band if _TIER.match(s))
        if tiers:
            sec["tier"] = int(_TIER.match(tiers[0][1]).group(1))
        if len(tiers) > 1:
            sec["active_tier"] = int(_TIER.match(tiers[-1][1]).group(1))
        xp = next((_XP.match(s) for x, y, s in band if _XP.match(s)), None)
        if xp:
            sec["xp"], sec["xp_max"] = _num(xp.group(1)), _num(xp.group(2))
        ax = next(x for x, y, s in band if s == "Activate")
        act = [(abs(bx - ax), bid) for bx, by, bid in buttons if ay - 10 <= by <= ay + 25 and 0 < ax - bx <= 60]
        sec["activate"] = min(act)[1] if act else None
        arrows = sorted((bx, bid) for bx, by, bid in buttons if ay <= by <= ay + 25 and bx < 150)
        if len(arrows) >= 2:
            sec["prev"], sec["next"] = arrows[0][1], arrows[1][1]
        icon = [hue for ix, iy, hue in icons if ay - 5 <= iy <= ay + 30 and ix < 100]
        sec["hue"] = icon[0] if icon else None
        out["sections"][name] = sec
    return out


def activated_text(text: str) -> bool:
    """The server's line for a finished activation: "Harvest aspect armor activated."
    (armor live; the weapon/spellbook wording not seen yet)."""
    t = (text or "").strip().lower()
    return " aspect " in f" {t}" and t.endswith(" activated.")


def already_text(text: str) -> bool:
    """The server's answer to the confirming press when the gear already has that
    aspect (live 2026-10-04: "Your armor is already of that aspect.", nothing spent)."""
    return (text or "").strip().lower().endswith("is already of that aspect.")


def suit(world: dict, me: int, hue: int = HARVEST_HUE) -> dict:
    """The worn armor as the world model has it, judged by hue (the server re-sends
    each piece with the aspect's hue on activation; item names lag behind):
    {pieces: [{layer, serial, name, hue}], missing: [layer names not worn], plain:
    [layer names worn without the aspect's hue], ok}."""
    worn = {}
    for key, it in (world.get("items") or {}).items():
        c = it.get("container")
        if c is not None and (int(c, 16) if isinstance(c, str) else int(c)) == me and it.get("layer") in ARMOR_LAYERS:
            worn[it["layer"]] = {"layer": ARMOR_LAYERS[it["layer"]], "serial": key, "name": it.get("name"),
                                 "hue": it.get("hue")}
    missing = [name for layer, name in ARMOR_LAYERS.items() if layer not in worn]
    plain = [p["layer"] for p in worn.values() if p["hue"] != hue]
    return {"pieces": sorted(worn.values(), key=lambda p: p["layer"]), "missing": missing, "plain": plain,
            "ok": not missing and not plain}


class _Menu:
    """One visit to the menu over the IO: what the server sent, the open gump."""

    def __init__(self, io, human):
        self.io, self.human, self.texts, self.g = io, human, [], None

    def send(self, pkt, until) -> list:
        """Send (the IO raises its own error on a proxy refusal), then collect what the
        server answers until `until(events)` or WAIT_S."""
        self.io.poll()
        self.io.send(pkt)
        end, got = time.monotonic() + WAIT_S, []
        while True:
            _, new = self.io.poll()
            got += new
            if until(got) or time.monotonic() > end:
                break
            time.sleep(0.05)
        self.texts += [e.get("text") for e in got if e.get("ev") == "speech_heard" and e.get("text")]
        g = next((e for e in reversed(got) if e.get("ev") == "gump_open"
                  and _id(e.get("gump_id")) == ASPECT_GUMP), None)
        if g is not None:
            self.g = g
        return got

    def menu(self) -> dict:
        return parse(self.g.get("layout"), self.g.get("lines"))

    def open(self) -> dict:
        self.human.wait("speak")
        self.send(actions.say_unicode("[aspect"), _has_gump)
        if self.g is None:
            raise AspectError("the Aspect Mastery menu didn't open after [aspect")
        return self.menu()

    def press(self, button: int, until=None) -> list:
        g, self.g = self.g, None
        self.human.wait("menu")
        got = self.send(actions.gump_reply(_id(g["serial"]), ASPECT_GUMP, button, g.get("layout") or "",
                                           g.get("lines") or []), until or _has_gump)
        return got

    def close(self):
        if self.g is not None:
            self.press(0, until=lambda evs: True)


def _id(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def _has_gump(evs) -> bool:
    return any(e.get("ev") == "gump_open" and _id(e.get("gump_id")) == ASPECT_GUMP for e in evs)


def read(io, human) -> dict:
    """Open the menu, read it (parse), close it."""
    m = _Menu(io, human)
    try:
        return m.open()
    finally:
        m.close()


def activate(io, section: str, human, want: str | None = None) -> dict:
    """Apply the `section`'s aspect (stepped to `want` first, by its right arrow) to
    the worn gear, like a player: Activate, "Click again to confirm.", Activate.
    {ok, already, aspect, tier, charges_before, charges, texts, error?}; the menu is
    closed afterwards. AspectError when the menu doesn't open or the aspect isn't
    among the section's aspects."""
    m = _Menu(io, human)
    try:
        menu = m.open()
        before = menu["charges"]
        for _ in range(MAX_STEPS):
            sec = menu["sections"].get(section) or {}
            if want is None or (sec.get("aspect") or "").lower() == want.lower():
                break
            if sec.get("next") is None:
                raise AspectError(f"the {section} section has no aspect arrows")
            m.press(sec["next"])
            if m.g is None:
                raise AspectError(f"the menu didn't come back after stepping the {section} aspects")
            menu = m.menu()
        else:
            raise AspectError(f"no aspect {want!r} among the {section} section's aspects")
        sec = menu["sections"][section]
        if sec.get("activate") is None:
            raise AspectError(f"the {section} section shows no Activate ({sec.get('aspect')} not unlocked?)")
        m.press(sec["activate"], until=lambda evs: _has_gump(evs) and any(
            e.get("text") == CONFIRM_TEXT for e in evs))
        out = {"aspect": sec["aspect"], "tier": sec["tier"], "charges_before": before, "warn_below": menu["warn_below"]}
        if CONFIRM_TEXT not in m.texts or m.g is None:
            return {**out, "ok": False, "already": False, "charges": before, "texts": m.texts,
                    "error": f"no '{CONFIRM_TEXT}' after Activate"}
        m.press(m.menu()["sections"][section]["activate"], until=lambda evs: _has_gump(evs) and any(
            e.get("ev") == "speech_heard" and e.get("text") != CONFIRM_TEXT for e in evs))
        done = next((t for t in m.texts if activated_text(t)), None)
        already = next((t for t in m.texts if already_text(t)), None)
        charges = m.menu()["charges"] if m.g is not None else None
        res = {**out, "ok": bool(done or already), "already": already is not None, "charges": charges,
               "texts": m.texts}
        if not res["ok"]:
            res["error"] = f"not activated: {[t for t in m.texts if t != CONFIRM_TEXT]}"
        return res
    finally:
        m.close()
