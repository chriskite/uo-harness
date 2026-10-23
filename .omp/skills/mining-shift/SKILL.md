---
name: mining-shift
description: Run Outland Dan's mining shifts through a Seer (overseer) subagent per docs/OVERSEER.md §5 "Mining shifts", after checking his runebook still recalls to the DTF guild house and Logan's House, while this session watches and fixes every issue the Seer or the user reports. Use when the user asks to send Outland Dan mining, for mining runs or a mining shift, or to start the Seer for Dan.
---

# Mining shift: the Seer plays, you fix

Two roles. **The Seer** (project agent `overseer`, `.omp/agents/overseer.md`) runs Outland Dan's shift by
docs/OVERSEER.md §5 "Mining shifts" (the loop it shares with "Lumber shifts"): `ctl mining plan`, `ctl run` its
`command` verbatim, `ctl wait`, plan again, run after run until the user says stop. **You** (the main session)
check Dan's travel, start the Seer, watch it, and fix the code and docs behind every issue it or the user
reports. You never drive the game while it runs; it never edits code.

Read first: AGENTS.md (in context), docs/OVERSEER.md §5 "Lumber shifts", "Mining shifts", "Travelling somewhere
new", "Recall-reached spots" and §6; docs/PLAN.md "Mining runner: smelt in the field, keep the ingots"; docs/NOTES.md
"Mining: what the repo and the store know".

## 1. Before dispatch

1. Stack up and current: `python harness/stack.py status`. An `external … not ours` service is still old code
   (NOTES "Stack supervisor"); tell the user rather than restarting it.
2. `./ctl.cmd --char "Outland Dan" status > /tmp/s.json`, then with `jq.exe … < /tmp/s.json`: `ok`, `name`, `pos`,
   `facet`, `hits`, `weight`, `gate`, `tasks`, `open_junctures`, `overseer_heartbeat`, `gumps_open`, and the
   runebook (graphic `0x22C5`, 0x49865F8F as of 2026-10-23) in `backpack.items`. Home is Logan's House: the
   landing (3575, 3251), facet 0 (harness/data/homes.json).
3. **One Seer per character.** `read history://`: a `SeerOutlandDan*` or `DanMiningSeer*` agent `running` (or a
   heartbeat under 90 s old from another session) already owns Dan; message it (`write agent://<id>`) instead of
   starting another. Revive a parked one the same way when its context is still small; otherwise dispatch fresh.
4. **Runebook check: always when the user says they changed the book, else when the last check is old.** The
   runners recall by the entry index remembered from the last read (places.known_books; escape.recall: the index
   wins over the name), so a moved rune sends a stale run to the wrong place.
   - Read it: `./ctl.cmd --char "Outland Dan" act recall <book> --check`. Needs `ok`, `remembered: true`,
     `can_cast`, `charges`, and two runes: **"DTF Loot Chest"** landing near (4134, 1429) (the way into the guild
     house for every DTF-library landing, homes.json `library_rune`) and **"Logan's House"** landing at (3575,
     3251) (the way home and the red escape: the default, else by name; LUMBER_LOOP.md §6 "home"). Note each one's `i`.
   - Then when the user moved runes: prove it live from home. `act recall <book> --rune "DTF Loot Chest"` must
     return `to` ≈ [4134, 1429]; wait ~4 s; `act recall <book>` (the default) must return `to` = [3575, 3251], or
     `act recall <book> --rune "Logan's House"` when the default is something else. Each costs a charge.
   - Missing or wrong landing: tell the user, start nothing. A changed default or index: update the docs that name it
     (OVERSEER.md "Travelling somewhere new" / "Recall-reached spots", LUMBER_LOOP.md §6 "home") and push.
5. Anything off (dead, not at home, a task running, gate paused, open urgent junctures, under ~3 charges with
   no recall reagents): tell the user and dispatch only once they say how to proceed. Also glance at `./ctl.cmd
   --char "Outland Dan" mining plan` (`ok`, the pick's spot and landing; `ok: false` with `retry_in_min` means
   every spot is cooling down, which the Seer handles by waiting at home).

## 2. Dispatch

One `task` call; `agent: "overseer"` goes **on the `tasks[]` item** (a top-level `agent` is ignored: OVERSEER.md
§6). Name `SeerOutlandDan`, with a number when `history://` lists that name from an earlier session (omp refuses
the repeat). Don't set `isolated`: ctl must run the main checkout's code against the main store.

`context`: the goal, the state and the reporting contract below. `task`:

