---
name: lumber-shift
description: Run Outland Dan's lumber shifts (self-optimizing lumber loops) through a Seer (overseer) subagent per docs/OVERSEER.md §5 "Lumber shifts", after checking his runebook still recalls to the DTF guild house and Logan's House, while this session watches and fixes every issue the Seer or the user reports. Takes the user's shift instructions, e.g. "exploit" or "until 23:00" (`/skill:lumber-shift exploit`). Use when the user asks to send Outland Dan lumberjacking, for lumber runs, lumber loops or a lumber shift, or to start the Seer for Dan's lumber.
---

# Lumber shift: the Seer plays, you fix

Two roles. **The Seer** (project agent `overseer`, `.omp/agents/overseer.md`) runs Outland Dan's shift by
docs/OVERSEER.md §5 "Lumber shifts": `ctl lumber plan`, `ctl run` its `command` verbatim, `ctl wait`, plan again,
run after run until the user says stop. **You** (the main session) check Dan's travel and kit, start the Seer, watch
it, and fix the code and docs behind every issue it or the user reports. You never drive the game while it runs; it
never edits code.

Read first: AGENTS.md (in context), docs/OVERSEER.md §5 "Lumber shifts", "Travelling somewhere new",
"Recall-reached spots", "Safety" and §6; docs/LUMBER_LOOP.md §6 (the plan, library hops, forest spots), §12.6 (home
is Logan Wolf's house) and §13 (the runner: trip steps, hatchet uses, self care, tracking reds, running from a
creature).

## 0. The user's instructions

