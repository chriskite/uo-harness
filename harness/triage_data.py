"""Build the Laya speech-triage fine-tuning set and the held-out real lines
(docs/PLAN.md "Laya speech triage"; how to run it: docs/NOTES.md).

  python harness/triage_data.py [--out harness/data/triage] [--seed 1]

Writes, under --out (gitignored: it holds real player lines):
- train.jsonl     one prompt per row, exactly as triage.state_text builds it:
                  {"state", "check", "direct", "source", "text"}; `direct` may
                  be null (addressee unknown: that row trains `check` only)
- heldout.jsonl   real in-game player lines never trained on, with their real
                  context, for eval_triage.py --heldout:
                  {"lines": [[speaker, text]...], "near": [[name, tiles]...],
                   "me", "check", "direct", "source"}
- composition.json  rows per source and label (also printed)

Labels (both questions are about the last line):
- check  = 1: aimed at us and probing whether we're there / awake / human,
  asking what we're doing, or telling us to say or do something to show it.
- direct = 1: aimed at us (a greeting, question, request or remark to us).
- both 0: aimed at someone else or at nobody (ads, chatter, emotes, pets).

Sources:
- real      in-game player lines from the memory store (harness.db speech_heard
            events, filtered like speech_guard: say/emote/whisper/yell, no click
            echoes, no numbers, no NPCs or creatures, not us). All check=0.
- discord   Discord messages (harness/data/discord.db) as nearby player lines:
            register, typos, slang. Not in-game, so a bounded share
            (DISCORD_SHARE of the player side). Second-person questions are
            skipped (in a scene they could be aimed at us).
- hardneg   synthetic player lines: friends pinging each other with context
            showing who it's for, trade ads, guild chatter, pet commands,
            'say 1'-style group counts, Turkish/French/Portuguese/Czech/Japanese
            chatter and ads, ad cycles (one player's 2-5 ads in a row; added in
            run 4 after held-out recruitment-ad cycles kept alarming).
- gm        synthetic GM-style lines aimed at us (check=1, direct=1): varied
            personas, with and without our name, after silence or mid-chatter,
            speaker on screen or hidden; English only (run 1 had a few non-English
            checks and then alarmed on held-out Turkish ads [INFERENCE: cause]). Written
            by us: no real Outlands staff speech exists in anything we have
            (PLAN.md, 2026-10-04).
- decoy     the gm wording aimed at someone else (another named player, a
            party, a quote): check=0, direct=0.
- toyou     players talking to us without checking on us (trade, help,
            compliments, a back-and-forth with our reply in the context; added
            in run 3 after run 2 alarmed on a held-out real conversation with
            us): check=0, direct=1.

Leakage rules: the eval_triage.py CASES lines, and real lines held out here,
never enter training, not even as context; synthetic and Discord lines within
NEAR_DUP (difflib ratio, normalised text) of any of them are dropped. Held-out
real lines: every text an eval speaker said, the one real conversation aimed at
us (Da Mountain Man), and HOLDOUT_PCT of the other texts by a stable hash,
closed under near-duplicates.
"""
from __future__ import annotations

import argparse
import collections
import difflib
import hashlib
import json
import os
import random
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import eval_triage  # noqa: E402
import speech_guard  # noqa: E402
import triage  # noqa: E402

DATA = os.path.join(HERE, "data")
OUT = os.path.join(DATA, "triage")
HOLDOUT_PCT = 30
NEAR_DUP = 0.85
PER_TEXT = 3                 # contexts kept per real training text (ads repeat hundreds of times)
DISCORD_SHARE = 0.25         # of the player-side rows (real + discord + hardneg)
OURS = {"Hackworth", "Shackleworth", "TestWorth"}       # our characters (states: "I am X")
NPC_NAME = re.compile(r"\b(Acolyte|Preacher|Agent) of Entropy$")   # canned-verse NPCs without a title label
CREATURE = re.compile(r"^(a|an) ", re.I)
# Real lines aimed at us (he answered Hackworth, session 40); and lines whose
# addressee we can't tell (direct unknown: check only).
TO_US = {"Da Mountain Man"}
UNSURE = {"Totten", "Johhanna", "Jaroslaus", "JustKiss", "JohnyRcz", "Akordia", "Joel Embiid", "Hermes Trivium"}
FORCE_HELDOUT = {"Da Mountain Man"}

# Our names in synthetic rows: the live characters plus invented ones, never
# the eval's TestWorth, so a gain on the eval isn't name matching.
ME_NAMES = ["Hackworth", "Shackleworth", "Bramblewood", "Oakhart", "Fenwick", "Tamsin Reed", "Ashby", "Corwin Vale",
            "Elric Thorne", "Mossbeard", "Juniper", "Garrick", "Willa Stone", "Pell", "Hollis", "Rowan Tane",
            "Birchley", "Ferris", "Odo", "Ysolde", "Kestrel", "Brannoc", "Lumen", "Thatch"]
