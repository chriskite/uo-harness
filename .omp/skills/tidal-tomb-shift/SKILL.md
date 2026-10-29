---
name: tidal-tomb-shift
description: Run Logan Wolf's Tidal Tomb crawl shifts through a Seer (overseer) subagent per docs/OVERSEER.md, while this session watches and fixes every issue the Seer or the user reports. Takes the user's shift instructions, e.g. "only level 1" (`/skill:tidal-tomb-shift only level 1`), and turns them into the runner's command. Use when the user asks for Tidal Tomb (TT) runs, a Tidal Tomb shift, or to start the Seer for Logan Wolf in Tidal Tomb.
---

# Tidal Tomb shift: the Seer plays, you fix

Two roles. **The Seer** (project agent `overseer`, `.omp/agents/overseer.md`) runs Logan Wolf's shift by
docs/OVERSEER.md §5 "Mausoleum crawl shifts" with its Tidal Tomb commands ("Tidal Tomb level 1 instead", "Another
hunt level by the user's word"): drop-off, loadout check, `ctl run hunt …`, `ctl wait`, run after run until the user
says stop. **You** (the main session) start it, watch it, and fix the code and docs behind every issue it or the
user reports. You never drive the game while it runs; it never edits code.

Read first: AGENTS.md (in context), docs/OVERSEER.md §5 "Mausoleum crawl shifts" and §6, docs/HUNT_LOOP.md "Pets",
"In and out of a dungeon", "Crawl"; docs/NOTES.md "Tidal Tomb: …" sections; docs/SIM.md "Tidal Tomb".

## 0. The user's instructions

The user may add instructions after the skill name (`/skill:tidal-tomb-shift only level 1` arrives as
`User: only level 1`), in the request, or later while the shift runs. They hold for the whole shift, through every
re-dispatch, until the user changes them. None: level 1, the §5 "Tidal Tomb level 1 instead" command. Map each one
before dispatch:

1. **A level** ("only level 1", "level 2", "TT 3"): the level's command from docs/OVERSEER.md §5 "Another hunt level
   by the user's word"; "only" means every run of the shift on it. Each run hunts one level: the crawl stays on the
   floor it reached, and its "zones" are depth bands of that floor, not levels.

   | The user says | Level | State |
   |---|---|---|
   | level 1, nothing | TT 1 | The default; the runner's live runs since 2026-10-26. |
   | level 2 | TT 2 | Runnable: the runner has run it live (user 2026-10-29). The oxygen rule fights back to the arrival stairs and steps up to level 1 and down again before 732 s under water (the simulator: deprived runs 36 -> 4 of 48, deaths 14 -> 11; docs/HUNT_LOOP.md "Oxygen"). Simulator: 4 of 24 runs with a death (docs/SIM.md "Level 2's bedrocks"); 2026-10-27 seeds 4-5 both home (83k, 74k gp). |
   | level 3 | TT 3 | Never run live by the runner. Level 2 fought through on the way (past 732 s under water there in most simulated runs: no refill leg on that walk), the giant astral zoa at the arrival the main killer; on level 3 the oxygen rule fights to the nearest Recovery Dolphin. Simulator: 5 of 24 with a death; 2026-10-27 seeds 4-5 both died (seed 5 on level 1's walk at 70 s). |

   **Level 3:** before the first run, tell the user in one message what the table says for it and wait for their go;
   then dispatch with it. Their go stands for the shift. Level 2 needs no go (user 2026-10-29).
2. **A runner setting** ("no songs", "stop after 50 kills", "don't provoke", "never pull the zoa"): the flag from
   `python harness/loop_hunt.py --help` added to the level's command (`--no-songs`, `--kills 50`, `--no-provoke`,
   `--never-aggro "giant astral zoa"`, `--avoid-backlash`). The flags the level's command already has stay.
3. **A shift rule** ("three runs", "stop at 23:00", "home by midnight"): Seer procedure, written into the dispatch
   (no new run after the Nth; `ctl stop --after-trip` at the time). Pets are the user's to bring: the runner claims
   the tamed pets beside Logan at the start.
4. **Nothing maps** (no flag, no procedure): tell the user before dispatch that it needs runner work (section 4),
   and ask whether to start without it.

When you dispatch, tell the user the plan in one line: the level, the added flags, the shift rules.

## 1. Before dispatch

1. Stack up and current: `python harness/stack.py status`. An `external … not ours` service is still old code
   (NOTES "Stack supervisor"); tell the user rather than restarting it.
2. `./ctl.cmd --char "Logan Wolf" status > /tmp/s.json`, then with `jq.exe … < /tmp/s.json`: `ok`, `pos`, `facet`,
   `hits`, `gate`, `tasks`, `open_junctures`, `overseer_heartbeat`, Logan's pets in `mobiles` within 3 tiles
   (whichever the user brought; usually GraveRobber `0x000DDEFE` and AlaFlambe `0x0026EF56`), `gumps_open`.
3. **The rune.** Every Tidal Tomb command recalls in by "Tidal Tomb Inside" in the tome 0x444AB1F3 (the library tome
   "Bad Places II" at the guild house isn't one the runner can use). At home with no task running: `./ctl.cmd --char
   "Logan Wolf" act recall 0x444AB1F3 --check` must list that row and 2 charges at least. Missing: tell the user (the
   rune is theirs to add) and start nothing.
4. **One Seer per character.** `read history://`: a `SeerLoganWolf*` agent `running` (or a heartbeat under 90 s
   old from another session) already owns Logan, in the Mausoleum or here; message it (`write agent://<id>`) instead
   of starting another. A parked one may be revived the same way when its context is still small; otherwise
   dispatch fresh.
5. Anything off (dead, pets missing, gate paused, a task running): tell the user and dispatch only once they say
   how to proceed. Alive inside a dungeon with no run: the Seer's first act is the go-home run (§5 step 7) with the
   command of the level he stands on (§5 "Another hunt level": the levels' boxes; the Mausoleum's too) plus
   `--go-home`; say so in the dispatch. §5 step 1: start at home in Logan Wolf's house (the "Logan's House" landing).

## 2. Dispatch

One `task` call; `agent: "overseer"` goes **on the `tasks[]` item** (a top-level `agent` is ignored: OVERSEER.md
§6). Name `SeerLoganWolf`, with a number when `history://` lists that name from an earlier session (omp refuses
the repeat: "already owned by another session generation", 2026-10-22). Don't set `isolated`: ctl must run the
main checkout's code against the main store (§5 step 0).

`context`: the goal and the reporting contract below. `task`:

```
Act as the Seer for Logan Wolf: follow docs/OVERSEER.md §5. Goal: Tidal Tomb crawl shifts (§5 'Mausoleum crawl
shifts' with its Tidal Tomb commands: 'Tidal Tomb level 1 instead', 'Another hunt level by the user's word') until
the user says stop.
State at dispatch (checked by the parent, <HH:MM>): <hits, pos, facet, at home or where, pets and their hits,
gold, gate, tasks, open junctures; the tome's charges and its "Tidal Tomb Inside" row>.
Runner changes since the last shift (commits <shas>): <what each changes and the log lines it writes>.
Shift instructions from the user, verbatim: <"…", or "none">.
Command for every run, a restart inside and the go-home run: <the full ctl run hunt command of the level>.
<Shift rules from the instructions (run count, stop time), the user's go for level 3 with its time, and any
other change to §5's procedure the user asked for, else "§5 procedure verbatim".>
Reporting contract (the parent session fixes code; you never do):
- Report every issue to the parent at once with `write agent://Main`: a runner error or traceback, a run that
  did something §5 or the user's play rules say it must not, a stuck or confused run, a run on another level than
  the shift's, a death, pet_down, pet_left_behind, a refused or failing ctl/act, anything the user reports as a bug
  in viz or Telegram chat (quote it with its chat id). Include: time, task id, log path, the exact log lines,
  Logan's pos/hits/pets, where he is now and what you did.
- Then keep the shift: get Logan safe by §5 (steps 7 and 8: `ctl stop --after-trip` unless it's an emergency),
  start no new hunt while the issue is open, and keep `ctl wait` going. A parent message wakes you; it says
  when the fix is in and what changed, and whether to resume.
- §5 step 7: an erroneous stop (task_failed) with Logan alive in the dungeon: get him home at once (the
  runner's own escape, else the go-home run: the command above, of the level he stands on, plus `--go-home`);
  never leave him standing there waiting for an answer (user 2026-10-24). Deaths and pets lost go to the user too
  (`ctl say "@user …"`); start nothing until they answer.
- Yield your final report (what .omp/agents/overseer.md asks for) only when the shift ends.
```

## 3. Watch

Call `wait`. It returns on a message from the Seer, its end, the user, or a 30-minute cap.

- **Every return**, a read-only glance: `ctl --char "Logan Wolf" status` (hits, pos, `intent`, `tasks`,
  `overseer_heartbeat`), the tail of `logs/tasks/<task_id>.log`, `ctl chat --role overseer --after N` and
  `ctl junctures --after N` (keep N from the last glance). A Seer silent while a run is in trouble, a heartbeat
  older than 90 s, a run idling ("Nothing to patrol to; waiting" outside the dungeon), or Logan on another level than
  the shift's (the boxes in §5 "Another hunt level") is an issue too: message the Seer (`write agent://<id>`) or
  check `history://<id>` for what it is doing.
- **Levels 2 and 3:** the runner's oxygen rule (docs/HUNT_LOOP.md "Oxygen") fights its way to the easiest refill
  before 732 s under water: a "oxygen: to the dolphin/teleporter up ..." intent and an `oxygen` job event per leg are
  expected, not a new bug. Past 732 s anyway (a long fought leg, the walk across level 2 to level 3) Logan and the pets
  lose hits every 6 s down to 66 %, which no song or heal stops. A death there goes to the user like any other.
- **Never** `ctl wait` for Logan: it advances the Seer's shared cursors (`overseer_juncture_cursor`,
  `overseer_chat_cursor`) and the Seer misses those events. Never `ctl act`, `ctl run` or `ctl stop` while the
  Seer runs: have the Seer do it. Never fight by hand.
- The user may report to you here (steering) or to the Seer in viz/Telegram (it forwards). Treat both the same.
- **A new instruction mid-shift** (e.g. "only level 1 from now on"): map it as in section 0 and message the Seer
  the new command or rule. It takes effect at the next `ctl run`; a run in progress keeps its arguments. A level
  change the user wants now: the Seer's `ctl stop --after-trip` first, then the new command from home.
- The Seer yielded: read its report, fix what it lists (section 4), then dispatch a fresh Seer (section 2, with the
  shift's instructions and command) unless the user ended the shift. Long shifts outgrow a Seer's context (§7):
  re-dispatch when its transcript gets long, between runs.

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
   `test_loop_hunt.py` for the hunt runner, and for fight, crawl, leave or pet behaviour the Tidal Tomb simulator
   (docs/SIM.md "Running" and "Tidal Tomb"): `python harness/sim/run.py --scenario tidal_tomb --runs 8 --compare`
   for level 1; level 2 `--area 6815 1907 6969 2100 -- --enter-via 6718 1984 --leave-gate 6913 2071`; level 3
   `--area 7047 1906 7257 2223 -- --enter-via 6609 2060 --enter-via 6884 1929 --fight-via 6884 1929 --leave-gate
   6897 1992 --leave-via 7161 2221`; `python harness/test_sim.py` (its `test_tidal_*`, `test_go_home_tidal_level3`).
   Batches as live plays use `--others --prior <the main store>`. Before/after numbers from the simulator are the
   proof the user asks for before trying again in game. Keep a failing-before/passing-after test where it catches a
   plausible regression. Large fixes may go to a `task` subagent, not isolated.
4. **Record and push** (Rule 0): the finding in docs/NOTES.md (a "Tidal Tomb: …" section for the run), the runner
   rule in docs/HUNT_LOOP.md, a changed Seer procedure in docs/OVERSEER.md §5, the decision in docs/PLAN.md, the
   simulator's numbers in docs/SIM.md "Tidal Tomb". Commit only the fix's files (the user's uncommitted work stays
   out), push, and see the push succeed.
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
