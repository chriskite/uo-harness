"""Combat rules and stock packet sequences shared by `ctl act` and the hunt runner.

User decision 2026-09-30 (ANTICHEAT.md §8.17): hostile monsters may be fought
and looted; players, their pets and NPCs never. Everything here is pure: the
callers send the packets (ctl through its control socket, runners through
agent_link.Link) and do their own waiting.

- attackable(): the monsters-only rule (threats.identify + notoriety 3-6) for
  attack and target.
- attack_packets(): the stock double-click on a mobile in war mode
  (GameActions.DoubleClick → RequestMobileStatus + Attack): 0x34 unless the
  client has an outstanding status request for that mob, then 0x05
  (ANTICHEAT.md §10 A7).
- target_mobile() / target_self(): 0x6C answers to a spell's cursor.
- human_corpse(), loot_order(), grab_packets(): the loot rule and the stock
  GrabItem shape (0x07 lift, 0x08 drop into the open backpack).
"""
import actions
import threats

# RunUO Notoriety constants (Innocent 1 .. Invulnerable 7) [INFERENCE: not in the
# local ClassicUO tree; the client only switches on the named enum].
NOTORIETY = {1: "innocent", 2: "ally", 3: "attackable", 4: "criminal", 5: "enemy",
             6: "murderer", 7: "invulnerable"}
# Notoriety 3-6 is attackable without a criminal flag; 1 (innocent: players' pets) and
# 2 (ally) are criminal to attack, 7 is invulnerable.
ATTACKABLE_NOTORIETY = frozenset([3, 4, 5, 6])
ATTACK_MIN_HP = 0.3                  # refuse to start a fight below this share of max hits
VIEW_RANGE = 18                      # ClassicUO ClientViewRange: the client drops objects beyond it
CORPSE_GRAPHIC = 0x2006
LOOT_RANGE = 2                       # tiles; the server's own limit is similar [INFERENCE]
GOLD_GRAPHIC = 0x0EED
DROP_AUTO = 0x7FFFFFFF               # drop-into-container auto position (demo capture 204225)
# ClassicUO Game/Data/SpellsMagery.cs (ids 1-64); the Outlands client casts with 0xFF sub 4
# (observed live, session 20260928_164548: ids 5 and 15)
MAGERY_SPELLS = (
    "Clumsy", "Create Food", "Feeblemind", "Heal", "Magic Arrow", "Night Sight", "Reactive Armor", "Weaken",
    "Agility", "Cunning", "Cure", "Harm", "Magic Trap", "Magic Untrap", "Protection", "Strength",
    "Bless", "Fireball", "Magic Lock", "Poison", "Telekinesis", "Teleport", "Unlock", "Wall of Stone",
    "Arch Cure", "Arch Protection", "Curse", "Fire Field", "Greater Heal", "Lightning", "Mana Drain", "Recall",
    "Blade Spirits", "Dispel Field", "Incognito", "Magic Reflection", "Mind Blast", "Paralyze", "Poison Field",
    "Summon Creature", "Dispel", "Energy Bolt", "Explosion", "Invisibility", "Mark", "Mass Curse",
    "Paralyze Field", "Reveal", "Chain Lightning", "Energy Field", "Flamestrike", "Gate Travel", "Mana Vampire",
    "Mass Dispel", "Meteor Swarm", "Polymorph", "Earthquake", "Energy Vortex", "Resurrection", "Air Elemental",
    "Summon Daemon", "Earth Elemental", "Fire Elemental", "Water Elemental")
# Mana per Magery circle (circle = (id - 1) // 8 + 1): wiki Magery, circles 1-7 read 2026-10-02
# (4/6/9/11/14/20/40); the 8th circle's 50 is RunUO's MagerySpell.m_ManaTable [INFERENCE for Outlands]
CIRCLE_MANA = (4, 6, 9, 11, 14, 20, 40, 50)
LAYER_BACKPACK = 0x15


