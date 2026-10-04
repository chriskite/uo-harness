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
"""
import re

ASPECT_GUMP = 0x907FC735
SECTIONS = ("weapon", "spellbook", "armor")       # top to bottom
CONFIRM_TEXT = "Click again to confirm."
_TOKEN = re.compile(r"\{([^}]*)\}")
_TIER = re.compile(r"^Tier (\d+)$")
_XP = re.compile(r"^(\d[\d,]*)/(\d[\d,]*)xp$")


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
