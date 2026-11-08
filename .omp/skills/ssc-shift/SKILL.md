---
name: ssc-shift
description: Run Logan Wolf's Shadowspire Cathedral crawl shifts through a Seer (overseer) subagent per docs/OVERSEER.md, while this session watches and fixes every issue the Seer or the user reports. Takes the user's shift instructions, e.g. "three runs" (`/skill:ssc-shift three runs`), and turns them into the runner's command. Use when the user asks for Shadowspire Cathedral (SSC) runs, an SSC shift, or to start the Seer for Logan Wolf in Shadowspire.
---

# Shadowspire Cathedral shift: the Seer plays, you fix

Two roles. **The Seer** (project agent `overseer`, `.omp/agents/overseer.md`) runs Logan Wolf's shift by
docs/OVERSEER.md §5 "Mausoleum crawl shifts" with its Shadowspire commands ("Another hunt level by the user's word",
"Shadowspire Cathedral"): drop-off, loadout check, `ctl run hunt …`, `ctl wait`, run after run until the user says
stop. **You** (the main session) start it, watch it, and fix the code and docs behind every issue it or the user
reports. You never drive the game while it runs; it never edits code.

Read first: AGENTS.md (in context), docs/OVERSEER.md §5 "Mausoleum crawl shifts" and §6, docs/HUNT_LOOP.md "Pets",
"In and out of a dungeon", "Crawl"; docs/NOTES.md "Shadowspire Cathedral: …" sections; docs/SIM.md "Shadowspire
Cathedral".

**The runner has never hunted Shadowspire live.** Everything it does there was tried only in the simulator, built from
the user's seven hand-played visits of 2026-10-24 to 11-03 (Logan died on level 1 on 10-31, to two red players). The
first live runs are the test: watch them closely.

## 0. The user's instructions

The user may add instructions after the skill name (`/skill:ssc-shift three runs` arrives as `User: three runs`), in
the request, or later while the shift runs. They hold for the whole shift, through every
re-dispatch, until the user changes them. None: level 1 (the user's pick, 2026-11-08). Map each one before dispatch:

1. **A level** ("only level 1", "SSC 2"): the level's command from docs/OVERSEER.md §5 "Another hunt level by the
   user's word" ("Shadowspire Cathedral"); "only" means every run of the shift on it. Each run hunts one level: the
   crawl stays on the floor it reached, and its "zones" are depth bands of that floor, not levels.

   All commands are `ctl run hunt --pets --crawl --heal-in-place --vet-range 5 --crawl-learn-s 180 --leave-recall
   0x444AB1F3 --enter-recall 0x444AB1F3 --gate-dest Totem --enter-rune "SSC Inside"` plus:

   | The user says | Level | Arguments | State (simulator 2026-11-08, AlaFlambe and Bussin, 30 min from home) |
   |---|---|---|---|
   | level 1, nothing | SSC 1 | `--leave-gate 5145 2480 --leave-gate 5176 2489 --leave-gate 5157 2647` | The default. 0 of 32 runs with a death, 145k gp a game hour. Against the user's visits per hour: kills 155 vs 105, damage on Logan 1,068 vs 1,217. **3-4 of 32 runs left a pet in SSC** at the time-up leave: "attacked N tiles from it; through it now" with the pets 4-10 tiles back, then "PETS LEFT BEHIND (gate)". It happened at all three gates. Out by the easiest of its three gates (two golden, one crimson), all of whose menus listed Totem live. Never run live. |
   | level 2 | SSC 2 | the go-home run only: `--enter-via 5132 2653 --fight-via 5132 2653 --leave-via 5277 2641 --leave-gate 5157 2647` | **Refuse it: runner work first.** 15 of 32 runs with a death, 26 with a pet death. The stoneshapers' spawns probably overstate this [INFERENCE: 5 x the user's encounter rate]. The runner treats teleporter tiles as two-way, but the strips are one-way. A stray step onto the twin strip tile fires, is learnt into the store as a wall, and closes that landing for every later run (2 of 32). Offer level 1, or the runner work (docs/SIM.md "Shadowspire Cathedral", "Open"). |
   | level 3, level 4, the east wing, a boss | none | none | Not in the scenario (never hunted; the east wing visit was a guild boss fight). Refuse it. |