def _serial(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def backpack(items: dict, me) -> int | None:
    """Serial of the backpack worn by `me` (layer 0x15), or None."""
    return next((_serial(k) for k, v in items.items() if v.get("layer") == LAYER_BACKPACK
                 and v.get("container") is not None and _serial(v["container"]) == me), None)


def pack_items(items: dict, pack: int):
    """(serial key, item) for everything in the backpack, any bag depth."""
    parent = {_serial(k): (_serial(it["container"]) if it.get("container") else None) for k, it in items.items()}
    for k, it in items.items():
        c, depth = parent[_serial(k)], 0
        while c is not None and c != pack and depth < 8:
            c, depth = parent.get(c), depth + 1
        if c == pack and it.get("graphic") is not None:
            yield k, it


def key_of(serial: int) -> str:
    return f"0x{serial:08X}"


def spell_id(text: str) -> int | None:
    """A Magery spell by number (1-64) or name (case, spacing and _ ignored), else None."""
    t = text.strip()
    if t.isdigit() and 1 <= int(t) <= len(MAGERY_SPELLS):
        return int(t)
    key = "".join(t.lower().split()).replace("_", "")
    for i, name in enumerate(MAGERY_SPELLS, 1):
        if "".join(name.lower().split()) == key:
            return i
    return None


def spell_mana(sid: int) -> int:
    return CIRCLE_MANA[(sid - 1) // 8]


def attackable(world: dict, key: str) -> tuple[bool, str]:
    """(ok, why not): the monsters-only rule shared by attack and target."""
    mob = world["mobiles"].get(key)
    if mob is None or mob.get("x") is None:
        return False, f"mobile {key} not known to the world model"
    label = (world.get("labels") or {}).get(key)
    kind, player, evidence = threats.identify(mob, label)
    if kind != "monster" or player:
        return False, (f"{key} ({label or mob.get('name')}) is not a hostile monster ({kind}; "
                       f"{', '.join(evidence)}): only monsters, never players or NPCs")
    noto = mob.get("notoriety")
    if noto not in ATTACKABLE_NOTORIETY:
        return False, (f"{key} has notoriety {noto} ({NOTORIETY.get(noto, '?')}): likely someone's pet or a "
                       f"protected creature")
    return True, ""


def attack_packets(world: dict, serial: int) -> list[bytes]:
    """The stock attack on `serial` once war mode is on: 0x34 unless the client
    has an outstanding status request for it (world.status_requested: ClassicUO
    RequestMobileStatus sends it while HitsRequest < Received), then 0x05."""
    pre = [] if key_of(serial) in (world.get("status_requested") or []) else [actions.status_request(serial)]
    return pre + [actions.attack(serial)]


def target_mobile(cursor: dict, serial: int, mob: dict) -> bytes:
    """0x6C answering the cursor that is up with a mobile (its tile and body)."""
    return actions.target_object(cursor["cursor_id"], serial, mob["x"], mob["y"], mob.get("z") or 0,
                                 mob.get("graphic") or 0, cursor.get("cursor_type") or 0)


def target_self(cursor: dict, self_serial: int, pos, body) -> bytes:
    """0x6C answering the cursor that is up with yourself."""
    return actions.target_object(cursor["cursor_id"], self_serial, pos[0], pos[1], pos[2],
                                 body or 0, cursor.get("cursor_type") or 0)


def human_corpse(corpse: dict) -> bool:
    """A corpse with a human body (a player, a human NPC, your own): never looted
    (criminal, and policy: no corpse runs). A corpse's amount is the body it was."""
    return corpse.get("amount") in threats.HUMAN_BODIES or "remains of" in (corpse.get("name") or "").lower()


def corpse_contents(world: dict, corpse: int) -> dict:
    return {k: v for k, v in world["items"].items()
            if v.get("container") is not None and _serial(v["container"]) == corpse}


def loot_order(inside: dict) -> list:
    """(key, item) pairs in the order they are taken: gold first, then by serial."""
    return sorted(inside.items(), key=lambda kv: (kv[1].get("graphic") != GOLD_GRAPHIC, kv[0]))


def grab_packets(serial: int, amount: int, pack: int) -> list[bytes]:
    """The stock GrabItem (GameActions.cs:819-852): lift, then drop into the open
    backpack at the auto position, sent back to back."""
    return [actions.lift(serial, amount), actions.drop(serial, DROP_AUTO, DROP_AUTO, 0, 0, pack)]