PLAYERS = ["Aldric Fenn", "Morwen", "xXDarkXx", "Sir Pantsalot", "Velka", "Grimsby", "Ottilie", "Brother Cade",
           "Nyx", "Lord Bramwell", "Saffron", "Torvik", "Mags", "Ulfric Stone", "Pip", "Delphine", "KillaKev",
           "Ragnhild", "Bodo", "Selwyn", "Iskra", "Thornwick", "Ember", "Quinlan", "Hesper", "Dunmore", "Tavi",
           "Jorund", "Lysa", "Crowley", "Bex", "Fennimore", "Zarra", "Old Tom", "Wendell", "Ilsa Marr", "Mirko",
           "Kaan", "Emre", "Zeynep", "Baris", "Elif", "Aurelien", "Margaux", "Lucas QC", "Joao", "Tiago",
           "Big Lenny", "Snowbird", "Dawnstrider", "Mister Fisher", "Hagar", "Rook", "Sly", "Tamwyn", "Corra"]
STAFF = ["GM Arden", "Seer Lys", "Counselor Pike", "Staff Hollow", "GM Rhea", "Seer Tobin", "Arden", "Mirelle",
         "Tobin", "Sable", "Quill", "Hollow", "Vane", "Corwyn", "Lark", "Ostrander", "Beck", "Halden", "Wren",
         "Marrow", "Ilse", "Cato", "Juno", "Fable"]
PETS = ["Fluffy", "Bessie", "Rex", "Thunder", "Shadow", "Biscuit", "Brutus", "Nugget", "Ziggy", "Mochi", "Tank", "Pepper"]


def norm(t: str) -> str:
    return re.sub(r"[\W_]+", " ", t.casefold()).strip()


class NearDup:
    """Near-duplicate test of normalised text against a fixed set."""

    def __init__(self, texts):
        self.exact = {norm(t) for t in texts}
        self.items = [n for n in self.exact if n]

    def __call__(self, text: str) -> bool:
        n = norm(text)
        if n in self.exact:
            return True
        for m in self.items:
            sm = difflib.SequenceMatcher(None, n, m)
            if sm.real_quick_ratio() >= NEAR_DUP and sm.quick_ratio() >= NEAR_DUP and sm.ratio() >= NEAR_DUP:
                return True
        return False


def stable(s: str) -> int:
    return int(hashlib.sha1(s.encode()).hexdigest()[:8], 16)


# ---------------------------------------------------------------- prompts

def world_for(me: str, near: list[tuple[str, int]]) -> dict:
    mobiles = {f"0x{i + 1:08X}": {"graphic": 0x190, "notoriety": 1, "flags": 0x20, "x": 100 + d, "y": 100, "name": n}
               for i, (n, d) in enumerate(near)}
    return {"self": {"serial": "0x00094375", "name": me, "x": 100, "y": 100}, "mobiles": mobiles, "labels": {}}


def state(me: str, lines: list[tuple[str, str]], near: list[tuple[str, int]]) -> str:
    who = {"serial": "0x00000001", "text": lines[-1][1], "context": [{"name": n, "text": x} for n, x in lines]}
    return triage.state_text(who, world_for(me, near))


# ---------------------------------------------------------------- real lines

