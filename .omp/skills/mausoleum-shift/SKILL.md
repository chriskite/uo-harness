---
name: mausoleum-shift
description: Run Logan Wolf's Mausoleum crawl shifts through a Seer (overseer) subagent per docs/OVERSEER.md, while this session watches and fixes every issue the Seer or the user reports. Use when the user asks for Mausoleum runs, a Mausoleum shift, or to start the Seer for Logan Wolf.
---

# Mausoleum shift: the Seer plays, you fix

Two roles. **The Seer** (project agent `overseer`, `.omp/agents/overseer.md`) runs Logan Wolf's shift by
docs/OVERSEER.md §5 "Mausoleum crawl shifts": drop-off, loadout check, `ctl run hunt …`, `ctl wait`, run after run
until the user says stop. **You** (the main session) start it, watch it, and fix the code and docs behind every
issue it or the user reports. You never drive the game while it runs; it never edits code.

Read first: AGENTS.md (in context), docs/OVERSEER.md §5 "Mausoleum crawl shifts" and §6, docs/HUNT_LOOP.md "Pets",
"Recall in, recall out", "Crawl".

## 1. Before dispatch

1. Stack up and current: `python harness/stack.py status`. An `external … not ours` service is still old code
   (NOTES "Stack supervisor"); tell the user rather than restarting it.
2. `./ctl.cmd --char "Logan Wolf" status > /tmp/s.json`, then with `jq.exe … < /tmp/s.json`: `ok`, `pos`, `facet`,
   `hits`, `gate`, `tasks`, `open_junctures`, `overseer_heartbeat`, the pets (GraveRobber `0x000DDEFE`,
   AlaFlambe `0x0026EF56`) in `mobiles` within 3 tiles, `gumps_open`.
3. **One Seer per character.** `read history://`: a `SeerLoganWolf*` agent `running` (or a heartbeat under 90 s
   old from another session) already owns Logan; message it (`write agent://<id>`) instead of starting another.
   A parked one may be revived the same way when its context is still small; otherwise dispatch fresh.
4. Anything off (dead, pets missing, gate paused, a task running): tell the user and dispatch only once they say
   how to proceed. Alive inside the dungeon with no run: the Seer's first act is the go-home run (§5 step 7: the
   §5 command plus `--go-home`); say so in the dispatch. §5 step 1: start at home in Logan Wolf's house (the
   "Logan's House" landing).

## 2. Dispatch

One `task` call; `agent: "overseer"` goes **on the `tasks[]` item** (a top-level `agent` is ignored: OVERSEER.md
§6). Name `SeerLoganWolf`, with a number when `history://` lists that name from an earlier session (omp refuses
the repeat: "already owned by another session generation", 2026-10-22). Don't set `isolated`: ctl must run the
main checkout's code against the main store (§5 step 0).

`context`: the goal and the reporting contract below. `task`:

```
Act as the Seer for Logan Wolf: follow docs/OVERSEER.md §5. Goal: Mausoleum crawl shifts (§5 'Mausoleum crawl
shifts') until the user says stop.
State at dispatch (checked by the parent, <HH:MM>): <hits, pos, facet, at home or where, pets and their hits,
gold, gate, tasks, open junctures>.
Runner changes since the last shift (commits <shas>): <what each changes and the log lines it writes>.
<Any change to §5's command or procedure the user asked for, else "§5 verbatim".>
Reporting contract (the parent session fixes code; you never do):
- Report every issue to the parent at once with `write agent://Main`: a runner error or traceback, a run that
  did something §5 or the user's play rules say it must not, a stuck or confused run, a death, pet_down,
  pet_left_behind, a refused or failing ctl/act, anything the user reports as a bug in viz or Telegram chat
  (quote it with its chat id). Include: time, task id, log path, the exact log lines, Logan's pos/hits/pets,
  where he is now and what you did.