The user may add instructions after the skill name (`/skill:lumber-shift exploit` arrives as `User: exploit`), in
the request, or later while the shift runs. They hold for the whole shift, through every re-dispatch, until the user
changes them. None: §5 verbatim (the plan's `command`, explore picks included: that is how the job learns). Map each
one before dispatch:

1. **Exploit** ("exploit", "boards now", "best spot only"): every run is the plan's `greedy.command` verbatim
   (`ctl lumber plan --exploit` makes it the pick). Never edit `--spot` into the pick's command (§5).
2. **Keep off a spot** ("not witcher_282", "avoid Nusero"): `ctl lumber spot set <id> --status disabled --reason
   "user <date>: <their words>"`, then `ctl lumber forests` so the fill stops there. A spot by its name: `ctl lumber
   spots` maps it to an id. Done by you before dispatch (store only, no game action) or by the Seer mid-shift.
3. **A plan setting** ("shorter stints", "30-minute runs"): `ctl lumber plan --stint-min N`; the Seer passes it on
   every plan.
4. **A runner setting** ("no hops", "don't ride", "leave the aspect alone"): the flag from `python
   harness/loop_lumber.py --help` appended to the plan's command (`--hops 0`, `--mount off`, `--harvest-aspect
   off`). A user's say is one of §5's allowed overrides; the Seer `think`s why each time. Never `--track off`,
   `--resupply off` or a lower `--track-react-range` without the user naming that exact thing: they keep Dan alive.
5. **A shift rule** ("three runs", "stop at 23:00", "until 20 000 boards"): Seer procedure, written into the
   dispatch (no new run after the Nth; `ctl stop --after-trip` at the time or once the stored boards reach it).

An instruction that fits none of these, or that would break §5's safety rules (a no-go area such as Old Papua, a
candidate spot unseen on `ctl map`, fighting): ask the user before dispatch.

## 1. Before dispatch

1. Stack up and current: `python harness/stack.py status`. An `external … not ours` service is still old code
   (NOTES "Stack supervisor"); tell the user rather than restarting it.
2. `./ctl.cmd --char "Outland Dan" status > /tmp/s.json`, then with `jq.exe … < /tmp/s.json`: `ok`, `name`, `pos`,
   `facet`, `hits`, `weight`, `gate`, `tasks`, `open_junctures`, `overseer_heartbeat`, `gumps_open`,
   `equipment` (the worn hatchet, the mount layer), and in `backpack.items` the runebook (graphic `0x22C5`,
   0x49865F8F as of 2026-10-23), the trapped pouches (`0x0E79`, hue 38) and the hatchets. Home is Logan's House:
   the landing (3575, 3251), facet 0, at home within 6 tiles (harness/data/homes.json, LUMBER_LOOP §12.6). The
   runner tops up hatchets, pouches and the loadout itself at the house shelf and chest (§13 "Trip" 1a, 1a'); what
   it can't find becomes a `low_supplies` juncture.
3. **One Seer per character.** `read history://`: a `SeerOutlandDan*`, `DanMiningSeer*`, `LumberSeer*` or `Seer-*`
   agent `running` (or a heartbeat under 90 s old from another session) already owns Dan; message it (`write
   agent://<id>`) instead of starting another. One already on a mining shift: tell the user (one task per character)
   and start nothing until they choose. Revive a parked lumber Seer the same way when its context is still small;
   otherwise dispatch fresh.
4. **Runebook check: always when the user says they changed the book, else when the last check is old.** The
   runner recalls by the entry index remembered from the last read (places.known_books; escape.recall: the index
   wins over the name), so a moved rune sends a stale run to the wrong place.
   - Read it: `./ctl.cmd --char "Outland Dan" act recall <book> --check`. Needs `ok`, `remembered: true`,
     `can_cast`, `charges`, and two runes: **"DTF Loot Chest"** landing near (4134, 1429) (the way into the guild
     house for every DTF-library landing, homes.json `library_rune`) and **"Logan's House"** landing at (3575,
     3251) (the way home and the red escape: the default, entry 8 since 2026-10-23; LUMBER_LOOP §6 "home"). Note
     each one's `i`.
   - Then when the user moved runes: prove it live from home. `act recall <book> --rune "DTF Loot Chest"` must
     return `to` ≈ [4134, 1429]; wait ~4 s; `act recall <book>` (the default) must return `to` = [3575, 3251], or
     `act recall <book> --rune "Logan's House"` when the default is something else. Each costs a charge. The guild
     shelf's Resupply refills the book to 10 charges on the next trip through the guild house.
   - Missing or wrong landing: tell the user, start nothing. A changed default or index: update the docs that name
     it (OVERSEER.md "Recall-reached spots", LUMBER_LOOP.md §6 "home", the mining-shift skill) and push.
5. **The plan:** `./ctl.cmd --char "Outland Dan" lumber plan > /tmp/p.json` (add `--exploit` / `--stint-min` per
   section 0). Check `ok`, `home.at_home`, `pick` (`spot`, `mode`, `p_best`, `landing`, `command`) and `greedy`.
   `ok: false` (every spot out) is for the Seer (§5: `lumber discover`, inspect, approve); a line ending "price
   needed …" lists gear the plan values at 0 gp on death: ask the user its price and `ctl lumber price` it.
   `hatchets.buy` is advice for the user.
6. Anything off (dead, a ghost mount, not at home, a task running, gate paused, open urgent junctures, under ~3
   charges with no recall reagents, no trapped pouch and none on the shelf): tell the user and dispatch only once
   they say how to proceed.

## 2. Dispatch

One `task` call; `agent: "overseer"` goes **on the `tasks[]` item** (a top-level `agent` is ignored: OVERSEER.md
§6). Name `SeerOutlandDan`, with a number when `history://` lists that name from an earlier session (omp refuses
the repeat). Don't set `isolated`: ctl must run the main checkout's code against the main store.

`context`: the goal, the state and the reporting contract below. `task`:

```
Act as the Seer for Outland Dan: follow docs/OVERSEER.md §5. Goal: lumber shifts (§5 'Lumber shifts') until the
user says stop. Pass --char "Outland Dan" on every ctl call.
State at dispatch (checked by the parent, <HH:MM>): <hits, pos, facet, at home, weight, gold, gate, tasks, open
junctures, hatchets carried, trapped pouches, mount>. Runebook <serial>: <charges>, default <name> (entry <i>);
"DTF Loot Chest" entry <i> → (x, y), "Logan's House" entry <i> → (x, y)<, both recalled live at HH:MM>.
Current plan pick: <spot, mode, P(best), landing>; greedy: <spot>.
Runner changes since the last shift (commits <shas>): <what each changes and the log lines it writes>.
Shift instructions from the user, verbatim: <"…", or "none">.
Plan call for every run: <`ctl lumber plan` with its flags>. Run: <the plan's `command` | its `greedy.command`>
<plus the runner flags from the instructions>.
<Shift rules from the instructions (run count, stop time, board target) and any other change to §5's procedure the
user asked for, else "§5 procedure verbatim".>
Reporting contract (the parent session fixes code; you never do):
- Report every issue to the parent at once with `write agent://Main`: a runner error or traceback, a run that did
  something §5 or LUMBER_LOOP.md §13 says it must not, a stuck or confused run, a death, a `pk_escape` or creature
  escape that went wrong, a recall that landed somewhere unexpected, logs or boards left in the pack after a run, a
  `store` event with ok: false, a `hop` that made no sense, `low_supplies` (trapped pouch, hatchet uses, mount,
  harvest aspect armor, arcane essence), a spot that proved unworkable, a refused or failing ctl/act, anything the
  user reports as a bug in viz or Telegram chat (quote it with its chat id). Include: time, task id, log path, the
  exact log lines, Dan's pos/hits/weight, where he is now and what you did.
- Then keep the shift: get Dan home safely by §5 (`ctl stop --after-trip` unless it's an emergency; logs left in
  the pack: `act convert --store` at home), start no new run while the issue is open, and keep `ctl wait` going. A
  parent message wakes you; it says when the fix is in and what changed, and whether to resume.
- Deaths, possible staff and server restrictions go to the user too (`ctl say "@user …"`); start nothing until they
  answer. Spots you approve, disable or merge (`lumber discover`, `spot set`, `forests`) go to the parent as well.
- Every few runs, `ctl say` one progress line (runs, boards stored, spots tried).
- Yield your final report (what .omp/agents/overseer.md asks for) only when the shift ends.
```

## 3. Watch

Call `wait`. It returns on a message from the Seer, its end, the user, or a 30-minute cap.

- **Every return**, a read-only glance: `./ctl.cmd --char "Outland Dan" status` (hits, pos, weight, `intent`,
  `tasks`, `overseer_heartbeat`), the tail of `logs/tasks/<task_id>.log`, `ctl chat --role overseer --after N` and
  `ctl junctures --after N` (keep N from the last glance). `ctl lumber audit --days 1` shows how the day's trips
  compare with the plan. A Seer silent while a run is in trouble, a heartbeat older than 90 s, or Dan idle outside
  the house with no run is an issue too: message the Seer or check `history://<id>`.
- **Never** `ctl wait` for Dan: it advances the Seer's shared cursors (`overseer_juncture_cursor`,
  `overseer_chat_cursor`) and the Seer misses those events. Never `ctl act`, `ctl run`, `ctl stop` or `ctl lumber
  plan` while the Seer runs (a plan posts an `action` chat row the Seer would read as its own): have the Seer do it.
  The runebook check (section 1) is done before dispatch, not during a shift.