2. **A runner setting** ("no songs", "stop after 50 kills", "don't provoke", "never pull the winged hulks"): the flag
   from `python harness/loop_hunt.py --help` added to the level's command (`--no-songs`, `--kills 50`, `--no-provoke`,
   `--never-aggro "winged hulk"`). The flags the level's command already has stay.
3. **A shift rule** ("three runs", "stop at 23:00", "home by midnight"): Seer procedure, written into the dispatch
   (no new run after the Nth; `ctl stop --after-trip` at the time). Pets are the user's to bring: the runner claims
   the tamed pets beside Logan at the start.
4. **Nothing maps** (no flag, no procedure, a level the table refuses): tell the user before dispatch that it needs
   runner work (section 4), and ask whether to start without it.

When you dispatch, tell the user the plan in one line: the level, the added flags, the shift rules.

## 1. Before dispatch

1. Stack up and current: `python harness/stack.py status`. An `external … not ours` service is still old code
   (NOTES "Stack supervisor"); tell the user rather than restarting it.
2. `./ctl.cmd --char "Logan Wolf" status > /tmp/s.json`, then with `jq.exe … < /tmp/s.json`: `ok`, `pos`, `facet`,
   `hits`, `gate`, `tasks`, `open_junctures`, `overseer_heartbeat`, Logan's pets in `mobiles` within 3 tiles
   (whichever the user brought), `gumps_open`.
3. **The rune.** Every Shadowspire command recalls by the "SSC Inside" row of the tome 0x444AB1F3; it lands inside on
   level 1 at (5144, 2480), beside the golden gate 0x40035E71. At home with no task running: `./ctl.cmd --char "Logan
   Wolf" act recall 0x444AB1F3 --check` must list that row and 2 charges at least. Missing: tell the user (the rune is
   theirs to add) and start nothing.
4. **One Seer per character.** `read history://`: a `SeerLoganWolf*` agent `running` (or a heartbeat under 90 s
   old from another session) already owns Logan, in another dungeon or here; message it (`write agent://<id>`)
   instead of starting another. A parked one may be revived the same way when its context is still small; otherwise
   dispatch fresh.
5. Anything off (dead, pets missing, gate paused, a task running): tell the user and dispatch only once they say
   how to proceed. Alive inside a dungeon with no run: the Seer's first act is the go-home run (§5 step 7) with the
   command of the level he stands on (§5 "Another hunt level": the levels' boxes, Shadowspire's among them) plus
   `--go-home`. Logan in Shadowspire's east wing (the boss and silver-gate rooms, x 5400-5540, y 2400-2540) or outside
   on its grounds: no command; ask the user. §5 step 1: start at home in Logan Wolf's house (the "Logan's House"
   landing).

## 2. Dispatch

One `task` call; `agent: "overseer"` goes **on the `tasks[]` item** (a top-level `agent` is ignored: OVERSEER.md
§6). Name `SeerLoganWolf`, with a number when `history://` lists that name from an earlier session (omp refuses
the repeat: "already owned by another session generation", 2026-10-22). Don't set `isolated`: ctl must run the
main checkout's code against the main store (§5 step 0).

`context`: the goal and the reporting contract below. `task`:

```
Act as the Seer for Logan Wolf: follow docs/OVERSEER.md §5. Goal: Shadowspire Cathedral crawl shifts (§5 'Mausoleum
crawl shifts' with its Shadowspire commands: 'Another hunt level by the user's word', 'Shadowspire Cathedral') until
the user says stop. The runner has never hunted Shadowspire live: these are its first runs there.
State at dispatch (checked by the parent, <HH:MM>): <hits, pos, facet, at home or where, pets and their hits,
gold, gate, tasks, open junctures; the tome's charges and its "SSC Inside" row>.
Runner changes since the last shift (commits <shas>): <what each changes and the log lines it writes>.
Shift instructions from the user, verbatim: <"…", or "none">.
Command for every run, a restart inside and the go-home run: <the full ctl run hunt command of the level>.
<Shift rules from the instructions (run count, stop time), and any other change to §5's procedure the user asked
for, else "§5 procedure verbatim".>
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
- **What Shadowspire does that the other dungeons don't** (docs/NOTES.md "Shadowspire Cathedral: …"; expected, not
  new bugs):
  - the recall lands inside, on level 1 at (5144, 2480), a tile from the golden gate 0x40035E71: no walk from outside;
  - "*mud spray*", "*gold dust*" or "*iron shards*" over a gargoyle: 10 quick ticks (4-8 each) on one of us, every
    15-16 s. After a mud spray, "Your feet sink in mud!" on Logan is a puddle rooting him 1 s, then "You are no longer
    entangled.";
  - "*detonates*" over a gargan scorcher: 38-65 on the pets within 1-3 of it, 32-54 on Logan beside them;
  - "The creature intimidates you, reducing your accuracy." / "*intimidated*": a winged hulk, no damage;
  - crowds: 22-64 other players in each capture, corpses round the arrival, "A murder has occurred within this area
    and warning ravens sent forth!". Not a fault;
  - a hue-2825 moongate at (5128, 2622) shows only to a ghost (the ghosts' gate).
- **Watch for** red players on level 1. Logan died there by hand on 2026-10-31 (5130, 2614) to two reds, with a mud
  gargoyle rooting him. The runner's red leave ("LEAVING ... by the gold moongate") is its own: nothing to fix. Also
  watch for a pet left behind at the time-up leave (above): `pet_left_behind` is urgent, and fetching the pet is the
  user's job. And watch for Logan on level 2 (x 5241-5359) during a level 1 run: a stray step south onto the strip
  (5133, 2653), which is not in `teleporters.json` (never seen in 64 simulated runs). Have the Seer `ctl stop` that
  run (the run is now somewhere wrong, §5 step 8) and start level 2's go-home run at once.
- **Never** `ctl wait` for Logan: it advances the Seer's shared cursors (`overseer_juncture_cursor`,
  `overseer_chat_cursor`) and the Seer misses those events. Never `ctl act`, `ctl run` or `ctl stop` while the
  Seer runs: have the Seer do it. Never fight by hand.
- The user may report to you here (steering) or to the Seer in viz/Telegram (it forwards). Treat both the same.
- **A new instruction mid-shift** (e.g. "no provoking from now on"): map it as in section 0 and message the Seer
  the new command or rule. It takes effect at the next `ctl run`; a run in progress keeps its arguments. A level
  change the user wants now: the Seer's `ctl stop --after-trip` first, then the new command from home.
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
   behaviour the Shadowspire simulator (docs/SIM.md "Running" and "Shadowspire Cathedral"), with the live team and
   store:
   `python harness/sim/run.py --scenario ssc --runs 16 --team phoenix_incubus --prior <main checkout>/harness/data/harness.db`
   plus the level's:
   - level 1, the scenario's default: `-- --vet-range 5 --crawl-learn-s 180`;
   - level 2 `--level 2 -- --vet-range 5 --crawl-learn-s 180` (its level block gives the strips, the fought leg and
     the crimson gate).

   `python harness/test_sim.py` has Shadowspire's tests (`test_ssc_*`). Before/after numbers from the simulator are
   the proof the user asks for before trying again in game. Keep a failing-before/passing-after test where it catches
   a plausible regression. Large fixes may go to a `task` subagent, not isolated.
4. **Record and push** (Rule 0): the finding in docs/NOTES.md (a "Shadowspire Cathedral: …" section for the run), the
   runner rule in docs/HUNT_LOOP.md, a changed Seer procedure in docs/OVERSEER.md §5, the decision in docs/PLAN.md,
   the simulator's numbers in docs/SIM.md "Shadowspire Cathedral". Commit only the fix's files (the user's
   uncommitted work stays out), push, and see the push succeed.
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
