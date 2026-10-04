"""Back up the harness data that git doesn't hold and that can't be recreated
easily to the NAS share (docs/NOTES.md "Backups").

What goes where under DEST (default \\\\STARGAZER\\files\\uo-harness, which is
drive F: when mapped interactively; scheduled tasks don't see drive mappings,
so the default is the UNC path):

  db/harness-YYYYMMDD-HHMMSS.db.gz   consistent snapshot of harness/data/harness.db
                                     (SQLite online backup API, so the proxy can
                                     keep writing; quick_check before it ships;
                                     skipped when identical to the last one).
                                     Retention: every snapshot from the last
                                     KEEP_ALL_HOURS, then the newest per day.
                                     Skipped on a computer that doesn't hold
                                     the store once harness/dbhandoff.py is in
                                     use (dbhandoff.not_held_here).
  discord/discord-YYYYMMDD-HHMMSS.db.gz
                                     the same for harness/data/discord.db, the
                                     captured Discord history (harness/discord_capture.py);
                                     skipped while that DB doesn't exist.
  discord_kb/discordkb-YYYYMMDD-HHMMSS.db.gz
                                     the same for harness/data/discord_kb.db, the
                                     claims and facts harness/discord_kb.py mined from
                                     the Discord history (paid LLM output, so costly
                                     to regenerate); skipped while it doesn't exist.
                                     discord_vec.db is not backed up: it regenerates.
  logs/                              logs/ (session captures, screens, overseer
                                     task logs). Additive: files deleted locally
                                     stay on the share.
  artifacts/                         repo-root raw captures (*.pcapng, *.etl),
                                     screenshots (*.png), divert.log. Additive.
  ghidra/                            the Ghidra project (hours of analysis to
                                     redo). Mirrored, so the copy stays a
                                     consistent project; skipped while absent (only
                                     the laptop has it), so a computer without it
                                     can't mirror the share's copy away.
  discord_media/                     image attachments downloaded by
                                     discord_capture.py (harness/data/discord_media).
                                     Additive; skipped while the folder doesn't exist.
  models/                            fine-tuned checkpoints (models/laya-triage, the
                                     deployed Laya triage model, ~843 MB; slow to
                                     retrain and not reproducible bit for bit).
                                     Additive, never older over newer (/XO), so a
                                     computer that hasn't pulled the newest one
                                     can't overwrite it; skipped while absent.
  last_backup.json                   result of the last run.

Not backed up: everything tracked in git (pushed to GitHub), test-run logs
(logs_test*/), downloads (ClassicUO.exe, tarballs, upstream trees, WinDivert),
outputs regenerable by repo scripts (strings dumps, mrt_map.json,
decompiled/), and credentials (settings.json, the Discord browser profile
harness/data/discord_profile/).

Restore a DB: gunzip a snapshot to harness/data/harness.db (or discord.db) with
its writer (proxy / discord_capture serve) stopped, and delete any stale -wal /
-shm next to it.

Usage: python harness/backup.py [--dest PATH]   (exit 1 if any part failed)
Scheduled hourly by register_backup_task.ps1.
"""

import argparse
import datetime
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DEST = r"\\STARGAZER\files\uo-harness"
DB = os.path.join(ROOT, "harness", "data", "harness.db")
DISCORD_DB = os.path.join(ROOT, "harness", "data", "discord.db")
# (live DB, dest subdir, snapshot prefix, required)
DBS = [
    (DB, "db", "harness", True),
    (DISCORD_DB, "discord", "discord", False),
    (os.path.join(ROOT, "harness", "data", "discord_kb.db"), "discord_kb", "discordkb", False),
]
LOG = os.path.join(ROOT, "logs", "backup.log")
KEEP_ALL_HOURS = 48

# (source dir relative to ROOT, dest subdir, robocopy file filters, robocopy mode flags, required)
TREES = [
    ("logs", "logs", [], ["/E"], True),
    (".", "artifacts", ["*.pcapng", "*.etl", "*.png", "divert.log"], [], True),
    ("ghidra", "ghidra", [], ["/MIR"], False),
    (os.path.join("harness", "data", "discord_media"), "discord_media", [], ["/E"], False),
    ("models", "models", [], ["/E", "/XO"], False),
]

SNAP_RE = re.compile(r"^([a-z]+)-(\d{8}-\d{6})\.db\.gz$")
SNAP_FMT = "%Y%m%d-%H%M%S"


def log(msg):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def consistent_copy(db, out):
    """Copy `db` to the new file `out` with the SQLite online backup API, so a
    writer may keep working. Rows still only in the WAL are included; `out` is
    in rollback-journal mode, so it stands alone without a -wal next to it.
    Raises RuntimeError when the copy fails PRAGMA quick_check."""
    # read-only: never checkpoints or otherwise writes the live store
    src = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro", uri=True)
    dst = sqlite3.connect(out)
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")
        check = dst.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        dst.close()
        src.close()
    if check != "ok":
        raise RuntimeError(f"snapshot failed quick_check: {check}")


