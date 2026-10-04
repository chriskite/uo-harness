"""Hand the memory store (harness/data/harness.db) from one computer to another
through the NAS share (docs/NOTES.md "Two computers (memory store handoff)";
decision: docs/PLAN.md "Two computers: hand the memory store off").

  python harness/dbhandoff.py status
  python harness/dbhandoff.py push [--no-logs] [--force]                  leaving this computer
  python harness/dbhandoff.py pull [--no-logs] [--discard-local] [--force] arriving here
  (all take --dest PATH, default backup.DEFAULT_DEST)

One computer holds the store at a time. The live store is never shared: WAL
mode needs every process on one host, and the rows of two copies can't be
merged (autoincrement ids collide, counters would need adding). So:

  push  needs every process that has the store open stopped (proxy, runners,
        `ctl run` tasks, overseer, Telegram bridge, viz); it checks that by
        switching the store to rollback-journal mode, which SQLite only allows
        to the sole connection, and which also checkpoints the WAL into the
        file. Uploads a verified gzipped copy as generation N+1 and releases
        the store. Refused when another computer holds the store, or when this
        computer's copy isn't based on the newest generation (pull first).
  pull  refused while another computer holds the store (push there first), and
        when this computer's store changed since its own last push or pull (a
        fork: both computers played on the same generation; --discard-local
        replaces it anyway). Installs the generation and takes the store. The
        replaced local store stays next to it as harness.db.prev.

"Changed" compares a content fingerprint: per source table, the row count and
a sha256 over its rows in rowid order. Derived tables (FTS shadow tables,
knowledge_vec) are left out; they regenerate.

On the share, under <dest>/handoff/:
  owner.json                gen, file, sha256 (of the uncompressed store),
                            fingerprint, holder (computer name or null when
                            released), pushed_by/pushed_t, pulled_t,
                            telegram_sha256
  harness-genNNNN.db.gz     the newest generation (older ones are deleted;
                            backup.py's db/ snapshots keep the history)
  telegram.json             the Telegram bridge config (bot token, paired
                            chat), so the bridge runs on whichever computer
                            holds the store. push uploads it when this
                            computer has one; pull installs it and keeps a
                            different local one as telegram.json.prev.
                            The token sits on the share, which backup.py
                            otherwise keeps free of credentials (user
                            decision 2026-10-04).
Here: harness/data/handoff.json, the generation and fingerprint of this
computer's last push or pull.

push also writes a db/ snapshot and copies logs/ to the share (backup.py's
method); pull copies the share's logs/ into logs/, newer files only.
backup.py skips its harness.db snapshot on a computer that doesn't hold the
store (not_held_here), so a computer waking from sleep can't upload a stale
store as the newest backup.
"""

import argparse
import datetime
import gzip
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import sys
import tempfile

import backup

ROOT = backup.ROOT
DB = backup.DB
STATE = os.path.join(ROOT, "harness", "data", "handoff.json")
LOGS = os.path.join(ROOT, "logs")
TELEGRAM = os.path.join(ROOT, "harness", "data", "telegram.json")  # bot token: never print it
TELEGRAM_NAME = "telegram.json"
SUB = "handoff"
OWNER = "owner.json"
DERIVED = {"knowledge_vec"}  # regenerable (docs/MEMORY.md), so not part of the fingerprint


class Refused(Exception):
    """The handoff would lose or fork data; nothing was changed."""