def real_lines(db: str) -> list[dict]:
    """Character lines heard in-game, in order, each with its context (the
    lines within speech_guard.RECENT_S, last RECENT_N, ours included)."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("SELECT session, t, ev, data FROM events WHERE ev IN ('speech_heard', 'query') "
                       "ORDER BY session, seq").fetchall()
    npc = set()
    for _, _, ev, d in rows:
        if ev == "speech_heard" and '"type": 6' in d:
            e = json.loads(d)
            if e.get("type") == 6 and speech_guard.threats._TITLE.match(e.get("text") or ""):
                npc.add(e["serial"])
    out, recent, clicked, me, sess = [], collections.deque(maxlen=speech_guard.RECENT_N), {}, None, None
    for s, t, ev, d in rows:
        if s != sess:
            sess, me, clicked = s, None, {}
            recent.clear()
        e = json.loads(d)
        ser = e.get("serial")
        if ser is None or t is None:
            continue
        if ev == "query":
            if e.get("kind") == 0x09:
                clicked[ser] = t
            continue
        if t - clicked.get(ser, -1e18) <= speech_guard.CLICK_ECHO_S or e.get("type") not in speech_guard.SPEECH_TYPES:
            continue
        text, name = e.get("text") or "", e.get("name") or ""
        if ser in (0, 0xFFFFFFFF) or ser >= speech_guard.ITEM_SERIAL_MIN or not text.strip() \
                or speech_guard._NUMBER.match(text):
            continue
        if name in OURS:
            me = name
            recent.append((t, name, text))
            continue
        if CREATURE.match(name) or ser in npc or NPC_NAME.search(name) or not name:
            continue
        recent.append((t, name, text))
        ctx = [(n, x) for tt, n, x in recent if t - tt <= speech_guard.RECENT_S]
        out.append({"session": s, "t": t, "name": name, "text": text, "me": me, "context": ctx})
    for r in out:              # lines before our first line of the session: the session's character
        r["me"] = r["me"] or next((x["me"] for x in out if x["session"] == r["session"] and x["me"]), "Hackworth")
    return out


def near_for(r: dict, rng: random.Random) -> list[tuple[str, int]]:
    names = []
    for n, _ in r["context"]:
        if n != r["me"] and n not in names:
            names.append(n)
    near = [(n, rng.randint(1, 16)) for n in names]
    if rng.random() < 0.4:
        near.append((rng.choice(PLAYERS), rng.randint(3, 18)))
    return sorted(near, key=lambda p: p[1])


def direct_label(name: str):
    return 1 if name in TO_US else None if name in UNSURE else 0


def split_real(lines: list[dict]):
    """(train texts, held-out texts) by normalised text."""
    eval_speakers = {sp for label, ls, _ in eval_triage.CASES if label == 0 for sp, _ in ls}
    eval_texts = [x for _, ls, _ in eval_triage.CASES for _, x in ls]
    by_text = collections.defaultdict(set)
    for r in lines:
        by_text[norm(r["text"])].add(r["name"])
    near_eval = NearDup(eval_texts)
    held = {n for n, who in by_text.items()
            if who & (eval_speakers | FORCE_HELDOUT) or stable(n) % 100 < HOLDOUT_PCT or near_eval(n)}
    while True:                 # close under near-duplicates
        nd = NearDup(held)
        moved = {n for n in by_text if n not in held and nd(n)}
        if not moved:
            break
        held |= moved
    return set(by_text) - held, held


# ---------------------------------------------------------------- discord

DISCORD_CHANNELS = ("newplayer", "harvesting", "sell", "buy", "taming", "pvp", "offtopic", "trade", "thieves-pvm",
                    "pvm", "guilds-recruiting", "time-dungeon", "ships-pvm", "real-estate", "sell-archive", "buy-archive")
_SKIP_DISCORD = re.compile(r"https?://|<[@#:a]|```|\n|@everyone|@here")
_SECOND_Q = re.compile(r"\b(you|u|ya|ur|your|youre|you're|yall|y'all|anyone|any1|somebody|someone)\b", re.I)


def discord_seeds(db: str, rng: random.Random, n: int, bad) -> list[list[str]]:
    """`n` groups of 1-3 consecutive short messages from one channel: the last
    is the judged line, the others its context."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    q = ("SELECT m.channel_id, m.content FROM messages m JOIN channels c ON c.id = m.channel_id "
         f"WHERE c.name IN ({','.join('?' * len(DISCORD_CHANNELS))}) ORDER BY m.channel_id, m.id")
    by_ch = collections.defaultdict(list)
    for ch, text in con.execute(q, DISCORD_CHANNELS):
        text = (text or "").strip()
        if 3 <= len(text) <= 90 and not _SKIP_DISCORD.search(text) and re.search(r"[A-Za-z]{2}", text):
            by_ch[ch].append(text)
    pool = [(ch, i) for ch, msgs in by_ch.items() for i in range(len(msgs))]
    rng.shuffle(pool)
    out, seen = [], set()
    for ch, i in pool:
        last = by_ch[ch][i]
        if "?" in last and _SECOND_Q.search(last):
            continue
        if norm(last) in seen or bad(last):
            continue
        k = rng.choice((0, 0, 1, 2))
        ctx = [m for m in by_ch[ch][max(0, i - k):i] if not bad(m)]
        seen.add(norm(last))
        out.append(ctx + [last])
        if len(out) >= n:
            break
    return out


# ---------------------------------------------------------------- synthetic

