"""Memory store handoff tests (harness/dbhandoff.py, docs/NOTES.md "Two computers
(memory store handoff)"). Two computers A and B share one temp "NAS" folder.

  1. Push needs the store closed: an open connection elsewhere refuses it.
  2. Bootstrap: A pushes generation 1; rows still in A's WAL travel; B (no store
     yet) pulls it and holds it; backups run only on the holder.
  3. Ownership: while B holds it, A can neither push nor pull; pull --force can.
  4. Unchanged push releases without a new generation.
  5. Fork: A played on generation 1 while B made generation 2; A's pull refuses
     and names the table, --discard-local installs and keeps A's store as .prev.
  6. Derived tables (knowledge_vec, FTS shadows) don't count as changes.
"""

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dbhandoff as h  # noqa: E402
import memory  # noqa: E402

FAILURES = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def refused(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except h.Refused as e:
        return str(e)
    return None


def chat(db, text, keep_open=False):
    con = memory.connect(db)
    con.execute("INSERT INTO chat(t, role, kind, text, data) VALUES (0, 'user', 'message', ?, '{}')", (text,))
    con.commit()
    if keep_open:
        return con
    con.close()


def texts(db):
    con = sqlite3.connect(db)
    try:
        return [r[0] for r in con.execute("SELECT text FROM chat ORDER BY id")]
    finally:
        con.close()


def main():
    with tempfile.TemporaryDirectory() as td:
        nas = os.path.join(td, "nas")
        os.makedirs(nas)
        A = {"db": os.path.join(td, "a", "harness.db"), "state_path": os.path.join(td, "a", "handoff.json"), "me": "A"}
        B = {"db": os.path.join(td, "b", "harness.db"), "state_path": os.path.join(td, "b", "handoff.json"), "me": "B"}
        kw = {"logs_dir": None}

        con = chat(A["db"], "one", keep_open=True)
        con.execute("PRAGMA wal_autocheckpoint=0")
        con.execute("INSERT INTO chat(t, role, kind, text, data) VALUES (0, 'user', 'message', 'in-wal', '{}')")
        con.commit()
        why = refused(h.push, nas, **A, **kw)
        check(why is not None and "open in another process" in why, f"push refused while the store is open: {why}")
        check(h.read_owner(nas) is None, "a refused push writes nothing")
        con.close()

        check(h.not_held_here(nas, "A") is None and h.not_held_here(nas, "B") is None,
              "handoff never used: both computers back up")
        r = h.push(nas, **A, **kw)
        check(r["gen"] == 1 and r["uploaded"], f"bootstrap push is generation 1: {r}")
        check(not os.path.exists(A["db"] + "-wal"), "pushed store is checkpointed (no -wal)")
        check(h.not_held_here(nas, "A") is not None, "released: the pusher stops backing up the store")

        r = h.pull(nas, **B, **kw)
        check(r["installed"] and texts(B["db"]) == ["one", "in-wal"], f"B installs gen 1 with the WAL row: {r}")
        check(h.read_owner(nas)["holder"] == "B", "B holds the store")
        check(h.not_held_here(nas, "B") is None and "held by B" in (h.not_held_here(nas, "A") or ""),
              "only the holder backs up")
        check(h.pull(nas, **B, **kw)["installed"] is False, "pulling again on the holder is a no-op")

        why = refused(h.push, nas, **A, **kw)
        check(why is not None and why.startswith("B holds"), f"non-holder push refused: {why}")
        why = refused(h.pull, nas, **A, **kw)
        check(why is not None and why.startswith("B holds"), f"pull refused while B holds it: {why}")

        r = h.push(nas, **B, **kw)
        check(r["gen"] == 1 and r["uploaded"] is False, f"unchanged push only releases: {r}")
        r = h.pull(nas, **B, **kw)
        check(r["installed"] is False and h.read_owner(nas)["holder"] == "B", f"re-pull of own generation: {r}")

        con = memory.connect(B["db"])  # derived rows: no change
        con.execute("INSERT INTO knowledge_vec(id, hash, vec) VALUES (1, 'x', x'00')")
        con.commit()
        con.close()
        check(h.push(nas, **B, **kw)["uploaded"] is False, "a knowledge_vec row isn't a change")
        h.pull(nas, **B, **kw)

        chat(B["db"], "two")
        chat(A["db"], "fork")  # A plays on generation 1 without holding it
        r = h.push(nas, **B, **kw)
        check(r["gen"] == 2 and r["uploaded"], f"B pushes generation 2: {r}")
        why = refused(h.push, nas, **A, **kw)
        check(why is not None and "pull first" in why, f"A's push from generation 1 is refused: {why}")
        why = refused(h.pull, nas, **A, **kw)
        check(why is not None and "chat" in why and "--discard-local" in why, f"fork refused, names the table: {why}")
        check(texts(A["db"]) == ["one", "in-wal", "fork"], "a refused pull keeps A's store")
        r = h.pull(nas, **A, discard_local=True, **kw)
        check(r["installed"] and texts(A["db"]) == ["one", "in-wal", "two"], f"--discard-local installs gen 2: {r}")
        check(texts(A["db"] + ".prev") == ["one", "in-wal", "fork"], "the discarded store is kept as .prev")

        why = refused(h.pull, nas, **B, **kw)
        check(why is not None and why.startswith("A holds"), "B can't pull while A holds")
        r = h.pull(nas, **B, force=True, **kw)
        check(r["installed"] is False and h.read_owner(nas)["holder"] == "B",
              f"pull --force takes the store (B's copy already is gen 2): {r}")
        check(sorted(os.listdir(os.path.join(nas, "handoff"))) == ["harness-gen0002.db.gz", "owner.json"],
              "only the newest generation stays on the share")

    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