- Then keep the shift: get Logan safe by §5 (steps 7 and 8: `ctl stop --after-trip` unless it's an emergency),
  start no new hunt while the issue is open, and keep `ctl wait` going. A parent message wakes you; it says
  when the fix is in and what changed, and whether to resume.
- §5 step 7: an erroneous stop (task_failed) with Logan alive in the dungeon: get him home at once (the
  runner's own escape, else the go-home run: the §5 command plus `--go-home`); never leave him standing
  there waiting for an answer (user 2026-10-24). Deaths and pets lost go to the user too (`ctl say "@user …"`);
  start nothing until they answer.
- Yield your final report (what .omp/agents/overseer.md asks for) only when the shift ends.
```

## 3. Watch

Call `wait`. It returns on a message from the Seer, its end, the user, or a 30-minute cap.

- **Every return**, a read-only glance: `ctl --char "Logan Wolf" status` (hits, pos, `intent`, `tasks`,
  `overseer_heartbeat`), the tail of `logs/tasks/<task_id>.log`, `ctl chat --role overseer --after N` and
  `ctl junctures --after N` (keep N from the last glance). A Seer silent while a run is in trouble, a heartbeat
  older than 90 s, or a run idling ("Nothing to patrol to; waiting" outside the dungeon) is an issue too: message
  the Seer (`write agent://<id>`) or check `history://<id>` for what it is doing.
- **Never** `ctl wait` for Logan: it advances the Seer's shared cursors (`overseer_juncture_cursor`,
  `overseer_chat_cursor`) and the Seer misses those events. Never `ctl act`, `ctl run` or `ctl stop` while the
  Seer runs: have the Seer do it. Never fight by hand.
- The user may report to you here (steering) or to the Seer in viz/Telegram (it forwards). Treat both the same.
- The Seer yielded: read its report, fix what it lists (section 4), then dispatch a fresh Seer (section 2)
  unless the user ended the shift. Long shifts outgrow a Seer's context (§7): re-dispatch when its transcript
  gets long, between runs.

## 4. Fix

User reports are ground truth; don't re-verify them in game.

1. **Safety first.** Logan in danger or a run doing harm: tell the Seer what to do (`stop --after-trip`, or plain
   `stop` only for a GM, a server restriction or a run already somewhere wrong; §5 step 8). Anything needing
   hands in the client (a ghost, fetching pets, a walk out the go-home run couldn't make) is the user's: ask
   them. Logan alive in the dungeon after an erroneous stop: have the Seer start the go-home run at once (§5
   step 7), don't wait for an answer.
2. **Find the cause from evidence:** the task log, the session capture (`logs/session_*.jsonl`, `jq.exe -c
   'select(…)'`), the store's events, the Seer's transcript (`history://<id>`). Write `[INFERENCE]` on anything
   unobserved.
3. **Reproduce offline, then fix** in the main checkout: the module's own `harness/test_*.py`, root
   `test_loop_hunt.py` for the hunt runner, and for fight, crawl, leave or pet behaviour the Mausoleum simulator
   (`python harness/sim/run.py …`, the live-fight replays `harness/sim/fight1949.py` and `fight1822.py` (copy one
   for a new incident), `python harness/test_sim.py`; docs/SIM.md). Batches as live plays use `--others --eerie
   --prior <the main store>`; `harness/sim/parity.py` sets them beside the live runs. Before/after numbers from the
   simulator are the proof the user asks for before trying again in game. Keep a
   failing-before/passing-after test where it catches a plausible regression. Large fixes may go to a `task`
   subagent, not isolated.
4. **Record and push** (Rule 0): the finding in docs/NOTES.md (the run's section), the runner rule in
   docs/HUNT_LOOP.md, a changed Seer procedure in docs/OVERSEER.md §5, the decision in docs/PLAN.md. Commit only
   the fix's files (the user's uncommitted work stays out), push, and see the push succeed.
5. **Make it live.** Runner code (loop_hunt, tamer, crawl, aggro, escape, moongate, agent_link, …) loads at
   `ctl run`: the next run has it; a run in progress keeps the old code. ctl.py loads per call. Proxy and
   world-model code (`harness/world/`, the proxy, the state port) needs a proxy restart: ask the user, with
   Logan at home in the house and no run (after it `status.mobiles` is empty until the pets show again). §5 or
   overseer.md changes reach a running Seer only by message.
6. **Brief the Seer** (`write agent://<id>`): the commit, what changed, the log lines it writes now, whether to
   resume. Tell the user in one or two lines: the issue, the cause, the fix, the commit.

## 5. Ending

The user says stop: tell the Seer to end the shift (it runs `ctl stop --after-trip` and waits for `task_done` at
home), wait for its report, and pass the user its summary plus every fix made this shift with its commit.