# Attendance checks and questions a GM might put to a gatherer. {me} = our name.
GM_CORES = [
    "are you still with us?", "hello, can you hear me?", "{me}, are you at your computer?", "anybody in there?",
    "you awake?", "still breathing over there?", "are you around?", "can you hear me {me}",
    "i need a response from you", "please reply to me", "answer me please", "Please acknowledge this message.",
    "Hi there, this is a staff member. Please respond within the next minute.",
    "Staff here. Are you actively playing right now?", "Greetings {me}. This is a routine check, please reply.",
    "{me}, I'm a game master. Please say something.", "Are you playing this character yourself?",
    "is someone actually controlling this character?", "are you a real person?", "are you human?",
    "prove you're not a script", "Are you using any third party programs?", "is this an unattended macro?",
    "you botting?", "how long have you been out here?", "what are you up to out here?", "whatcha doing?",
    "what are you working on today?", "why have you been at the same tree for an hour?",
    "type the word banana in chat please", "say the name of the nearest town", "please type your character's name",
    "say yes if you can read this", "say something so i know you're there", "move one step to your left",
    "please walk north a few steps", "stop what you're doing and talk to me", "pause for a moment and reply please",
    "come over here", "follow me please", "{me} please respond to this GM",
    "This is Outlands staff. Answer within two minutes please.", "hellooo?", "knock knock", "ping",
    "earth to {me}", "you with me?", "hey, still here?", "r u at ur pc", "u afk or wat", "respond pls", "reply",
    "hi {me}, quick check, you there", "Excuse me {me}, may I have a moment?",
    "what's the weather like where you are?", "what is 3 plus 4?", "how many hours have you played today?",
    "tell me a joke", "what color is your horse?", "spell your name backwards", "{me}?", "{me}!!",
    "hey {me}", "good evening {me}", "{me} can we talk for a second", "Hello {me}, do you have a minute?",
    "I am a GM, please answer me", "Game Master here, are you present?", "Staff check, reply please",
    "this is a staff member, are you available to talk?", "Can you confirm you are at the keyboard?",
    "need you to answer me real quick", "i see you working, mind saying hi?", "can you stop and say hello?",
    "are you there or not", "hello hello", "are you sleeping?", "did you fall asleep?", "are you away?",
    "are you on autopilot?", "are you even reading this", "am i talking to a bot?", "is anyone playing this char",
    "wave if you can see me", "please emote *waves*", "bow if you hear me", "type 'here' if you are here",
    "can you do a quick dance for me?", "tell me what you are doing right now", "where are you headed?",
    "how is the gathering going today?", "what's your plan for those resources?",
    "i've been watching you for a while, everything ok?", "you've been quiet a long time, all good?",
    "Good afternoon. I'm with the Outlands staff team and I'd like a word with you.",
    "Hello {me}, this is a GM. I just need a quick reply from you to confirm you're playing.",
    "We received a report about this area. {me}, please answer when you see this.",
    "{me}, this is your second message from staff. Please respond.",
    "Last chance to reply before I take action on this character.",
    "If you don't respond I'll have to move you to jail.", "Unattended resource gathering isn't allowed. Reply?",
    "Sir, a moment of your time please.", "Madam, are you at your station?", "Traveler, do you hear me?",
    "Greetings, woodsman. Speak if you are present.", "Well met. Kindly answer me, friend.",
    "lumberjack, say hi", "hi there, what's up?", "hey bud, what's going on here?", "sup, whatcha up to",
    "whats ur name?", "how did you get here?", "where did you come from?", "who taught you to chop like that?",
    "how many trees have you cut today?", "what time is it where you are?", "what's 12 minus 5?",
    "name three reagents", "what's the name of your horse?", "what's in your backpack right now?",
    "type the third word of this sentence", "repeat after me: blue apples", "please say 'present'",
    "say the color of my robe", "say any number between one and ten", "please write 'ok' in chat",
    "turn around and face me", "walk to the stump on your east", "take two steps toward me please",
    "drop what you're doing and come here", "stop chopping please", "hold on, stop for a moment",
    "dismount and talk to me", "open a trade window with me please", "can you look at me?",
    "hey, look up from the tree", "yo, chopper, you there", "gatherer, respond", "excuse me, woodcutter?",
    "i need you to talk to me for a minute", "can I get a word in?", "you good? haven't moved in a while",
    "Is everything all right? You seem to be repeating the same thing.",
    "This is a routine activity check. Please type anything.", "Activity check. Reply with any message.",
    "Hello? Staff here. Can you hear me?", "{me}... hello?", "{me} hello?? anyone?", "{me} respond",
    "{me}, answer me", "{me} are you awake", "{me} afk?", "{me} u there", "{me} wake up", "{me}, reply please",
    "{me} I'm talking to you", "{me}, I need you to answer", "you are {me}, right? please answer",
    "{me}, you there? staff here", "{me}, still chopping? say hi",
]
# Players talking to us, not checking on us.
TOYOU_CORES = [
    "{me} how much for a stack of boards?", "can you sell me some cedar?", "{me} need any bandages?",
    "hey {me} want to join our guild?", "thanks for the help {me}!", "nice mount {me}", "cool robe where'd you get it?",
    "{me} do you know where the bank is?", "{me} which way to the moongate?", "{me} you selling those?",
    "ty {me} :)", "gl with your grind {me}", "{me} want to group up for orcs later?", "lol nice hat {me}",
    "{me} i'll trade you 50 ore for some kindling", "{me} can you craft me a bow?", "{me} watch out, reds to the east",
    "{me} careful there's a pk around", "{me} is that tree taken?", "mind if i chop next to you {me}?",
    "{me} do you have a spare hatchet to sell?", "{me} you in a guild?", "welcome to the server {me}!",
    "{me} where did you get that dye?", "{me} wanna buy a house spot?",
    "if you need help just ask", "here to help new players, ask me anything", "welcome to outlands!",
    "are you new here?", "first time on this shard?", "you'll get the hang of it", "nice work, keep it up",
    "good luck out there", "that's a lot of logs lol", "you look like a fellow lumberjack", "same, im new too",
    "lol yeah the start is rough", "want a few bandages for the road?", "i can show you the bank if you want",
    "thats a nice spot you found", "you play long?", "need a gate somewhere?", "careful, orcs spawn here",
]
# A player chatting with us: their opener, our reply, their follow-up (the judged line).
OUR_REPLIES = ["thanks!", "hey", "lol yeah", "just a new char", "ty", "you bet", "hi there", "not much, chopping",
               "sure", "maybe later", "haha", "nope", "been a while", "first week here", "ok cool"]
