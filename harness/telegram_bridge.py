"""Telegram bridge for the overseer bus (docs/OVERSEER.md §8).

The overseer's chat and the junctures that wake it go to a Telegram bot chat on
the user's phone, and what the user writes to the bot becomes a `user` chat row,
which wakes `ctl wait` like a message typed in the viz. Like every other party on
the bus it only needs the DB file (and api.telegram.org); it never touches the
client, the proxy or the game connection. Stdlib only (urllib).

Usage: python harness/telegram_bridge.py [--db P] [--config P] <cmd>
  pair                    wait for a message to the bot; save that chat as the only one
                          the bridge talks to
  run [--thoughts] [--actions] [--min-severity info|attention|urgent]
                          the bridge (long-lived; Ctrl+C ends it)
  send <text>             one message to the paired chat (setup check)
Config: harness/data/telegram.json (gitignored: it holds the bot token),
  {"token": "123456:ABC...", "chat_id": 123456789}; the environment variables
  UO_TELEGRAM_TOKEN and UO_TELEGRAM_CHAT_ID override it.
"""
import argparse
import heapq
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import task_wrap as tw  # noqa: E402
from ctl import ALWAYS_WAKE, HEARTBEAT_KEY, SEVERITY_RANK  # noqa: E402
from memory import DEFAULT_DB, Memory  # noqa: E402

API = "https://api.telegram.org"
API_ENV = "UO_TELEGRAM_API"                  # tests: a fake Bot API base URL
TOKEN_ENV, CHAT_ENV = "UO_TELEGRAM_TOKEN", "UO_TELEGRAM_CHAT_ID"
CONFIG_PATH = os.path.join(HERE, "data", "telegram.json")
VIA = "telegram"                             # chat row data.via of messages from the bot
CHAT_CURSOR_KEY = "telegram_chat_cursor"
JUNCTURE_CURSOR_KEY = "telegram_juncture_cursor"
UPDATE_OFFSET_KEY = "telegram_update_offset"
MSG_MAX = 4096                               # Bot API sendMessage text limit
CHAT_MAX = 2000                              # same limit as the viz compose box (POST /api/chat)
HEARTBEAT_FRESH_S = 90.0                     # the viz's "overseer active" (viz/src/overseer.ts)
LONG_POLL_S = 25
POLL_S = 1.0
BACKOFF_S = (2, 5, 10, 30, 60)


def log(msg: str):
    print(f"{time.strftime('%H:%M:%S')} telegram: {msg}", file=sys.stderr, flush=True)


class ApiError(Exception):
    """A failed Bot API call. `code` None = no HTTP answer (network, timeout)."""

    def __init__(self, code, description, retry_after=None):
        super().__init__(f"{code}: {description}")
        self.code, self.description, self.retry_after = code, description, retry_after

    @property
    def retryable(self) -> bool:
        return self.code is None or self.code == 429 or self.code >= 500


class Bot:
    def __init__(self, token: str, api: str | None = None):
        self.token = token
        self.api = (api or os.environ.get(API_ENV) or API).rstrip("/")

    def call(self, method: str, http_timeout: float = 15.0, **params):
        req = urllib.request.Request(f"{self.api}/bot{self.token}/{method}", json.dumps(params).encode(),
                                     {"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=http_timeout) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:      # the API answers errors with a JSON body
            try:
                res = json.loads(e.read())
            except ValueError:
                raise ApiError(e.code, e.reason) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            # never str() the request: its URL carries the token
            raise ApiError(None, type(e).__name__ + (f" {e.reason}" if hasattr(e, "reason") else "")) from None
        if not res.get("ok"):
            raise ApiError(res.get("error_code"), res.get("description"),
                           (res.get("parameters") or {}).get("retry_after"))
        return res["result"]

    def send(self, chat_id: int, text: str, silent: bool = False):
        for part in chunks(text):
            self.call("sendMessage", chat_id=chat_id, text=part, disable_notification=silent)


def chunks(text: str, n: int = MSG_MAX) -> list[str]:
    """Split at line breaks where possible, every part at most n characters."""
    out = []
    while len(text) > n:
        cut = text.rfind("\n", 0, n)
        cut = n if cut <= 0 else cut
        out.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return out + [text] if text else out


# ----------------------------------------------------------------------- config
def load_config(path: str = CONFIG_PATH) -> dict:
    cfg = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    token = os.environ.get(TOKEN_ENV) or cfg.get("token")
    chat = os.environ.get(CHAT_ENV) or cfg.get("chat_id")
    return {"token": token, "chat_id": None if chat in (None, "") else int(chat)}


def save_config(path: str, token: str, chat_id: int):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"token": token, "chat_id": chat_id}, f)
    os.replace(tmp, path)