- The user may report to you here (steering) or to the Seer in viz/Telegram (it forwards). Treat both the same.
- **A new instruction mid-shift**: map it as in section 0 and message the Seer the new plan call, flags or rule. It
  takes effect at the next `ctl run`; a run in progress keeps its arguments. A change the user wants now: the Seer's
  `ctl stop --after-trip` first, then the new run from home.
- The Seer yielded: read its report, fix what it lists (section 4), then dispatch a fresh Seer (section 2, with the
  shift's instructions) unless the user ended the shift. Re-dispatch between runs when its transcript gets long
  (OVERSEER.md §7).

## 4. Fix

User reports are ground truth; don't re-verify them in game.

1. **Safety first.** Dan in danger or a run doing harm: tell the Seer what to do (`stop --after-trip`, or plain
   `stop` only for a GM, a server restriction or a run already somewhere wrong). Anything needing hands in the
   client (a ghost, a stuck gump, an item held on the cursor after a stop mid-drag: NOTES "Hatchet shelf loop") is
   the user's: ask them.
2. **Find the cause from evidence:** the task log, the session capture (`logs/session_*.jsonl`, `jq.exe -c
   'select(…)'`), the store's lumber episodes, trip rows and job events, the Seer's transcript (`history://<id>`).
   Write `[INFERENCE]` on anything unobserved.
3. **Reproduce offline, then fix** in the main checkout: `python test_loop_lumber.py` at the repo root (the runner
   against simulated worlds, ~2 min; one scenario alone: `python test_loop_lumber.py <name>`), `python
   harness/test_lumber_opt.py` (the plan), `python harness/test_lumber_hatchets.py`, `python
   harness/test_lumber_forest.py`, `python harness/test_escape.py` (recalls, book parsing). The mining runner shares
   `loop_lumber.run_loop`: run `python test_loop_mining.py` too after touching it. Keep a failing-before/passing-after
   test where it catches a plausible regression. Large fixes may go to a `task` subagent, not isolated.
4. **Record and push** (Rule 0): the finding in docs/NOTES.md (a dated section, as "Hatchet shelf loop"), the runner
   rule in docs/LUMBER_LOOP.md §13 (§6 for the plan), the decision in docs/PLAN.md, a changed Seer procedure in
   docs/OVERSEER.md §5 "Lumber shifts". Commit only the fix's files (the user's uncommitted work stays out), push,
   and see the push succeed.
5. **Make it live.** Runner code (loop_lumber, lumber_opt, escape, agent_link, convert, stockpile, shelf, …) loads
   at `ctl run`: the next run has it; a run in progress keeps the old code. ctl.py loads per call. Proxy and
   world-model code (`harness/world/`, the proxy, the state port, the agent gate) needs a proxy restart: ask the
   user, with Dan at home and no run. §5 or overseer.md changes reach a running Seer only by message.
6. **Brief the Seer** (`write agent://<id>`): the commit, what changed, the log lines it writes now, whether to
   resume. Tell the user in one or two lines: the issue, the cause, the fix, the commit.

## 5. Ending

The user says stop: tell the Seer to end the shift (it runs `ctl stop --after-trip` and waits for `task_done` at
home, the boards in the Resource Stockpile; logs or boards still in the pack get `act convert --store`), wait for its
report, and pass the user its summary (runs, boards stored, spots tried, deaths or escapes) plus every fix made this
shift with its commit.