FOLLOWUPS = ["np", "cool cool", "haha nice", "true", "fair enough", "glad to hear it", "ok then", "yep", "oh nice",
             "gotcha", "have fun out here", "you'll like it here", "see ya around", "if you need anything just whisper",
             "where you from?", "how long you been playing?", "lol same", "right on", "nice nice", "word",
             "the shard is pretty friendly", "you picked a good spot", "watch for thieves near the bank",
             "my guild helps new folks too", "anyway gl", "ha, me too", "sounds good", "take care", "for real",
             "it gets easier after a week"]
FRIEND_PINGS = ["{x} u back?", "{x} you still on?", "yo {x} u alive", "{x}? hello?", "{x} you there bro",
                "{x} wake up lol", "{x} r u afk", "{x} where did you go", "{x} you there? we're leaving",
                "oi {x}", "{x} answer me in discord", "{x} u ok?", "{x} come back to the bank", "{x}!! wait for me"]
FRIEND_SETUPS = ["brb dinner", "afk 5", "brb", "one sec door", "gotta feed the dog", "back in 10", "phone, brb",
                 "going to bank brb", "lag... hold on", "wait for me at the gate"]
TRADE = ["WTS 500 boards 5gp ea", "selling bandages pst", "buying iron ore paying 8 each", "WTB blessed runebook",
         "vendor house at outpost, spots open 4k a week", "LF crafter to fill a large BOD", "selling gm bows cheap",
         "anyone selling dragon scales", "wtb fertile dirt", "Selling Stealth/Hiding scrolls, pm me",
         "buying all your spined leather", "WTS rare dye tubs, best offer", "200k for a tmap level 6, any takers",
         "need a smith to repair my armor", "selling 10k cut leather at the bank", "trading pet whistles here",
         "Kirin taming chance... wtb bonded nightmare", "selling gm runic hammer", "PC on this sword?"]
GUILD = ["[CFC] omni tonight at 8 server time, be there", "recruiting miners and lumberjacks, all welcome",
         "anyone up for a destard run?", "need 2 more for t8 map", "guild meeting at the house after boss",
         "who's got the key to the hall", "Join [OAK] - casual pvm guild, discord in profile",
         "ok group, heal up before the boss", "pull the next room slow", "stack on me", "everyone mount up",
         "bring your pots, ocean boss in 10", "we leave from brit bank", "nice drop!!", "gg all",
         "loot split at the bank", "who needs a res", "rez me pls", "i'm out of bandies", "ugh i died again"]
PET_CMDS = ["all guard me", "all come", "all stay", "all stop", "all follow me", "all release", "{p} kill",
            "{p} follow me", "{p} guard me", "{p} stay", "{p} come", "{p} attack", "{p} stop", "{p} drop",
            "{p} fetch", "{p} patrol", "{p} release", "all attack", "all kill"]
COUNTS = ["everyone say 1 when ready", "party say 2 if you have pots", "x up if you're coming to the boss",
          "type 1 in party when you're at the gate", "say in if you want a spot on the omni",
          "ok say 3 when you're mounted", "count off in party chat", "type ready in chat team",
          "say aye if you agree", "say go when healers are set"]
FOREIGN = ["selam millet, kim dungeon gelir?", "abi nerdesin", "tamam geliyorum", "kanka bankaya gidiyorum",
           "bu agaci ben kesiyorum", "yarin omni var mi", "bon je vais a la banque", "quelqu'un a des bandages?",
           "salut tout le monde", "on part en donjon ce soir", "brb manger", "ok merci beaucoup",
           "c'est quoi le prix des planches?", "attendez-moi au portail", "bora pessoal, boss em 5 min",
           "alguem vende bandagem?", "valeu galera", "ahoj vsichni", "dzieki", "danke dir", "jaja genau",
           "Guildimiz yeni uye ariyor, her aksam dungeon!", "Aktif Turk guildi, discord'dan yaz", "kim ocean gelir",
           "boss kac dakika sonra", "abi bekle beni", "sagol kardesim", "hadi gidelim", "bankada bulusalim",
           "Ucretsiz baslangic seti, guildimize katil!", "yeni oyunculara yardim ediyoruz, katil bize",
           "Rejoignez notre guilde, sorties donjon tous les soirs!", "on recrute, francophones bienvenus",
           "Nova guilda brasileira, venha jogar com a gente!", "Guilda ativa procura membros",
           "Hledame nove cleny do guildy", "メンバー募集中、気軽にどうぞ", "初心者歓迎です"]