```
Act as the Seer for Outland Dan: follow docs/OVERSEER.md §5. Goal: mining shifts (§5 'Mining shifts') until the
user says stop. Pass --char "Outland Dan" on every ctl call.
State at dispatch (checked by the parent, <HH:MM>): <hits, pos, facet, at home, weight, gold, gate, tasks, open
junctures>. Runebook <serial>: <charges>, default <name> (entry <i>); "DTF Loot Chest" entry <i> → (x, y),
"Logan's House" entry <i> → (x, y)<, both recalled live at HH:MM>. Current plan pick: <spot, mode, landing>.
Runner changes since the last shift (commits <shas>): <what each changes and the log lines it writes>.
<Any change to §5's procedure the user asked for, else "§5 verbatim".>
Reporting contract (the parent session fixes code; you never do):
- Report every issue to the parent at once with `write agent://Main`: a runner error or traceback, a run that did
  something §5 says it must not, a stuck or confused run, a death, a recall that landed somewhere unexpected, ore
  or ingots left in the pack after a run ("no smelt at home: …"), a `smelt` event with ok: false, an `unworkable`
  event, `low_supplies` (pickaxe), a refused or failing ctl/act, anything the user reports as a bug in viz or
  Telegram chat (quote it with its chat id). Include: time, task id, log path, the exact log lines, Dan's
  pos/hits/weight, where he is now and what you did.
- Then keep the shift: get Dan home safely by §5 (`ctl stop --after-trip` unless it's an emergency), start no
  new run while the issue is open, and keep `ctl wait` going. A parent message wakes you; it says when the fix
  is in and what changed, and whether to resume.
- Deaths, possible staff and spot activations go to the user too (`ctl say "@user …"`); start nothing until they
  answer. Never activate a candidate spot without the user's OK.
- Yield your final report (what .omp/agents/overseer.md asks for) only when the shift ends.
```

## 3. Watch

Call `wait`. It returns on a message from the Seer, its end, the user, or a 30-minute cap.

- **Every return**, a read-only glance: `./ctl.cmd --char "Outland Dan" status` (hits, pos, weight, `intent`,
  `tasks`, `overseer_heartbeat`), the tail of `logs/tasks/<task_id>.log`, `ctl chat --role overseer --after N` and
  `ctl junctures --after N` (keep N from the last glance), and the mining job events (`ctl mining plan` shows the
  spots and cooldowns). A Seer silent while a run is in trouble, a heartbeat older than 90 s, or Dan idle outside
  the house with no run is an issue too: message the Seer or check `history://<id>`.
- **Never** `ctl wait` for Dan: it advances the Seer's shared cursors (`overseer_juncture_cursor`,
  `overseer_chat_cursor`) and the Seer misses those events. Never `ctl act`, `ctl run` or `ctl stop` while the
  Seer runs: have the Seer do it. The runebook check (section 1) is done before dispatch, not during a shift.
- The user may report to you here (steering) or to the Seer in viz/Telegram (it forwards). Treat both the same.
- The Seer yielded: read its report, fix what it lists (section 4), then dispatch a fresh Seer (section 2)
  unless the user ended the shift. Re-dispatch between runs when its transcript gets long (OVERSEER.md §7).

## 4. Fix

User reports are ground truth; don't re-verify them in game.

1. **Safety first.** Dan in danger or a run doing harm: tell the Seer what to do (`stop --after-trip`, or plain
   `stop` only for a GM, a server restriction or a run already somewhere wrong). Anything needing hands in the
   client (a ghost, a stuck gump) is the user's: ask them.
2. **Find the cause from evidence:** the task log, the session capture (`logs/session_*.jsonl`, `jq.exe -c
   'select(…)'`), the store's mining episodes and job events, the Seer's transcript (`history://<id>`). Write
   `[INFERENCE]` on anything unobserved.
3. **Reproduce offline, then fix** in the main checkout: `python harness/test_mining.py` (dig classifier, planner,
   store tables), `python harness/test_ore_nodes.py` (spots from the map), `python harness/test_escape.py` (recalls,
   book parsing), and the lumber runner's tests for the shared loop (`loop_lumber.run_loop`, home, travel). Keep
   a failing-before/passing-after test where it catches a plausible regression. Large fixes may go to a `task`
   subagent, not isolated.
4. **Record and push** (Rule 0): the finding in docs/NOTES.md (the mining section), the runner rule in
   docs/PLAN.md "Mining runner…", a changed Seer procedure in docs/OVERSEER.md §5 "Mining shifts". Commit only the
   fix's files (the user's uncommitted work stays out), push, and see the push succeed.
5. **Make it live.** Runner code (loop_mining, mining_opt, loop_lumber, escape, agent_link, …) loads at `ctl
   run`: the next run has it; a run in progress keeps the old code. ctl.py loads per call. Proxy and world-model
   code (`harness/world/`, the proxy, the state port) needs a proxy restart: ask the user, with Dan at home and no
   run. §5 or overseer.md changes reach a running Seer only by message.
6. **Brief the Seer** (`write agent://<id>`): the commit, what changed, the log lines it writes now, whether to
   resume. Tell the user in one or two lines: the issue, the cause, the fix, the commit.

## 5. Ending

The user says stop: tell the Seer to end the shift (it runs `ctl stop --after-trip` and waits for `task_done` at
home, the ingots in the Resource Stockpile), wait for its report, and pass the user its summary plus every fix
made this shift with its commit.
