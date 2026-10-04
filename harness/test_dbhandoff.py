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
  7. telegram.json travels with the store: installed on pull, a different local
     one kept as .prev, a computer without one gets the share's.
  8. The Laya checkpoint folder travels: uploaded only when its content
     changed (also on a push that only releases), verified and installed on
     pull with a different local one kept as .prev, unchanged ones skipped; a
     corrupted file on the share is refused and installs nothing.
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
        A = {"db": os.path.join(td, "a", "harness.db"), "state_path": os.path.join(td, "a", "handoff.json"),
             "telegram": os.path.join(td, "a", "telegram.json"), "model": os.path.join(td, "a", "laya-triage"),
             "me": "A"}
        B = {"db": os.path.join(td, "b", "harness.db"), "state_path": os.path.join(td, "b", "handoff.json"),
             "telegram": os.path.join(td, "b", "telegram.json"), "model": os.path.join(td, "b", "laya-triage"),
             "me": "B"}
        kw = {"logs_dir": None}

        def tg(who, content=None):
            if content is not None:
                with open(who["telegram"], "w", encoding="utf-8") as f:
                    f.write(content)
            with open(who["telegram"], encoding="utf-8") as f:
                return f.read()

        def ckpt(who, weights=None):
            """Write (weights given) or read a two-file checkpoint, one file nested like encoder/config.json."""
            w, c = os.path.join(who["model"], "model.safetensors"), os.path.join(who["model"], "encoder", "config.json")
            if weights is not None:
                os.makedirs(os.path.dirname(c), exist_ok=True)
                with open(w, "wb") as f:
                    f.write(weights)
                with open(c, "w", encoding="utf-8") as f:
                    f.write('{"layers": 28}')
            if not os.path.exists(w):
                return None
            with open(w, "rb") as f, open(c, encoding="utf-8") as g:
                return f.read(), g.read()

        con = chat(A["db"], "one", keep_open=True)
        con.execute("PRAGMA wal_autocheckpoint=0")
        con.execute("INSERT INTO chat(t, role, kind, text, data) VALUES (0, 'user', 'message', 'in-wal', '{}')")
        con.commit()
        why = refused(h.push, nas, **A, **kw)
        check(why is not None and "open in another process" in why, f"push refused while the store is open: {why}")
        check(h.read_owner(nas) is None, "a refused push writes nothing")
        con.close()
        tg(A, '{"token": "t1", "chat_id": 1}')
        ckpt(A, b"weights-v2" * 1000)

        check(h.not_held_here(nas, "A") is None and h.not_held_here(nas, "B") is None,
              "handoff never used: both computers back up")
        r = h.push(nas, **A, **kw)
        check(r["gen"] == 1 and r["uploaded"], f"bootstrap push is generation 1: {r}")
        check(r["model"].startswith("uploaded") and h.read_owner(nas)["model_sha256"]
              and sorted(h.read_owner(nas)["model_files"]) == ["encoder/config.json", "model.safetensors"],
              f"the checkpoint goes up with its manifest: {r['model']}")
        check(not os.path.exists(A["db"] + "-wal"), "pushed store is checkpointed (no -wal)")
        check(h.not_held_here(nas, "A") is not None, "released: the pusher stops backing up the store")

        r = h.pull(nas, **B, **kw)
        check(r["installed"] and texts(B["db"]) == ["one", "in-wal"], f"B installs gen 1 with the WAL row: {r}")
        check(r["telegram"] == "installed" and tg(B) == tg(A), f"B gets A's telegram.json: {r['telegram']}")
        check(r["model"] == "installed" and ckpt(B) == ckpt(A), f"B gets A's checkpoint, nested file included: {r['model']}")
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
        check(r["telegram"] == "unchanged", f"same telegram.json stays: {r['telegram']}")
        check(r["model"] == "unchanged", f"same checkpoint isn't copied again: {r['model']}")

        con = memory.connect(B["db"])  # derived rows: no change
        con.execute("INSERT INTO knowledge_vec(id, hash, vec) VALUES (1, 'x', x'00')")
        con.commit()
        con.close()
        ckpt(B, b"weights-v3" * 1000)  # retrained on B
        r = h.push(nas, **B, **kw)
        check(r["uploaded"] is False, "a knowledge_vec row isn't a change")
        check(r["model"].startswith("uploaded"), f"a retrained checkpoint goes up even when the store only releases: {r['model']}")
        h.pull(nas, **B, **kw)

        chat(B["db"], "two")
        tg(B, '{"token": "t1", "chat_id": 2}')  # re-paired on B
        chat(A["db"], "fork")  # A plays on generation 1 without holding it
        r = h.push(nas, **B, **kw)
        check(r["gen"] == 2 and r["uploaded"], f"B pushes generation 2: {r}")
        check(r["model"] == "unchanged", f"B's checkpoint is already on the share: {r['model']}")
        why = refused(h.push, nas, **A, **kw)
        check(why is not None and "pull first" in why, f"A's push from generation 1 is refused: {why}")
        why = refused(h.pull, nas, **A, **kw)
        check(why is not None and "chat" in why and "--discard-local" in why, f"fork refused, names the table: {why}")
        check(texts(A["db"]) == ["one", "in-wal", "fork"], "a refused pull keeps A's store")
        r = h.pull(nas, **A, discard_local=True, **kw)
        check(r["installed"] and texts(A["db"]) == ["one", "in-wal", "two"], f"--discard-local installs gen 2: {r}")
        check(texts(A["db"] + ".prev") == ["one", "in-wal", "fork"], "the discarded store is kept as .prev")
        check(r["telegram"] == "installed" and tg(B) == tg(A) and '"chat_id": 1' in tg({"telegram": A["telegram"] + ".prev"}),
              "B's re-paired telegram.json replaces A's, which is kept as .prev")
        check(r["model"] == "installed" and ckpt(A) == ckpt(B)
              and ckpt({"model": A["model"] + ".prev"})[0] == b"weights-v2" * 1000,
              f"B's retrained checkpoint replaces A's, which is kept as .prev: {r['model']}")

        why = refused(h.pull, nas, **B, **kw)
        check(why is not None and why.startswith("A holds"), "B can't pull while A holds")
        os.remove(B["telegram"])
        r = h.pull(nas, **B, force=True, **kw)
        check(r["installed"] is False and h.read_owner(nas)["holder"] == "B",
              f"pull --force takes the store (B's copy already is gen 2): {r}")
        check(r["telegram"] == "installed" and '"chat_id": 2' in tg(B), "a computer without telegram.json gets the share's")
        check(sorted(os.listdir(os.path.join(nas, "handoff"))) == ["harness-gen0002.db.gz", "model", "owner.json", "telegram.json"],
              "only the newest generation stays on the share, next to the checkpoint")

        with open(os.path.join(nas, "handoff", "model", "model.safetensors"), "r+b") as f:
            f.write(b"X")  # a damaged copy on the share
        C = {"db": os.path.join(td, "c", "harness.db"), "state_path": os.path.join(td, "c", "handoff.json"),
             "telegram": os.path.join(td, "c", "telegram.json"), "model": os.path.join(td, "c", "laya-triage"),
             "me": "C"}
        try:
            h.pull(nas, **C, force=True, **kw)
            err = None
        except RuntimeError as e:
            err = str(e)
        check(err is not None and "doesn't match" in err, f"a corrupted checkpoint on the share is refused: {err}")
        check(not os.path.exists(C["model"]) and not os.path.exists(C["model"] + ".pull.part")
              and not os.path.exists(C["db"]) and h.read_owner(nas)["holder"] == "B",
              "the refused pull installed nothing, left no partial copy and didn't take the store")

    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