# ----------------------------------------------------------------------- formatting
def format_chat(row: dict, thoughts: bool = False, actions: bool = False) -> tuple[str, bool] | None:
    """(text, silent) for a chat row, or None when it isn't forwarded."""
    role, kind, text = row["role"], row["kind"], row["text"]
    if role == "overseer":
        if kind == "message":
            return f"overseer: {text}", False
        if kind == "thought" and thoughts:
            return f"overseer (thinking): {text}", True
        if kind == "action" and actions:
            return f"overseer did: {text}", True
        return None                            # memory rows, and thoughts/actions unless asked for
    if role == "user":
        # the user's own Telegram lines are already in the chat; viz lines complete the transcript
        return None if (row.get("data") or {}).get("via") == VIA else (f"you (viz): {text}", True)
    return f"system: {text}", True


def format_juncture(j: dict, min_rank: int) -> tuple[str, bool] | None:
    """(text, silent) for a juncture: what wakes `ctl wait` (severity >= min_rank,
    and always a task's end), or None."""
    if SEVERITY_RANK.get(j["severity"], 0) < min_rank and j["kind"] not in ALWAYS_WAKE:
        return None
    text = f"{j['severity'].upper()} {j['kind']} #{j['id']} ({j['source']})\n{j['summary']}"
    if j.get("acked_t") is not None:
        text += "\n(already acked)"
    return text, j["severity"] == "info"


def ago(s: float) -> str:
    return f"{s:.0f}s" if s < 60 else f"{s / 60:.0f}m" if s < 3600 else f"{s / 3600:.0f}h"


# ----------------------------------------------------------------------- bridge
class Bridge:
    def __init__(self, mem: Memory, bot: Bot, chat_id: int, thoughts: bool = False, actions: bool = False,
                 min_rank: int = SEVERITY_RANK["attention"], sleep=time.sleep):
        self.mem, self.bot, self.chat_id = mem, bot, chat_id
        self.thoughts, self.actions, self.min_rank = thoughts, actions, min_rank
        self.sleep = sleep
        self._ignored_chats: set = set()

    # -- store -> Telegram ------------------------------------------------------
    def init_cursors(self):
        """A first run starts at the newest rows: no flood of old history."""
        for key, table in ((CHAT_CURSOR_KEY, "chat"), (JUNCTURE_CURSOR_KEY, "junctures")):
            if tw.meta_get(self.mem, key) is None:
                top = self.mem.con.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()[0]
                tw.meta_set(self.mem, key, str(top))

    def pump_out(self) -> int:
        """Deliver every new chat row and juncture in time order; returns messages sent.
        A cursor moves past a row only once it is sent (or isn't forwarded), so a
        restart or a network outage loses nothing."""
        ccur = int(tw.meta_get(self.mem, CHAT_CURSOR_KEY, "0"))
        jcur = int(tw.meta_get(self.mem, JUNCTURE_CURSOR_KEY, "0"))
        rows = heapq.merge(((r["t"], CHAT_CURSOR_KEY, r) for r in self.mem.chat(after_id=ccur, limit=200)),
                           ((j["t"], JUNCTURE_CURSOR_KEY, j) for j in self.mem.junctures(after_id=jcur, limit=200)),
                           key=lambda x: x[0])
        sent = 0
        for _, key, row in rows:
            msg = (format_chat(row, self.thoughts, self.actions) if key == CHAT_CURSOR_KEY
                   else format_juncture(row, self.min_rank))
            if msg is not None and self.deliver(*msg):
                sent += 1
            tw.meta_set(self.mem, key, str(row["id"]))
        return sent

    def deliver(self, text: str, silent: bool) -> bool:
        """Send, waiting out rate limits and outages; False if the API refuses the
        message for good (then it is skipped and logged)."""
        tries = 0
        while True:
            try:
                self.bot.send(self.chat_id, text, silent)
                return True
            except ApiError as e:
                if not e.retryable:
                    log(f"message refused, skipped ({e}): {text[:80]!r}")
                    return False
                wait = e.retry_after or BACKOFF_S[min(tries, len(BACKOFF_S) - 1)]
                log(f"send failed ({e}); retrying in {wait}s")
                tries += 1
                self.sleep(wait)

    # -- Telegram -> store ------------------------------------------------------
    def pump_in(self, timeout: int = LONG_POLL_S) -> int:
        """One getUpdates long poll; returns user chat rows posted."""
        offset = int(tw.meta_get(self.mem, UPDATE_OFFSET_KEY, "0"))
        updates = self.bot.call("getUpdates", timeout + 10, offset=offset, timeout=timeout,
                                allowed_updates=["message"])
        posted = 0
        for u in updates:
            posted += self.handle(u.get("message"))
            tw.meta_set(self.mem, UPDATE_OFFSET_KEY, str(u["update_id"] + 1))
        return posted

    def handle(self, msg: dict | None) -> int:
        if not msg:
            return 0
        chat = (msg.get("chat") or {}).get("id")
        if chat != self.chat_id:
            if chat not in self._ignored_chats:     # anyone can find a bot: only the paired chat counts
                self._ignored_chats.add(chat)
                log(f"ignoring messages from unpaired chat {chat}")
            return 0
        text = (msg.get("text") or "").strip()
        if text == "/start":                        # Telegram sends it when the chat is opened
            return 0
        if not text:
            self.reply("Only text messages reach the overseer.")
            return 0
        if len(text) > CHAT_MAX:
            self.reply(f"Too long for the overseer chat ({len(text)}/{CHAT_MAX} characters); not sent.")
            return 0
        self.mem.chat_post("user", text, data={"via": VIA, "message_id": msg.get("message_id")})
        hb = tw.meta_get(self.mem, HEARTBEAT_KEY)
        age = None if hb is None else time.time() - float(hb)
        if age is None or age >= HEARTBEAT_FRESH_S:
            seen = "never seen" if age is None else f"last seen {ago(age)} ago"
            self.reply(f"No overseer running ({seen}); your message waits in the store until one starts.")
        return 1

    def reply(self, text: str):
        try:
            self.bot.send(self.chat_id, text, silent=True)
        except ApiError as e:
            log(f"reply failed ({e})")