EMOTES = ["*stretches*", "*laughs*", "*bows*", "*sighs*", "*dances*", "*cheers*", "*coughs*", "*shrugs*",
          "*waves at {x}*", "*hugs {x}*", "*pokes {x}*", "*salutes*"]
# Recruitment and vendor-house ads, said in cycles by one player (real ones run 5-25 lines).
ADS = ["[OAK] Casual PvM guild recruiting, all timezones welcome!", "[OAK] Weekly boss nights and T-map runs!",
       "[OAK] New players get a starter kit and help with skills", "Interested in [OAK]? Discord: oakhaven",
       "Rune library, crafting hall and stables at our guild house!", "Tired of soloing? Join a friendly group today!",
       "Ocean, dungeons, factions - pick your content with us!", "Voice chat optional, fun mandatory",
       "Yeni oyuncu musun? [OAK] seni bekliyor!", "Her hafta boss ve harita gezisi, bize katil!",
       "Discord'dan yaz, hemen davet edelim", "Turk oyuncular icin aktif guild, katil!",
       "Guilde [OAK] recrute, francophones bienvenus!", "Sorties en groupe chaque soir, viens avec nous!",
       "Guilda [OAK] recrutando, todos bem-vindos!", "Venha farmar com a gente toda noite!",
       "VENDOR MALL OPEN - spots 3k per week!", "Rune to Westgate Mall in the library", "Bank, forge and gate inside!",
       "Cheapest vendor rent in Prevalia", "~ New arrivals every day ~", "Free reg bags for new players at my house",
       "Selling GM tools, 5gp each, vendor by the north gate", "Taming trainer and pet stable on site",
       "Moongate 1 screen west - easy to find!", "== Grand opening this weekend =="]
# GM wording aimed at someone else: {x} = the addressee, never us.
DECOY = ["{x}, are you at your computer?", "{x} are you still with us?", "{x} you awake?", "{x}, can you hear me?",
         "{x} what are you doing lol", "{x} whatcha doing", "{x} say something", "{x} please reply",
         "{x} you botting? lol jk", "{x} come over here", "{x} follow me", "{x} stop and talk to me",
         "{x} answer me please", "{x} are you a real person haha", "{x} tell me a joke", "{x} are you sleeping?",
         "{x}, how long have you been out here?", "{x} type here if you are here", "hello {x}", "hey {x}",
         "{x} you there? we're pulling", "{x} hello??", "{x} say yes if you're coming", "{x} what are you up to",
         "{x} r u at ur pc", "{x} respond in party", "{x} you macroing again lol", "{x} you with me?",
         "{x} are you even reading party chat", "{x} move left a bit", "{x} walk north, the gate is there",
         "everyone still here?", "anyone still awake in the party?", "group, all at your keyboards? boss soon",
         "{x} did you fall asleep on me", "{x} earth to {x}"]
REPORTED = ["lol a gm asked me if i was afk yesterday", "my buddy got jailed for not answering a gm",
            "gms check gatherers a lot on this shard", "if a gm asks 'are you there' just answer them",
            "staff pinged me while mining lol", "never macro unattended, they will ask you questions",
            "a counselor once asked me to type my name backwards", "the gm said 'please respond' and i was afk",
            "remember when staff made everyone dance at the event", "do gms really check if you're at your pc?"]


def _typo(s: str, rng: random.Random) -> str:
    if len(s) < 5:
        return s
    i = rng.randrange(1, len(s) - 2)
    op = rng.choice(("swap", "drop", "double"))
    if op == "swap":
        return s[:i] + s[i + 1] + s[i] + s[i + 2:]
    if op == "drop":
        return s[:i] + s[i + 1:]
    return s[:i] + s[i] + s[i:]


def style(s: str, rng: random.Random) -> str:
    """One persona's surface: formal as written, terse, lowercase, typo'd, chatspeak."""
    r = rng.random()
    if r < 0.3:
        return s
    if r < 0.5:
        return s.lower().rstrip(".!")
    if r < 0.65:
        return re.sub(r"[.,!']", "", s.lower())
    if r < 0.8:
        return _typo(s.lower(), rng)
    t = re.sub(r"\byou\b", "u", s.lower())
    t = re.sub(r"\bare\b", "r", t)
    t = re.sub(r"\byour\b", "ur", t)
    return t.rstrip(".")


def chatter(rng: random.Random, k: int) -> list[tuple[str, str]]:
    """k lines of other players' talk (synthetic) for a mid-conversation context."""
    pool = TRADE + GUILD + FOREIGN + [e for e in EMOTES if "{x}" not in e]
    return [(rng.choice(PLAYERS), rng.choice(pool)) for _ in range(k)]


