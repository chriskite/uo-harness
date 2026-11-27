---
name: petram-shift
description: Run Logan Wolf's Mount Petram crawl shifts through a Seer (overseer) subagent per docs/OVERSEER.md, while this session watches and fixes every issue the Seer or the user reports. Takes the user's shift instructions, e.g. "only level 1" (`/skill:petram-shift only level 1`), and turns them into the runner's command. Use when the user asks for Mount Petram (Petram, Mt Petram) runs, a Petram shift, or to start the Seer for Logan Wolf in Mount Petram.
---

# Mount Petram shift: the Seer plays, you fix

Two roles. **The Seer** (project agent `overseer`, `.omp/agents/overseer.md`) runs Logan Wolf's shift by
docs/OVERSEER.md §5 "Mausoleum crawl shifts" with its Mount Petram commands ("Another hunt level by the user's word",
"Mount Petram"): drop-off, loadout check, `ctl run hunt …`, `ctl wait`, run after run until the user says stop.
**You** (the main session) start it, watch it, and fix the code and docs behind every issue it or the user reports.
You never drive the game while it runs; it never edits code.

Read first: AGENTS.md (in context), docs/OVERSEER.md §5 "Mausoleum crawl shifts" and §6, docs/HUNT_LOOP.md "Pets",
"In and out of a dungeon", "Crawl"; docs/NOTES.md "Mount Petram: …" sections; docs/SIM.md "Mount Petram".

**The runner has never hunted Mount Petram live.** Everything it does there was tried only in the simulator, built
from one hand-played visit by the user (2026-11-26, 17.5 min; a red attacked Logan on level 3 at the end). The first
live runs are the test: watch them closely.

## 0. The user's instructions

The user may add instructions after the skill name (`/skill:petram-shift only level 1` arrives as `User: only level
1`), in the request, or later while the shift runs. They hold for the whole shift, through every re-dispatch, until
the user changes them. None: level 2 (below). Map each one before dispatch:

1. **A level** ("only level 1", "Petram 3", "the cave"): the level's command from docs/OVERSEER.md §5 "Another hunt
   level by the user's word" ("Mount Petram"); "only" means every run of the shift on it. Each run hunts one level:
   the crawl stays on the floor it reached, and its "zones" are depth bands of that floor, not levels.

   All commands are `ctl run hunt --pets --crawl --heal-in-place --vet-range 5 --crawl-learn-s 180 --leave-recall
   0x444AB1F3 --enter-recall 0x444AB1F3 --gate-dest Totem --enter-rune "Mount Petram Cave"` plus:

   | The user says | Level | Arguments | State (simulator 2026-11-26, AlaFlambe and Bussin, 32 runs of 30 min from home) |
   |---|---|---|---|
   | level 1, the cave | Petram 1 | `--leave-gate 5602 2768 --leave-gate 5610 2800 --leave-gate 5635 2819 --leave-gate 5629 2786` | Runnable. 0 runs with a death, 56k gp a game hour; kills 244 an hour against the user's 246; 2 of 32 left a pet at the time-up leave. Out by the easiest of its four golden gates, none opened live. |
   | level 2, nothing | Petram 2 | `--enter-via 5517 2780 --fight-via 5517 2780 --leave-gate 5639 2888` | **The default** (the safest level, and the one the simulator matches live: damage on Logan 318 an hour against the user's 262). 0 runs with a death or a pet left, 114k gp a game hour. Out by its crimson gate, never opened live. |
   | level 3, Mt Petram 3 | Petram 3 | `--enter-via 5517 2780 --enter-via 5571 2879 --fight-via 5571 2879 --leave-gate 5690 2885` | 0 runs with a death, 139k gp a game hour, but **5 of 32 left a pet at its gate** (a floor at z 0 under the gate's: the dash through it with a pet below), and its damage on Logan is a ninth of the user's visit (431 against 4,024 an hour, partly his 6.1 % songs). The ankheg's digs and the moss giant's slams hit Logan; a red attacked him there on 2026-11-26. Out by its crimson gate, which Logan took to Totem live. |

   **Level 3:** before the first run, tell the user in one message what the table says for it and wait for their go;
   then dispatch with it. Their go stands for the shift.
2. **A runner setting** ("no songs", "stop after 50 kills", "don't provoke", "never pull the moss giants"): the flag
   from `python harness/loop_hunt.py --help` added to the level's command (`--no-songs`, `--kills 50`, `--no-provoke`,
   `--never-aggro "moss giant"`). The flags the level's command already has stay.
3. **A shift rule** ("three runs", "stop at 23:00", "home by midnight"): Seer procedure, written into the dispatch
   (no new run after the Nth; `ctl stop --after-trip` at the time). Pets are the user's to bring: the runner claims
   the tamed pets beside Logan at the start.
4. **Nothing maps** (no flag, no procedure, a part of the dungeon the table doesn't have): tell the user before
   dispatch that it needs runner work (section 4), and ask whether to start without it.

When you dispatch, tell the user the plan in one line: the level, the added flags, the shift rules.

## 1. Before dispatch

1. Stack up and current: `python harness/stack.py status`. An `external … not ours` service is still old code
   (NOTES "Stack supervisor"); tell the user rather than restarting it.
2. `./ctl.cmd --char "Logan Wolf" status > /tmp/s.json`, then with `jq.exe … < /tmp/s.json`: `ok`, `pos`, `facet`,
   `hits`, `gate`, `tasks`, `open_junctures`, `overseer_heartbeat`, Logan's pets in `mobiles` within 3 tiles
   (whichever the user brought; on 2026-11-26 AlaFlambe `0x0026EF56` and Bussin `0x000EA04F`), `gumps_open`.
3. **The rune.** Every Petram command recalls by the "Mount Petram Cave" row of the tome 0x444AB1F3 (the user added it
   2026-11-26); it lands inside on level 1 at (5600, 2764). At home with no task running: `./ctl.cmd --char "Logan
   Wolf" act recall 0x444AB1F3 --check` must list that row and 2 charges at least. Missing: tell the user (the rune is
   theirs to add) and start nothing.
4. **One character inside.** The server refuses a character into Mount Petram while another of the account's is
   inside (docs/research/DISCORD_KB.md "mount petram multiple character entry restriction", single source). Outland
   Dan or another character of the user's in Mount Petram: tell the user and start nothing.
5. **One Seer per character.** `read history://`: a `SeerLoganWolf*` agent `running` (or a heartbeat under 90 s
   old from another session) already owns Logan, in another dungeon or here; message it (`write agent://<id>`)
   instead of starting another. A parked one may be revived the same way when its context is still small; otherwise
   dispatch fresh.
6. Anything off (dead, pets missing, gate paused, a task running): tell the user and dispatch only once they say
   how to proceed. Alive inside a dungeon with no run: the Seer's first act is the go-home run (§5 step 7) with the
   command of the level he stands on (§5 "Another hunt level": the levels' boxes, Mount Petram's among them) plus
   `--go-home`. §5 step 1: start at home in Logan Wolf's house (the "Logan's House" landing).

## 2. Dispatch

One `task` call; `agent: "overseer"` goes **on the `tasks[]` item** (a top-level `agent` is ignored: OVERSEER.md
§6). Name `SeerLoganWolf`, with a number when `history://` lists that name from an earlier session (omp refuses
the repeat: "already owned by another session generation", 2026-10-22). Don't set `isolated`: ctl must run the
main checkout's code against the main store (§5 step 0).

`context`: the goal and the reporting contract below. `task`:

```
Act as the Seer for Logan Wolf: follow docs/OVERSEER.md §5. Goal: Mount Petram crawl shifts (§5 'Mausoleum crawl
shifts' with its Mount Petram commands: 'Another hunt level by the user's word', 'Mount Petram') until the user says
stop. The runner has never hunted Mount Petram live: these are its first runs there.
State at dispatch (checked by the parent, <HH:MM>): <hits, pos, facet, at home or where, pets and their hits,
gold, gate, tasks, open junctures; the tome's charges and its "Mount Petram Cave" row>.
Runner changes since the last shift (commits <shas>): <what each changes and the log lines it writes>.
Shift instructions from the user, verbatim: <"…", or "none">.
Command for every run, a restart inside and the go-home run: <the full ctl run hunt command of the level>.
<Shift rules from the instructions (run count, stop time), the user's go for level 3 with its time, and any other
change to §5's procedure the user asked for, else "§5 procedure verbatim".>
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
- **What Mount Petram does that the other dungeons don't** (docs/NOTES.md "Mount Petram: the user's first visit";
  expected, not new bugs):
  - the recall lands inside, on level 1 at (5600, 2764); no gate is near enough for a recall out there;
  - the way down to level 2 is level 1's north strip (5516-5518, 2780): the runner steps onto it and sends one more
    move N ("the teleporter at 5517,2780: on it at (x, y, z), no teleport on the step onto it; the move from it
    (direction 0) fires it"), landing in a 3 x 3 pocket (5569-5571, 2864-2866) it walks out of N across level 2's
    strip;
  - level 2's tile (5571, 2879) goes to level 3's ledge (5699, 2801, z 72), which winds down to the floor. Only a
    move N from it was seen fire; the runner sends the move it stepped onto the tile with. "the teleporter at
    5571,2879: the step in direction <d> moved without a teleport" on a level 3 run is an issue for the parent (its
    `dirs` in harness/data/teleporters.json narrowed to [0], as the strips' are).
  - '*digs*' over an ankheg: 1.8 s later 35-53 on whoever stands bunched up to 12 tiles off, with poison on Logan
    ('*wracked with extreme pain*'); a moss giant's slam: 60-67 on each pet and 32-48 on Logan within 3 of it, "You
    have been knocked off your feet!";
  - deathstalks' '*entraps <pet>*', trapdoor spiders' webs ("You have been ensnared and poisoned!", hamstrings),
    "You are entangled in vines!": not modelled in the simulator, not faults by themselves.
- **Watch for** red players, above all on level 3 (Meloh attacked Logan there by hand on 2026-11-26 21:10, Corp Por;
  the guild calls "[DTF] Red Mt Petram 3"); the runner's red leave ("LEAVING ... by the moongate") is its own. A pet
  left behind at level 3's gate (5 of 32 simulated runs: a pet on the floor at z 0 under the gate's at 15, then "to the
  moongate: attacked 8 tiles from it; through it now") is urgent: `pet_left_behind`, the user fetches the pet; it is
  the open issue docs/SIM.md "Mount Petram" names, the one to fix first (section 4). Logan on level 2 or 3
  during a level 1 run, or on level 3 during a level 2 run: the strips and the marked tile are one-way teleporters the
  runner never steps onto unaimed, so this should not happen; have the Seer `ctl stop` that run (§5 step 8) and start
  that level's go-home run at once. Logan standing still in a pit or a channel with "no route from (x, y)": the
  runner's walks refuse drops into such sinks since 2026-11-26 (docs/HUNT_LOOP.md "No drop into a sink"), so it is
  an issue for the parent; getting him out by hand is the user's.
- **Never** `ctl wait` for Logan: it advances the Seer's shared cursors (`overseer_juncture_cursor`,
  `overseer_chat_cursor`) and the Seer misses those events. Never `ctl act`, `ctl run` or `ctl stop` while the
  Seer runs: have the Seer do it. Never fight by hand.
- The user may report to you here (steering) or to the Seer in viz/Telegram (it forwards). Treat both the same.
- **A new instruction mid-shift** (e.g. "level 1 from now on"): map it as in section 0 and message the Seer the new
  command or rule. It takes effect at the next `ctl run`; a run in progress keeps its arguments. A level change the
  user wants now: the Seer's `ctl stop --after-trip` first, then the new command from home.
- The Seer yielded: read its report, fix what it lists (section 4), then dispatch a fresh Seer (section 2, with the
  shift's instructions and command) unless the user ended the shift. Long shifts outgrow a Seer's context and its
  request budget (§7: near 200 requests it stops the run with `--after-trip` and yields): re-dispatch when its
  transcript gets long, between runs, and at once when it yields before the shift's end.

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
3. **Reproduce offline, then fix** in the main checkout (or a git worktree when another session edits it): the
   module's own `harness/test_*.py`, root `test_loop_hunt.py` for the hunt runner, and for fight, crawl, leave or pet
   behaviour the Mount Petram simulator (docs/SIM.md "Running" and "Mount Petram"), with the live team and store:
   `python harness/sim/run.py --scenario petram --runs 32 --team phoenix_incubus --prior <main checkout>/harness/data/harness.db --compare`
   plus the level's:
   - level 3, the scenario's default: `-- --vet-range 5 --crawl-learn-s 180`;
   - level 2 `--level 2 -- --vet-range 5 --crawl-learn-s 180`; level 1 `--level 1 -- …` (each level's block gives its
     teleporters, the fought leg and its gates).

   `python harness/test_sim.py` has Petram's tests (`test_petram_*`). The scenario is one visit deep: each live run
   is new evidence. Add its capture to `calibration.tags` in `harness/data/sim/petram.json`, then rerun
   `python harness/sim/calibrate.py --scenario petram` and check new first sightings against `spawns` (docs/SIM.md
   "Mount Petram" names what is thin). Before/after numbers from the simulator are the proof the user asks for
   before trying again in game. Keep a failing-before/passing-after test where it catches a plausible regression.
   Large fixes may go to a `task` subagent, not isolated.
4. **Record and push** (Rule 0): the finding in docs/NOTES.md (a "Mount Petram: …" section for the run), the runner
   rule in docs/HUNT_LOOP.md, a changed Seer procedure in docs/OVERSEER.md §5, the decision in docs/PLAN.md, the
   simulator's numbers in docs/SIM.md "Mount Petram". Commit only the fix's files (the user's uncommitted work stays
   out), push, and see the push succeed.
5. **Make it live.** Runner code (loop_hunt, tamer, crawl, aggro, escape, moongate, agent_link, pathfind, …) loads at
   `ctl run`: the next run has it; a run in progress keeps the old code. ctl.py loads per call. Proxy and world-model
   code (`harness/world/`, the proxy, the state port) needs a proxy restart: ask the user, with Logan at home in the
   house and no run (after it `status.mobiles` is empty until the pets show again). §5 or overseer.md changes reach a
   running Seer only by message.
6. **Brief the Seer** (`write agent://<id>`): the commit, what changed, the log lines it writes now, whether to
   resume. Tell the user in one or two lines: the issue, the cause, the fix, the commit.

## 5. Ending

The user says stop: tell the Seer to end the shift (it runs `ctl stop --after-trip` and waits for `task_done` at
home), wait for its report, and pass the user its summary plus every fix made this shift with its commit.
