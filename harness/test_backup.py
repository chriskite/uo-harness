"""Backup tests (harness/backup.py, docs/NOTES.md "Backups").

  1. Retention: every snapshot inside the keep-all window survives, older ones
     keep exactly the newest per day and per DB prefix (harness-, discord-),
     foreign files are never deleted.
  2. Snapshot: rows still only in the live WAL make it into the snapshot, the
     snapshot restores (gunzip) to a standalone DB, an unchanged store writes
     no new snapshot, a changed one does.
  3. Discord DBs ship only from the capture computer (the one with the Discord
     login profile): a restored copy elsewhere never becomes a snapshot.
"""

import datetime
import gzip
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backup  # noqa: E402

FAILURES = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def test_retention():
    now = datetime.datetime(2026, 10, 10, 12, 0, 0)
    names = [
        "harness-20261010-110000.db.gz",  # 1 h old
        "harness-20261008-120001.db.gz",  # just inside 48 h
        "harness-20261008-115959.db.gz",  # just outside 48 h, newest of its day
        "harness-20261008-090000.db.gz",  # same day, older -> pruned
        "harness-20261001-230000.db.gz",  # newest of 10-01
        "harness-20261001-010000.db.gz",  # pruned
        "harness-20261001-000000.db.gz",  # pruned
        "latest.json",
        "harness-20260101-000000.db.gz.part",
        "notes.txt",
    ]
    got = backup.plan_prune(names, now, keep_all_hours=48)
    check(got == ["harness-20261001-000000.db.gz", "harness-20261001-010000.db.gz",
                  "harness-20261008-090000.db.gz"], f"prune plan {got}")
    check(backup.plan_prune([n for n in names if n not in got], now) == [],
          "pruning is idempotent")
    # the Discord capture DB's snapshots follow the same rule, independently
    mixed = ["harness-20261001-230000.db.gz", "discord-20261001-220000.db.gz",
             "discord-20261001-100000.db.gz"]
    got = backup.plan_prune(mixed, now, keep_all_hours=48)
    check(got == ["discord-20261001-100000.db.gz"],
          f"newest per day is kept per prefix: {got}")


def test_snapshot():
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "live.db")
        dest = os.path.join(td, "dest")
        live = sqlite3.connect(db)
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("PRAGMA wal_autocheckpoint=0")  # keep rows in the WAL only
        live.execute("CREATE TABLE t (v)")
        live.execute("INSERT INTO t VALUES ('in-wal')")
        live.commit()
        check(os.path.getsize(db + "-wal") > 0, "rows are in the live WAL")

        t1 = datetime.datetime(2026, 10, 1, 10, 0, 0)
        n1 = backup.snapshot_db(db, dest, t1)
        check(n1 == "harness-20261001-100000.db.gz", f"first snapshot written: {n1}")
        restored = os.path.join(td, "restored.db")
        with gzip.open(os.path.join(dest, n1), "rb") as f, open(restored, "wb") as out:
            out.write(f.read())
        r = sqlite3.connect(restored)
        check(r.execute("PRAGMA journal_mode").fetchone()[0] == "delete",
              "restored snapshot is standalone (rollback journal)")
        check(r.execute("SELECT v FROM t").fetchall() == [("in-wal",)],
              "restored snapshot has the WAL-only row")
        r.close()

        n2 = backup.snapshot_db(db, dest, t1 + datetime.timedelta(hours=1))
        check(n2 is None, "unchanged store: no new snapshot")

        live.execute("INSERT INTO t VALUES ('later')")
        live.commit()
        n3 = backup.snapshot_db(db, dest, t1 + datetime.timedelta(hours=2))
        check(n3 == "harness-20261001-120000.db.gz", f"changed store: new snapshot {n3}")
        live.close()

        n4 = backup.snapshot_db(db, os.path.join(td, "discord"), t1, prefix="discord")
        check(n4 == "discord-20261001-100000.db.gz", f"prefixed snapshot name: {n4}")


def test_discord_capture_only():
    saved = backup.DBS, backup.TREES, backup.DISCORD_PROFILE, backup.LOG
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "discord.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE m (v)")
        con.commit()
        con.close()
        dest = os.path.join(td, "dest")
        os.makedirs(dest)
        profile = os.path.join(td, "discord_profile")
        backup.DBS = [(db, "discord", "discord", False, True)]
        backup.TREES = []
        backup.DISCORD_PROFILE = profile
        backup.LOG = os.path.join(td, "backup.log")
        try:
            rc = backup.run(dest)
            check(rc == 0 and not os.path.exists(os.path.join(dest, "discord")),
                  "no Discord profile here: the Discord DB is not snapshotted")
            os.makedirs(profile)
            rc = backup.run(dest)
            snaps = os.listdir(os.path.join(dest, "discord")) if os.path.isdir(os.path.join(dest, "discord")) else []
            check(rc == 0 and any(n.startswith("discord-") for n in snaps),
                  f"capture computer: the Discord DB is snapshotted {snaps}")
        finally:
            backup.DBS, backup.TREES, backup.DISCORD_PROFILE, backup.LOG = saved


def main():
    test_retention()
    test_snapshot()
    test_discord_capture_only()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