def synthetic(rng: random.Random) -> list[dict]:
    """GM checks aimed at us, the same words aimed elsewhere, players talking
    to us, and player-side hard negatives, each in a context."""
    rows = []

    def add(source, me, lines, near, check, direct):
        rows.append({"source": source, "me": me, "lines": lines, "near": near, "check": check, "direct": direct})

    for core in GM_CORES:
        for _ in range(4):
            me, gm = rng.choice(ME_NAMES), rng.choice(STAFF)
            text = style(core.format(me=me), rng)
            if "{me}" not in core and rng.random() < 0.3:
                text = rng.choice((f"{me}, {text}", f"{me} {text}", f"{text} {me}"))
            mode = rng.random()
            if mode < 0.4:                                   # after silence
                lines = [(gm, text)]
            elif mode < 0.6:                                 # a second ping
                lines = [(gm, style(rng.choice(GM_CORES).format(me=me), rng)), (gm, text)]
            elif mode < 0.75:                                # after our own line
                lines = [(me, rng.choice(("room", "bank", "*sighs*", "ok", "lol"))), (gm, text)]
            else:                                            # mid-chatter: name it so it's ours
                if me.lower() not in text.lower():
                    text = f"{me}, {text}"
                lines = chatter(rng, rng.randint(1, 3)) + [(gm, text)]
            others = [(n, rng.randint(2, 16)) for n, _ in lines if n not in (me, gm)]
            near = others + ([] if rng.random() < 0.3 else [(gm, rng.randint(1, 8))])   # hidden GM: not on screen
            add("gm", me, lines, sorted(set(near), key=lambda p: p[1]), 1, 1)
    for core in TOYOU_CORES:
        for _ in range(3):
            me, p = rng.choice(ME_NAMES), rng.choice(PLAYERS)
            lines = (chatter(rng, rng.randint(0, 2)) + [(p, style(core.format(me=me), rng))])
            add("toyou", me, lines, [(n, rng.randint(1, 12)) for n in {n for n, _ in lines}], 0, 1)
    for _ in range(90):
        me, p = rng.choice(ME_NAMES), rng.choice(PLAYERS)
        opener = style(rng.choice(TOYOU_CORES).format(me=me), rng)
        lines = [(p, opener), (me, rng.choice(OUR_REPLIES)), (p, style(rng.choice(FOLLOWUPS), rng))]
        if rng.random() < 0.3:
            lines = chatter(rng, 1) + lines
        add("toyou", me, lines, [(n, rng.randint(1, 8)) for n in {n for n, _ in lines if n != me}], 0, 1)
    for core in DECOY + FRIEND_PINGS:
        for _ in range(4):
            me, a, b = rng.choice(ME_NAMES), *rng.sample(PLAYERS, 2)
            text = style(core.format(x=b), rng)
            mode = rng.random()
            if mode < 0.5:
                lines = [(b, rng.choice(FRIEND_SETUPS)), (a, text)]
            elif mode < 0.8:
                lines = [(a, rng.choice(("hey", "you coming?", "ready?", "lets go"))), (b, "1 sec"), (a, text)]
            else:
                lines = chatter(rng, 1) + [(a, text)]
            add("decoy" if core in DECOY else "hardneg", me, lines,
                [(a, rng.randint(1, 10)), (b, rng.randint(1, 10))], 0, 0)
    for text in REPORTED:
        for _ in range(2):
            me, a, b = rng.choice(ME_NAMES), *rng.sample(PLAYERS, 2)
            lines = [(b, rng.choice(("anyone ever been checked by staff?", "gms ever talk to you?", "lol", "true"))),
                     (a, style(text, rng))]
            add("decoy", me, lines, [(a, rng.randint(1, 10)), (b, rng.randint(1, 10))], 0, 0)
    for pool, n in ((TRADE, 2), (GUILD, 2), (FOREIGN, 2), (EMOTES, 2), (COUNTS, 3), (PET_CMDS, 3)):
        for core in pool:
            for _ in range(n):
                me, a, b = rng.choice(ME_NAMES), *rng.sample(PLAYERS, 2)
                text = style(core.format(x=b, p=rng.choice(PETS)), rng)
                if pool is COUNTS:                            # a leader talking to a group
                    group = rng.sample(PLAYERS, 3)
                    lines = [(group[0], "ready"), (group[1], "omw")] + [(a, text)]
                    near = [(g, rng.randint(1, 9)) for g in group] + [(a, rng.randint(1, 9))]
                else:
                    lines = chatter(rng, rng.choice((0, 0, 1))) + [(a, text)]
                    near = [(n_, rng.randint(1, 14)) for n_ in {n_ for n_, _ in lines}]
                add("hardneg", me, lines, near, 0, 0)
    for core in ADS:                                          # one player spamming an ad cycle, 2-5 lines
        for _ in range(2):
            me, a = rng.choice(ME_NAMES), rng.choice(PLAYERS)
            k = rng.randint(1, 4)
            lines = [(a, x) for x in rng.sample([x for x in ADS if x != core], k)] + [(a, core)]
            near = [(a, rng.randint(2, 14))] + ([(rng.choice(PLAYERS), rng.randint(3, 18))] if rng.random() < 0.5 else [])
            add("hardneg", me, lines, near, 0, 0)
    return rows