def snapshot_db(db, dest_dir, now, prefix="harness"):
    """Write a gzipped consistent snapshot of `db` into dest_dir as
    <prefix>-YYYYMMDD-HHMMSS.db.gz. Returns the snapshot file name, or None
    when it equals the last one."""
    os.makedirs(dest_dir, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = os.path.join(td, "snap.db")
        consistent_copy(db, tmp)
        digest = sha256_file(tmp)
        latest_path = os.path.join(dest_dir, "latest.json")
        if os.path.exists(latest_path):
            with open(latest_path, encoding="utf-8") as f:
                latest = json.load(f)
            if latest.get("sha256") == digest and os.path.exists(
                    os.path.join(dest_dir, latest.get("file", ""))):
                return None
        name = f"{prefix}-{now.strftime(SNAP_FMT)}.db.gz"
        part = os.path.join(dest_dir, name + ".part")
        with open(tmp, "rb") as fin, open(part, "wb") as raw, \
                gzip.GzipFile(filename=f"{prefix}.db", mode="wb", fileobj=raw, mtime=0) as gz:
            shutil.copyfileobj(fin, gz, 1 << 20)
        os.replace(part, os.path.join(dest_dir, name))
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump({"file": name, "sha256": digest}, f)
        return name


def plan_prune(names, now, keep_all_hours=KEEP_ALL_HOURS):
    """Snapshot names to delete: keep every snapshot newer than keep_all_hours,
    and the newest snapshot of each calendar day (per prefix) before that.
    Names that aren't snapshot names are never returned."""
    cutoff = now - datetime.timedelta(hours=keep_all_hours)
    old = []
    for n in names:
        m = SNAP_RE.match(n)
        if m:
            t = datetime.datetime.strptime(m.group(2), SNAP_FMT)
            if t < cutoff:
                old.append((t, m.group(1), n))
    newest_per_day = {}
    for t, prefix, n in sorted(old):
        newest_per_day[(prefix, t.date())] = n
    keep = set(newest_per_day.values())
    return sorted(n for _, _, n in old if n not in keep)


def robocopy(src, dst, filters, flags):
    """Incremental copy (robocopy skips files with equal size and time).
    Returns (ok, summary)."""
    cmd = ["robocopy", src, dst, *filters, *flags,
           "/R:2", "/W:5", "/FFT", "/XJ", "/NP", "/NFL", "/NDL", "/NJH"]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = p.stdout.decode("oem" if os.name == "nt" else "utf-8", errors="replace")
    files = next((ln.strip() for ln in out.splitlines() if ln.strip().startswith("Files :")), "")
    # robocopy: 0-7 success (bit flags: copied/extra/mismatch), >= 8 failure
    return p.returncode < 8, f"rc={p.returncode} {files}" if p.returncode < 8 else out.strip()[-2000:]


def run(dest):
    now = datetime.datetime.now()
    t0 = time.time()
    results = {}
    if not os.path.isdir(dest):
        log(f"FAIL destination unreachable: {dest}")
        return 1

    import dbhandoff  # imports this module, so not at module level
    for db, sub, prefix, required in DBS:
        if not required and not os.path.exists(db):
            results[sub] = {"ok": True, "snapshot": None, "absent": True}
            continue
        if db == DB:
            try:
                other = dbhandoff.not_held_here(dest)
            except Exception as e:  # unreadable owner.json: don't risk a stale snapshot
                other = f"handoff owner unreadable: {e!r}"
            if other:
                results[sub] = {"ok": True, "snapshot": None, "skipped": other}
                continue
        try:
            dbdir = os.path.join(dest, sub)
            name = snapshot_db(db, dbdir, now, prefix)
            pruned = plan_prune(os.listdir(dbdir), now)
            for n in pruned:
                os.remove(os.path.join(dbdir, n))
            results[sub] = {"ok": True, "snapshot": name, "pruned": pruned}
        except Exception as e:  # one failed part must not stop the others
            results[sub] = {"ok": False, "error": repr(e)}

    for src, sub, filters, flags, required in TREES:
        src = os.path.normpath(os.path.join(ROOT, src))
        if not required and not os.path.isdir(src):
            results[sub] = {"ok": True, "summary": "absent"}
            continue
        ok, summary = robocopy(src, os.path.join(dest, sub), filters, flags)
        results[sub] = {"ok": ok, "summary": summary}

    ok = all(r["ok"] for r in results.values())
    status = {"time": now.isoformat(timespec="seconds"), "ok": ok,
              "seconds": round(time.time() - t0, 1), "results": results}
    try:
        with open(os.path.join(dest, "last_backup.json"), "w", encoding="utf-8") as f:
            json.dump(status, f, indent=1)
    except OSError as e:
        ok = False
        log(f"FAIL writing last_backup.json: {e!r}")
    log(("OK " if ok else "FAIL ") + json.dumps(results))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="back up harness data to the NAS")
    ap.add_argument("--dest", default=DEFAULT_DEST)
    return run(ap.parse_args().dest)


if __name__ == "__main__":
    sys.exit(main())
