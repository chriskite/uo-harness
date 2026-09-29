"""Reference MovementDriver — build server-accepted Outlands walk packets.

Ground truth: docs/MOVEMENT.md (code evidence + capture analysis).

Model (mirrors the Outlands ClassicUO fork's WalkerManager + the server's
RunUO-lineage MovementReq with Outlands' live fastwalk-token gate):

  seq  : u8 counter. 0 at login, +1 per walk, wraps 0xFF -> 1 (never 0).
         Reset to 0 by: login, S2C DenyWalk (0x21), S2C BF sub1 (fastwalk
         seed, which also teleports/resets the walker client-side).
         The server's expectation mirrors this counter; a client resync
         (C2S 22 0000) resets the SERVER expectation to 0.
  key  : fastwalk movement token. Server pushes tokens via S2C BF sub1
         (seed 5 slots + seq reset) and BF sub2 (push one token, no reset).
         Every walk pops the first nonzero slot (FIFO of 5); 0 when empty.
         While the server has an outstanding (unconsumed) token, the next
         walk MUST carry it; with none outstanding the key must be 0.
         Violations get silent direction-only processing (turn, no step).

Feed the driver the decoded session stream (observed client walks keep the
mirror honest; token pushes arrive via on_bf_seed/on_bf_push). Then call
build_walk() for a byte-exact, accepted packet (pre-XOR plaintext).
"""

from __future__ import annotations

import json
import struct
import sys

RUN_FLAG = 0x80
MAX_TOKEN_SLOTS = 5  # fork's FastWalkStack holds 5 keys (upstream had 6)


class MovementDriver:
    def __init__(self):
        self.seq = 0                    # WalkerManager.WalkSequence
        self.tokens = [0] * MAX_TOKEN_SLOTS  # FastWalkStack: 5 fixed slots
        self.walks_sent = 0

    # ------------------------------------------------------------------ events

    def on_login(self):
        """New session / character select: fresh WalkerManager."""
        self.seq = 0
        self.tokens = [0] * MAX_TOKEN_SLOTS
        self.walks_sent = 0

    def on_bf_seed(self, keys: list[int]):
        """S2C BF 001d 0001 <6 x u32be>: seed slots 0..4 (6th dropped),
        then OnPlayerTeleport -> walker reset (seq = 0)."""
        seeded = [k & 0xFFFFFFFF for k in keys[:MAX_TOKEN_SLOTS]]
        seeded += [0] * (MAX_TOKEN_SLOTS - len(seeded))
        self.tokens = seeded
        self.seq = 0

    def on_bf_push(self, key: int):
        """S2C BF 0009 0002 <u32be>: add one token to the first empty slot
        (AddValue semantics: full stack -> push is dropped)."""
        key &= 0xFFFFFFFF
        for i in range(MAX_TOKEN_SLOTS):
            if self.tokens[i] == 0:
                self.tokens[i] = key
                return

    def on_deny_walk(self):
        """S2C 0x21 DenyWalk: ClearSteps + WalkerManager.Reset."""
        self.seq = 0

    def on_client_walk(self, direction: int, seq: int, key: int):
        """Observed a walk packet from the real client: mirror its effects
        (seq post-increment with 0xFF -> 1 wrap; token pop if it used one)."""
        self.seq = 1 if seq == 0xFF else seq + 1
        if key != 0:
            self._pop_token(key)

    def _pop_token(self, key: int):
        """GetValue semantics: zero the first nonzero slot if it matches."""
        for i in range(MAX_TOKEN_SLOTS):
            if self.tokens[i] != 0:
                if self.tokens[i] == key:
                    self.tokens[i] = 0
                return

    def on_resync_sent(self):
        """Client sent 22 0000. Client seq is NOT reset; the SERVER's
        expectation becomes 0. Caller should treat next build_walk as
        requiring seq == 0 (the driver does not auto-reset; the client
        wouldn't). Use server_expected_seq() to see the divergence."""
        pass  # marker event; see server_expected_seq note in docs

    # ------------------------------------------------------------------ queries

    @property
    def token_outstanding(self) -> bool:
        return any(self.tokens)

    def current_token(self) -> int:
        """Token the next walk must carry (0 when none outstanding)."""
        for t in self.tokens:
            if t != 0:
                return t
        return 0

    # ------------------------------------------------------------------ build

    def build_walk(self, direction: int, run: bool) -> bytes:
        """Next accepted walk packet (plaintext, pre-XOR).

        02 <dir|0x80 if run> <seq u8> <key u32be>
        """
        direction &= 0xFF
        d = direction | (RUN_FLAG if run else 0)
        key = self.current_token()
        pkt = struct.pack(">BBB I", 0x02, d, self.seq & 0xFF, key)
        # effects: consume token, advance seq (wrap 0xFF -> 1)
        if key != 0:
            self.tokens[self.tokens.index(key)] = 0
        self.seq = 1 if self.seq == 0xFF else self.seq + 1
        self.walks_sent += 1
        return pkt

    def will_step(self) -> tuple[bool, str]:
        """Precondition check for the packet build_walk() would emit now."""
        if self.token_outstanding:
            return True, f"carrying outstanding token {self.current_token()}"
        return True, "no token outstanding; key=0 accepted"