def dedupe(rows: list[dict], key=lambda r: r["lines"][-1][1]) -> list[dict]:
    """Drop rows whose judged line (our name and the addressee masked) nearly
    repeats one already kept with the same source and labels."""
    kept, seen = [], collections.defaultdict(list)
    for r in rows:
        k = norm(key(r).replace(r["me"], "ME")) + f" ctx{min(len(r['lines']), 3)}"
        for n, _ in r["lines"]:
            k = k.replace(norm(n), "X") if norm(n) else k
        group = seen[(r["source"], r["check"], r["direct"])]
        if any(k == m or difflib.SequenceMatcher(None, k, m).ratio() >= 0.9 for m in group):
            continue
        group.append(k)
        kept.append(r)
    return kept


# ---------------------------------------------------------------- build

def build(out: str, seed: int, harness_db: str, discord_db: str) -> dict:
    rng = random.Random(seed)
    lines = real_lines(harness_db)
    train_t, held_t = split_real(lines)
    eval_texts = [x for _, ls, _ in eval_triage.CASES for _, x in ls]
    held_texts = [r["text"] for r in lines if norm(r["text"]) in held_t]
    bad = NearDup(eval_texts + held_texts)
    banned = {norm(t) for t in eval_texts} | held_t

    heldout, seen = [], set()
    eval_last = {norm(ls[-1][1]) for _, ls, _ in eval_triage.CASES}
    for r in lines:
        n = norm(r["text"])
        if n in held_t and n not in seen and n not in eval_last:
            seen.add(n)
            heldout.append({"lines": r["context"], "near": near_for(r, random.Random(stable(n))), "me": r["me"],
                            "check": 0, "direct": direct_label(r["name"]), "source": "real", "speaker": r["name"]})

    rows, per = [], collections.Counter()
    for r in lines:
        n = norm(r["text"])
        if n not in train_t or per[n] >= PER_TEXT:
            continue
        per[n] += 1
        ctx = [(a, x) for a, x in r["context"][:-1] if norm(x) not in banned] + [r["context"][-1]]
        rows.append({"source": "real", "me": r["me"], "lines": ctx, "near": near_for(r, rng),
                     "check": 0, "direct": direct_label(r["name"])})
    for r in list(rows):        # real lines are the primary player side: each again under another of our names
        if per[norm(r["lines"][-1][1])] < 2:
            rows.append({**r, "me": rng.choice(ME_NAMES), "near": near_for({**r, "context": r["lines"]}, rng)})

    syn = [r for r in synthetic(rng) if not any(bad(x) for _, x in r["lines"])]
    syn = dedupe(syn)
    n_player = len(rows) + sum(1 for r in syn if r["source"] == "hardneg")
    n_discord = int(n_player * DISCORD_SHARE / (1 - DISCORD_SHARE))
    for group in discord_seeds(discord_db, rng, n_discord, bad):
        names = rng.sample(PLAYERS, len(group))
        ls = list(zip(names, group))
        rows.append({"source": "discord", "me": rng.choice(ME_NAMES), "lines": ls,
                     "near": sorted({(n, rng.randint(1, 16)) for n in names}, key=lambda p: p[1]),
                     "check": 0, "direct": 0})
    rows += syn

    os.makedirs(out, exist_ok=True)
    comp = collections.Counter()
    with open(os.path.join(out, "train.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            comp[(r["source"], r["check"], r["direct"])] += 1
            f.write(json.dumps({"state": state(r["me"], r["lines"], r["near"]), "check": r["check"],
                                "direct": r["direct"], "source": r["source"], "text": r["lines"][-1][1]},
                               ensure_ascii=False) + "\n")
    with open(os.path.join(out, "heldout.jsonl"), "w", encoding="utf-8") as f:
        for h in heldout:
            f.write(json.dumps(h, ensure_ascii=False) + "\n")
    summary = {"seed": seed, "train_rows": len(rows),
               "train": [{"source": s, "check": c, "direct": d, "rows": n} for (s, c, d), n in sorted(
                   comp.items(), key=lambda kv: (kv[0][0], kv[0][1], -1 if kv[0][2] is None else kv[0][2]))],
               "real_texts": {"train": len(train_t), "heldout": len(held_t)},
               "heldout_rows": len(heldout),
               "heldout_direct": dict(collections.Counter(str(h["direct"]) for h in heldout))}
    with open(os.path.join(out, "composition.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--harness-db", default=os.path.join(DATA, "harness.db"))
    ap.add_argument("--discord-db", default=os.path.join(DATA, "discord.db"))
    a = ap.parse_args(argv)
    print(json.dumps(build(a.out, a.seed, a.harness_db, a.discord_db), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