# ----------------------------------------------------------------------- commands
def cmd_pair(a, cfg) -> int:
    bot = Bot(cfg["token"])
    me = bot.call("getMe")
    log(f"send any message to @{me.get('username')} from the Telegram account that should get "
        f"the overseer chat (waiting up to {a.timeout:.0f}s)")
    end = time.monotonic() + a.timeout
    offset = 0
    while time.monotonic() < end:
        for u in bot.call("getUpdates", LONG_POLL_S + 10, offset=offset, timeout=LONG_POLL_S,
                          allowed_updates=["message"]):
            offset = u["update_id"] + 1
            chat = (u.get("message") or {}).get("chat") or {}
            if chat.get("type") == "private":
                bot.call("getUpdates", offset=offset, timeout=0)     # confirm: the bridge won't see it again
                save_config(a.config, cfg["token"], chat["id"])
                bot.send(chat["id"], "Paired: the overseer chat and its alerts will arrive here.")
                log(f"paired with chat {chat['id']} ({chat.get('username') or chat.get('first_name')}); "
                    f"saved {a.config}")
                return 0
    log("no private message arrived; nothing saved")
    return 1


def cmd_send(a, cfg) -> int:
    Bot(cfg["token"]).send(cfg["chat_id"], " ".join(a.text))
    return 0


def cmd_run(a, cfg) -> int:
    def make(mem):
        return Bridge(mem, Bot(cfg["token"]), cfg["chat_id"], thoughts=a.thoughts, actions=a.actions,
                      min_rank=SEVERITY_RANK[a.min_severity])

    def inbound():
        b, fails = make(Memory(a.db)), 0
        while True:
            try:
                b.pump_in()
                fails = 0
            except (ApiError, sqlite3.Error) as e:   # 409: another bridge (or pair) is polling this bot
                wait = getattr(e, "retry_after", None) or BACKOFF_S[min(fails, len(BACKOFF_S) - 1)]
                log(f"getUpdates failed ({e}); retrying in {wait}s")
                fails += 1
                time.sleep(wait)

    out = make(Memory(a.db))
    out.init_cursors()
    threading.Thread(target=inbound, name="telegram-in", daemon=True).start()
    log(f"bridging {a.db} <-> chat {cfg['chat_id']} (junctures >= {a.min_severity}"
        f"{', thoughts' if a.thoughts else ''}{', actions' if a.actions else ''})")
    try:
        while True:
            try:
                out.pump_out()
            except sqlite3.Error as e:            # a locked store: try again next poll
                log(f"store error ({e})")
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--config", default=CONFIG_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pair", help="save the chat of the next private message to the bot")
    p.add_argument("--timeout", type=float, default=300.0)
    r = sub.add_parser("run", help="the bridge (long-lived)")
    r.add_argument("--thoughts", action="store_true", help="also forward `ctl think` rows (silent)")
    r.add_argument("--actions", action="store_true", help="also forward action rows (silent)")
    r.add_argument("--min-severity", choices=tuple(SEVERITY_RANK), default="attention",
                   help="junctures forwarded (a task's end always is); default: what wakes `ctl wait`")
    s = sub.add_parser("send", help="one message to the paired chat")
    s.add_argument("text", nargs="+")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    if not cfg["token"]:
        log(f"no bot token: put {{\"token\": ...}} in {a.config} or set {TOKEN_ENV} (docs/OVERSEER.md §8)")
        return 2
    if a.cmd != "pair" and cfg["chat_id"] is None:
        log("no chat paired yet: run `telegram_bridge.py pair` first")
        return 2
    try:
        return {"pair": cmd_pair, "run": cmd_run, "send": cmd_send}[a.cmd](a, cfg)
    except ApiError as e:
        log(f"Bot API error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