# ---------------------------------------------------------------------------
# Replay validation: feed a session jsonl, mirror the client's own walks, and
# verify the driver predicts every observed client walk's seq (and, given the
# externally observed token values, its key). Token pushes are not visible in
# the lossy S2C decode (docs/MOVEMENT.md §3), so they are injected at the
# timestamps where the client demonstrably consumed them.

def _load_c2s_walks(path):
    walks = []
    for line in open(path, encoding="utf-8"):
        d = json.loads(line)
        if d.get("dir") == "c2s" and d.get("id") == "0x02":
            h = bytes.fromhex(d["hex"])
            walks.append((d["t"], h[1], h[2], int.from_bytes(h[3:7], "big")))
    return walks


def replay(path, runs):
    """runs: list of (t_start, t_end, [token pushes as (t, key, is_seed)])
    describing client-driven movement runs to mirror."""
    walks = _load_c2s_walks(path)
    drv = MovementDriver()
    drv.on_login()
    errors = []
    pushes = sorted(p for run in runs for p in run[2])
    for run_start, run_end, _ in runs:
        sel = [w for w in walks if run_start <= w[0] <= run_end]
        # seq resets at each run start after a seed/reset event:
        for t, direction, seq, key in sel:
            while pushes and pushes[0][0] <= t:
                pt, pkey, is_seed = pushes.pop(0)
                if is_seed:
                    drv.on_bf_seed([pkey])
                else:
                    drv.on_bf_push(pkey)
            # predict: driver's current seq must equal observed seq
            if drv.seq != seq:
                errors.append(f"t={t:.3f} seq: predicted {drv.seq:#04x}, observed {seq:#04x}")
            if key != 0 and drv.current_token() != key:
                errors.append(f"t={t:.3f} key: predicted {drv.current_token()}, observed {key}")
            pkt = drv.build_walk(direction & 0x7F, bool(direction & 0x80))
            if pkt != bytes([0x02, direction, seq]) + key.to_bytes(4, "big"):
                errors.append(f"t={t:.3f} bytes: built {pkt.hex()}, observed 02{direction:02x}{seq:02x}{key:08x}")
    return errors


REPLAY_CASES = {
    # session 141253: single human movement stream, seq 00..0x53 continuous,
    # login token 8 consumed on walk 0.
    "logs/session_20260928_141253.jsonl": [
        (1790622873.0, 1790623162.0, [(1790622873.0, 8, True)]),
    ],
    # session 164548: seq 00..0xa4 continuous across 45 s idles, login token 8.
    "logs/session_20260928_164548.jsonl": [
        (1790631969.0, 1790632065.0, [(1790631969.0, 8, True)]),
    ],
    # session 223537 human runs: run2 seq 00..03 (token 1 push), run3 CONTINUES
    # seq 04..08 and carries token 1 again on its first walk (BF sub2 push,
    # no reset) -- the decisive mid-burst-push case.
    "logs/session_20260928_223537.jsonl": [
        (1790653355.0, 1790653357.0, [(1790653355.0, 1, False)]),
        (1790653394.0, 1790653397.0, [(1790653394.0, 1, False)]),
    ],
    # session 211622 human runs: seq resets to 0 at each run start (seed-style
    # reset), tokens 8,1,8,8,1 on the first walk of each run.
    "logs/session_20260928_211622.jsonl": [
        (1790648757.9, 1790648760.0, [(1790648757.9, 8, True)]),
        (1790649041.7, 1790649044.5, [(1790649041.7, 1, True)]),
        (1790649120.9, 1790649122.5, [(1790649120.9, 8, True)]),
        (1790650503.4, 1790650506.0, [(1790650503.4, 8, True)]),
        (1790651816.9, 1790651819.0, [(1790651816.9, 1, True)]),
    ],
}


def main():
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    total_err = 0
    for rel, runs in REPLAY_CASES.items():
        path = os.path.join(root, rel)
        errs = replay(path, runs)
        n_walks = sum(len([w for w in _load_c2s_walks(path) if r[0] <= w[0] <= r[1]]) for r in runs)
        status = "OK " if not errs else "FAIL"
        print(f"[{status}] {rel}: {n_walks} client walks mirrored")
        for e in errs:
            print("   ", e)
        total_err += len(errs)
    # unit: wrap rule 0xFF -> 1
    drv = MovementDriver()
    drv.seq = 0xFF
    pkt = drv.build_walk(2, True)
    assert pkt == bytes.fromhex("0282ff00000000"), pkt.hex()
    assert drv.seq == 1, drv.seq
    # unit: token pop + key=0 continuations
    drv = MovementDriver()
    drv.on_bf_seed([8, 0, 0, 0, 0, 0])
    assert drv.build_walk(2, True) == bytes.fromhex("02820000000008")
    assert drv.build_walk(2, True) == bytes.fromhex("02820100000000")
    drv.on_bf_push(1)
    assert drv.build_walk(2, False) == bytes.fromhex("02020200000001")
    print("[OK ] unit: wrap 0xFF->1, seed/pop semantics, sub2 push mid-burst")
    sys.exit(1 if total_err else 0)


if __name__ == "__main__":
    main()
