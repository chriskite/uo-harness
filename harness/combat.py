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
- wear_layer() / equip_packets(): what `ctl act equip` and the hunt runner send to
  put an item from the backpack on: the stock drag to the paperdoll, 0x07 lift,
  (the caller's human drag pause), 0x13 equip request on the item's layer.
- can_cast() / missing_reagents(): whether a Magery spell can be paid for: its
  mana and one of each of its reagents (SPELL_REAGENTS, ClassicUO
  SpellsMagery.cs; graphics checked against the client's tiledata names) in the
  backpack at any bag depth, or a spellstone instead of reagents (an item named
  "...spellstone..." or "...bauble...": Hackworth's "arielle's bauble",
  docs/NOTES.md; the Mage starting kit's 2250-charge stone). Without either the
  server answers a cast with cliloc 502630 "More reagents are needed for this
  spell." Whether the spellbook holds the spell isn't known to the world model:
  the server says so.
- human_corpse(), loot_order(), grab_packets(): the loot rule and the stock
  GrabItem shape (0x07 lift, 0x08 drop into the open backpack).
"""
import re

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
LAYER_ONE_HANDED, LAYER_TWO_HANDED = 0x01, 0x02
# Items whose tiledata layer is 0 although the server wears them on a layer (evidence):
# 31038 (0x793E) "prismatic staff", Outlands' arcane staff: artdata.uoo layer 0, flags
# Wearable; the server's 0x2E put Shackleworth's 0x57064E05 on layer 2 (two-handed),
# capture 20261003_113952 11:40:35 and again after the 11:55:48 equip
KNOWN_LAYERS = {31038: LAYER_TWO_HANDED}
# Reagent graphics (tiledata names: "Black Pearl%s%", "Blood Moss", "Garlic", "Ginseng",
# "Mandrake Root%s%", "Nightshade", "Sulfurous Ash", "Spider's Silk")
REAGENTS = {0x0F7A: "black pearl", 0x0F7B: "blood moss", 0x0F84: "garlic", 0x0F85: "ginseng",
            0x0F86: "mandrake root", 0x0F88: "nightshade", 0x0F8C: "sulfurous ash", 0x0F8D: "spiders' silk"}
_BP, _BM, _GA, _GI, _MR, _NS, _SA, _SS = REAGENTS
# One of each per cast, by spell id (ClassicUO Game/Data/SpellsMagery.cs, the stock UO reagents)
SPELL_REAGENTS = {
    1: (_BM, _NS), 2: (_GA, _GI, _MR), 3: (_NS, _GI), 4: (_GA, _GI, _SS), 5: (_SA,), 6: (_SS, _SA),
    7: (_GA, _SS, _SA), 8: (_GA, _NS),
    9: (_BM, _MR), 10: (_NS, _MR), 11: (_GA, _GI), 12: (_NS, _SS), 13: (_GA, _SS, _SA), 14: (_BM, _SA),
    15: (_GA, _GI, _SA), 16: (_MR, _NS),
    17: (_GA, _MR), 18: (_BP,), 19: (_BM, _GA, _SA), 20: (_NS,), 21: (_BM, _MR), 22: (_BM, _MR),
    23: (_BM, _SA), 24: (_BM, _GA),
    25: (_GA, _GI, _MR), 26: (_GA, _GI, _MR, _SA), 27: (_GA, _NS, _SA), 28: (_BP, _SS, _SA),
    29: (_GA, _GI, _MR, _SS), 30: (_MR, _SA), 31: (_BP, _MR, _SS), 32: (_BP, _BM, _MR),
    33: (_BP, _MR, _NS), 34: (_BP, _GA, _SS, _SA), 35: (_BM, _GA, _NS), 36: (_GA, _MR, _SS),
    37: (_BP, _MR, _NS, _SA), 38: (_GA, _MR, _SS), 39: (_BP, _NS, _SS), 40: (_BM, _MR, _SS),
    41: (_GA, _MR, _SA), 42: (_BP, _NS), 43: (_BM, _MR), 44: (_BM, _NS), 45: (_BP, _BM, _MR),
    46: (_GA, _MR, _NS, _SA), 47: (_BP, _GI, _SS), 48: (_BM, _SA),
    49: (_BP, _BM, _MR, _SA), 50: (_BP, _MR, _SS, _SA), 51: (_SS, _SA), 52: (_BP, _MR, _SA),
    53: (_BP, _BM, _MR, _SS), 54: (_BP, _GA, _MR, _SA), 55: (_BM, _MR, _SS, _SA), 56: (_BM, _MR, _SS),
    57: (_BM, _GI, _MR, _SA), 58: (_BP, _BM, _MR, _NS), 59: (_BM, _GI, _GA), 60: (_BM, _MR, _SS),
    61: (_BM, _MR, _SS, _SA), 62: (_BM, _MR, _SS), 63: (_BM, _MR, _SS, _SA), 64: (_BM, _MR, _SS),
}
SPELLSTONE_WORDS = ("spellstone", "bauble")
CLILOC_NO_REAGENTS = 502630          # "More reagents are needed for this spell."
TEXT_NO_REAGENTS = "More reagents are needed for this spell."


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


def tile_layer(graphic) -> int | None:
    """Tiledata layer of an item graphic (install dir, read-only), or None."""
    if graphic is None:
        return None
    try:
        import uomap
        it = uomap.tiledata().item(graphic)
    except (OSError, ValueError):
        return None
    return it.layer if it else None


def wear_layer(world: dict, serial: int) -> tuple[int | None, str]:
    """(layer, source) the item is worn on: its tiledata layer; else the layer the
    server last wore it on for us (world.worn_layers, from 0x2E/0x78, kept after it
    went to the pack); else KNOWN_LAYERS for its graphic. (None, why) otherwise."""
    key = key_of(serial)
    graphic = (world.get("items", {}).get(key) or {}).get("graphic")
    layer = tile_layer(graphic)
    if layer:
        return layer, "tiledata"
    layer = (world.get("worn_layers") or {}).get(key)
    if layer:
        return layer, "worn before (server 0x2E/0x78)"
    if graphic in KNOWN_LAYERS:
        return KNOWN_LAYERS[graphic], f"known for graphic {graphic}"
    return None, f"graphic {graphic} has no wearable layer in tiledata and was never seen worn"


def equip_packets(world: dict, me: int, serial: int) -> tuple[bytes, bytes]:
    """(lift, equip request) putting `serial` from the backpack (any bag depth) on
    `me`, as a player drags it onto the paperdoll; ValueError when it can't be."""
    items = world["items"]
    key = key_of(serial)
    it = items.get(key)
    if it is None:
        raise ValueError(f"item {key} not known to the world model")
    pack = backpack(items, me)
    if pack is None:
        raise ValueError("backpack not known to the world model")
    if it.get("container") is not None and _serial(it["container"]) == me and it.get("layer"):
        raise ValueError(f"{key} is already worn")
    if not any(k == key for k, _ in pack_items(items, pack)):
        raise ValueError(f"{key} isn't in your backpack")
    layer, why = wear_layer(world, serial)
    if not layer:
        raise ValueError(f"{key}: {why}")
    return actions.lift(serial, it.get("amount") or 1), actions.equip_request(serial, layer, me)


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


def reagents(world: dict, me: int) -> tuple[dict, bool]:
    """({reagent graphic: count}, spellstone?) over the backpack at any bag depth.
    A stone whose click label reads "[bound to X]" for another character doesn't
    count (live 2026-10-03: with one loose in the pack and ours in a bag the server
    refused every cast with 502630; which stone it checks first is unknown, so a
    refusal still blocks the spell via no_reagents_answer)."""
    items, labels = world.get("items") or {}, world.get("labels") or {}
    pack = backpack(items, me)
    counts, stone = {}, False
    if pack is None:
        return counts, stone
    mine = "[bound to " + ((world.get("self") or {}).get("name") or "") + "]"
    for k, it in pack_items(items, pack):
        g = _serial(it["graphic"])
        if g in REAGENTS:
            counts[g] = counts.get(g, 0) + (it.get("amount") or 1)
        label = labels.get(k) or ""
        if label.startswith("[bound to ") and label != mine:
            continue
        name = (it.get("name") or label or "").lower()
        if any(w in name for w in SPELLSTONE_WORDS) or any(
                w in (_tile_name(g) or "").lower() for w in SPELLSTONE_WORDS):
            stone = True
    return counts, stone


def _tile_name(graphic: int) -> str | None:
    """The client's tiledata name of an item graphic (install dir, read-only), or None.
    Item names reach the world model only through clicks and labels, so after a
    proxy restart an unclicked "arielle's bauble" is known only by its graphic."""
    try:
        import uomap
        tile = uomap.tiledata().item(graphic)
    except (OSError, ValueError):
        return None
    return tile.name if tile else None


def missing_reagents(world: dict, me: int, sid: int) -> list[str]:
    """Names of the reagents spell `sid` needs that the backpack lacks; [] with a spellstone."""
    counts, stone = reagents(world, me)
    if stone:
        return []
    return [REAGENTS[g] for g in SPELL_REAGENTS[sid] if counts.get(g, 0) < 1]


def can_cast(world: dict, me: int, sid: int, mana: int | None) -> bool:
    """Mana (None: unknown, not checked) and reagents or a spellstone for spell `sid`."""
    if mana is not None and mana < spell_mana(sid):
        return False
    return not missing_reagents(world, me, sid)


def no_reagents_answer(events) -> bool:
    """The server's 'More reagents are needed for this spell.' among `events`."""
    return any((e.get("ev") == "cliloc" and e.get("cliloc") == CLILOC_NO_REAGENTS)
               or (e.get("ev") == "speech_heard" and e.get("text") == TEXT_NO_REAGENTS) for e in events)


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
    """A player's or human NPC's corpse (a player, our own, a townsman): never looted
    (criminal, and policy: no corpse runs). A corpse's amount is the body it was.
    Players' corpses are named "the remains of <name>"; monster corpses "<a|an> <creature>
    corpse" (store, 1,700+ names). A human-bodied monster ("an orc hunter corpse", Urukton
    Bluffs 2026-10-03) is a monster corpse; any other human-bodied corpse counts as human."""
    name = (corpse.get("name") or "").strip().lower()
    if "remains of" in name:
        return True
    if corpse.get("amount") in threats.HUMAN_BODIES:
        return not _MONSTER_CORPSE.match(name)
    return False


_MONSTER_CORPSE = re.compile(r"^(a|an) .+ corpse$")


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