def this_host():
    return socket.gethostname()


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _read_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = path + ".part"
    with open(part, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
    os.replace(part, path)


def read_owner(dest):
    return _read_json(os.path.join(dest, SUB, OWNER))


def not_held_here(dest, me=None):
    """None when this computer may write the store: it holds it, or the handoff
    was never used. Otherwise why not (for backup.py's skip)."""
    owner = read_owner(dest)
    if owner is None or owner.get("holder") == (me or this_host()):
        return None
    if owner.get("holder") is None:
        return f"generation {owner['gen']} released by {owner.get('pushed_by')}, not pulled here"
    return f"held by {owner['holder']}"


def fingerprint(db):
    """{table: [rows, sha256 of the rows in rowid order]} over the source tables."""
    con = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro", uri=True)
    try:
        tables = con.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        virtual = [n for n, sql in tables if (sql or "").upper().startswith("CREATE VIRTUAL")]
        out = {}
        for name, sql in tables:
            if (name in DERIVED or name in virtual or name.startswith("sqlite_")
                    or any(name.startswith(v + "_") for v in virtual)):
                continue
            order = "" if "WITHOUT ROWID" in (sql or "").upper() else " ORDER BY rowid"
            h, n = hashlib.sha256(), 0
            for row in con.execute(f'SELECT * FROM "{name}"{order}'):
                h.update(repr(row).encode())
                n += 1
            out[name] = [n, h.hexdigest()]
        return out
    finally:
        con.close()


def _changed(fp, base):
    return sorted(t for t in set(fp) | set(base or {}) if fp.get(t) != (base or {}).get(t))


def close_store(db):
    """Raise Refused unless no other process has `db` open. Leaves the store
    checkpointed in rollback-journal mode (no -wal/-shm); memory.connect
    switches it back to WAL on the next open."""
    con = sqlite3.connect(db, timeout=0)
    try:
        mode = con.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
    except sqlite3.OperationalError as e:
        raise Refused(f"{db} is open in another process ({e}). Stop everything that uses it "
                      "(proxy, runners and `ctl run` tasks, overseer, Telegram bridge, viz) and retry") from e
    finally:
        con.close()
    if mode != "delete":
        raise Refused(f"{db} stayed in journal mode {mode!r}; is another process using it?")


def _logs(src, dst, pull):
    if not os.path.isdir(src):
        return {"ok": True, "summary": "absent"}
    # pull: only files newer than ours; backup.log is each computer's own
    flags = ["/E", "/XO", "/XF", "backup.log"] if pull else ["/E"]
    ok, summary = backup.robocopy(src, dst, [], flags)
    return {"ok": ok, "summary": summary}


def _put_telegram(src, d, owner):
    """Copy the Telegram bridge config (bot token, paired chat) to the share.
    Returns its sha256 for owner.json; without a local file the share keeps
    the last pushed one."""
    if not src or not os.path.exists(src):
        return (owner or {}).get("telegram_sha256")
    os.makedirs(d, exist_ok=True)
    part = os.path.join(d, TELEGRAM_NAME + ".part")
    shutil.copyfile(src, part)
    os.replace(part, os.path.join(d, TELEGRAM_NAME))
    return backup.sha256_file(src)


def _get_telegram(d, sha, dst):
    """Install the share's Telegram config at dst; a different local one is kept as .prev."""
    if not sha or not dst:
        return "not on the share"
    src = os.path.join(d, TELEGRAM_NAME)
    if not os.path.exists(src) or backup.sha256_file(src) != sha:
        raise RuntimeError(f"{src} is missing or doesn't match the sha256 in {OWNER}; push again")
    if os.path.exists(dst) and backup.sha256_file(dst) == sha:
        return "unchanged"
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    part = dst + ".part"
    shutil.copyfile(src, part)
    if os.path.exists(dst):
        os.replace(dst, dst + ".prev")
    os.replace(part, dst)
    return "installed"


def push(dest, db=DB, state_path=STATE, me=None, force=False, logs_dir=LOGS, telegram=TELEGRAM):
    me = me or this_host()
    if not os.path.exists(db):
        raise Refused(f"no memory store at {db}")
    owner, state = read_owner(dest), _read_json(state_path)
    gen = owner["gen"] if owner else 0
    if owner and not force:
        if owner.get("holder") not in (None, me):
            raise Refused(f"{owner['holder']} holds generation {gen}; pushing would overwrite what it "
                          f"plays there. Push on {owner['holder']}, then pull here (or push --force)")
        if not state or state.get("gen") != gen:
            raise Refused(f"this computer's store isn't based on generation {gen} (its last sync: "
                          f"{state and state.get('gen')}); pull first (or push --force)")
    close_store(db)
    out = {"host": me}
    d = os.path.join(dest, SUB)
    if owner and not force and fingerprint(db) == owner["fingerprint"]:
        owner.update(holder=None, pushed_by=me, pushed_t=_now(), telegram_sha256=_put_telegram(telegram, d, owner))
        _write_json(os.path.join(d, OWNER), owner)
        out.update(gen=gen, uploaded=False, why="unchanged since that generation; released it")
    else:
        os.makedirs(d, exist_ok=True)
        name = f"harness-gen{gen + 1:04d}.db.gz"
        with tempfile.TemporaryDirectory() as td:
            tmp = os.path.join(td, "snap.db")
            backup.consistent_copy(db, tmp)
            fp, sha, size = fingerprint(tmp), backup.sha256_file(tmp), os.path.getsize(tmp)
            part = os.path.join(d, name + ".part")
            with open(tmp, "rb") as fin, open(part, "wb") as raw, \
                    gzip.GzipFile(filename="harness.db", mode="wb", fileobj=raw, mtime=0) as gz:
                shutil.copyfileobj(fin, gz, 1 << 20)
            os.replace(part, os.path.join(d, name))
        tg = _put_telegram(telegram, d, owner)
        # owner.json last: until it names the new file, the previous generation stays the valid one
        _write_json(os.path.join(d, OWNER), {
            "gen": gen + 1, "file": name, "sha256": sha, "size": size, "fingerprint": fp,
            "telegram_sha256": tg, "holder": None, "pushed_by": me, "pushed_t": _now(), "pulled_t": None})
        for n in os.listdir(d):
            if n.startswith("harness-gen") and n != name:
                os.remove(os.path.join(d, n))
        _write_json(state_path, {"gen": gen + 1, "fingerprint": fp, "t": _now()})
        out.update(gen=gen + 1, uploaded=True, file=name, mb=round(os.path.getsize(os.path.join(d, name)) / 1e6, 1))
        out["history"] = backup.snapshot_db(db, os.path.join(dest, "db"), datetime.datetime.now())
    if logs_dir:
        out["logs"] = _logs(logs_dir, os.path.join(dest, "logs"), pull=False)
    return out


def pull(dest, db=DB, state_path=STATE, me=None, discard_local=False, force=False, logs_dir=LOGS,
         telegram=TELEGRAM):
    me = me or this_host()
    owner = read_owner(dest)
    if owner is None:
        raise Refused(f"nothing to pull from {dest}: push on the computer that has the store")
    gen, holder = owner["gen"], owner.get("holder")
    if holder == me:
        return {"host": me, "gen": gen, "installed": False, "why": "this computer already holds it"}
    if holder is not None and not force:
        raise Refused(f"{holder} holds generation {gen} (pulled {owner.get('pulled_t')}); push there first. "
                      f"If {holder} is gone, pull --force installs generation {gen} "
                      f"(pushed {owner.get('pushed_t')}), losing what was played there since")
    state = _read_json(state_path)
    out = {"host": me, "gen": gen}
    install = True
    if os.path.exists(db):
        close_store(db)
        fp = fingerprint(db)
        if fp == owner["fingerprint"]:
            install = False
        else:
            base = state["fingerprint"] if state else None
            changed = _changed(fp, base)
            if changed and not discard_local:
                since = (f"its last sync (generation {state['gen']})" if state
                         else "ever: it was never pushed or pulled")
                raise Refused(f"this computer's store changed since {since} in {', '.join(changed)}; "
                              f"installing generation {gen} would drop that. pull --discard-local "
                              f"installs it anyway and keeps this store as {os.path.basename(db)}.prev")
    if install:
        out["prev"] = _install(os.path.join(dest, SUB, owner["file"]), owner["sha256"], db)
    out["installed"] = install
    out["telegram"] = _get_telegram(os.path.join(dest, SUB), owner.get("telegram_sha256"), telegram)
    owner.update(holder=me, pulled_t=_now())
    _write_json(os.path.join(dest, SUB, OWNER), owner)
    _write_json(state_path, {"gen": gen, "fingerprint": owner["fingerprint"], "t": _now()})
    if logs_dir:
        out["logs"] = _logs(os.path.join(dest, "logs"), logs_dir, pull=True)
    return out


def _install(src, sha, db):
    """Unpack the snapshot next to `db`, verify it, swap it in. Returns the kept old store or None."""
    os.makedirs(os.path.dirname(os.path.abspath(db)), exist_ok=True)
    part = db + ".pull.part"
    try:
        with gzip.open(src, "rb") as fin, open(part, "wb") as fout:
            shutil.copyfileobj(fin, fout, 1 << 20)
        if backup.sha256_file(part) != sha:
            raise RuntimeError(f"{src} doesn't match the sha256 in {OWNER}")
        con = sqlite3.connect(part)
        try:
            check = con.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            con.close()
        if check != "ok":
            raise RuntimeError(f"{src} failed quick_check: {check}")
        prev = None
        if os.path.exists(db):
            prev = db + ".prev"
            os.replace(db, prev)
        for ext in ("-wal", "-shm"):
            if os.path.exists(db + ext):
                os.remove(db + ext)
        os.replace(part, db)
        return prev
    finally:
        if os.path.exists(part):
            os.remove(part)


def status(dest, db=DB, state_path=STATE, me=None):
    me = me or this_host()
    owner, state = read_owner(dest), _read_json(state_path)
    out = {"host": me, "dest": dest,
           "local": {"store": os.path.exists(db), "last_sync_gen": state and state.get("gen")}}
    fp = fingerprint(db) if os.path.exists(db) else None
    if fp is not None:
        out["local"]["changed_since_sync"] = _changed(fp, state and state.get("fingerprint")) if state else "never synced"
    if owner is None:
        out["next"] = "no handoff yet: push on the computer that has the store"
        return out
    out["share"] = {k: owner.get(k) for k in ("gen", "holder", "pushed_by", "pushed_t", "pulled_t", "size")}
    out["share"]["telegram"] = bool(owner.get("telegram_sha256"))
    holder = owner.get("holder")
    if holder == me:
        out["next"] = f"this computer holds generation {owner['gen']}: play here, push before you leave"
    elif holder:
        out["next"] = f"{holder} holds generation {owner['gen']}: push there, then pull here"
    else:
        out["next"] = f"generation {owner['gen']} is released (pushed by {owner.get('pushed_by')}): pull to play here"
        if fp is not None and fp != owner["fingerprint"] and (not state or _changed(fp, state.get("fingerprint"))):
            out["next"] += "; this store has unsynced changes, so pull will refuse (see --discard-local)"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="hand the memory store between computers via the NAS")
    ap.add_argument("cmd", choices=["status", "push", "pull"])
    ap.add_argument("--dest", default=backup.DEFAULT_DEST)
    ap.add_argument("--no-logs", action="store_true", help="don't copy logs/ to or from the share")
    ap.add_argument("--force", action="store_true",
                    help="push: even if this computer isn't the holder or behind; pull: even if another computer holds it")
    ap.add_argument("--discard-local", action="store_true",
                    help="pull: replace a store with unsynced changes (kept as harness.db.prev)")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.dest):
        print(f"refused: share unreachable: {a.dest}", file=sys.stderr)
        return 1
    logs = None if a.no_logs else LOGS
    try:
        if a.cmd == "status":
            out = status(a.dest)
        elif a.cmd == "push":
            out = push(a.dest, force=a.force, logs_dir=logs)
        else:
            out = pull(a.dest, discard_local=a.discard_local, force=a.force, logs_dir=logs)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1))
    return 0 if all(v.get("ok", True) for v in out.values() if isinstance(v, dict)) else 1


if __name__ == "__main__":
    sys.exit(main())
