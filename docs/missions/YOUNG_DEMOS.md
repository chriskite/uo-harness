# Mission: the "while still Young" demos (overseer-run)

The user asked for the overseer to run these itself, to see it in action (2026-09-30). They come
from docs/ROADMAP.md "Demos". The user is watching the client and the viz, answers captchas, and
chats through the viz Overseer tab.

The proxy records everything; the value is the capture plus your notes. For each demo, post what
you learned with `ctl say`, keeping it factual: packet/gump ids, texts, numbers, positions. Label
guesses as guesses.

Rules: docs/OVERSEER.md §5 and `harness/data/policy.json`.
- Never attack anything or anyone.
- Never answer the captcha.
- A renounce-Young prompt may only be closed.
- Stop and call the user (`ctl say "@user …"`) on anything unexpected: GM contact, jail, automation
  messages, repeated failures.

Order: 1 → 2 → 3 → 4. Death is last because it changes the character's state.

## 1. Hatchet wear
1. `ctl status`; find the hatchet: equipped, layer 2, serial `0x44ADB57A` per
   `harness/data/loops/lumber.json`.
2. `ctl act single_click 0x44ADB57A`. `heard` or `ctl journal` should show "(N uses remaining)".
3. `ctl run lumber --trips 1 --logs-per-trip 40` (about 20 chop attempts). `ctl wait`; a captcha
   is the human's.
4. When it ends: `single_click` again.
5. Report N before, N after, and the attempts and successes in the trip (the `task_done` data,
   or the episode). Per-attempt or per-success wear?

## 2. Vendor buy lists (don't buy anything)
1. Find the vendors: `status` mobiles with labels. Known on Shelter: "Sherwin the mage", "Zara the
   scribe" (docs/research/ECONOMY.md). The town is around the bank (1955,2583) and the inn
   (1932,2595).
   - If a vendor isn't in range, `ctl act goto <x> <y>` towards town.
   - If a label is missing, `single_click` the mobile.
2. For each of the mage, the scribe, and whoever sells hatchets (a provisioner, carpenter or
   tinker; find out):
   - `ctl act goto <serial>`
   - `ctl act menu <serial>`
   - pick the **Buy** entry: `ctl act menu_pick <serial> <index>`
   - read the list from `heard`/`journal`
   - send nothing else; the shop window stays open for the user to close
3. Report item → price for recall scrolls, blank runes, reagents (black pearl, bloodmoss,
   mandrake), hatchets, and anything a lumberjack needs.

## 3. The renounce-Young prompt (recognition only)
1. `ctl act goto 1985 2533 --range 2` (the Shelter moongate), then step onto the gate tile with
   `act walk`.
2. A gump should appear. Read it from `status` (gumps_open) or `journal`: gump id, texts, buttons.
3. **Close it**: `ctl act gump <serial> 0` (ctl allows only 0 on a renounce gump). If it can't be
   closed (noclose), stop and call the user. Never press anything else.
4. Report gump id, text and buttons, and whether you stayed Young (the journal or a status change).

## 4. Death and resurrection (New Player Dungeon)
1. Walk into the New Player Dungeon (marker at 1913,2566; the cave level at z −20 around the inn).
   Find a monster that attacks Young players, and stand near it. **Don't attack**; let it kill
   you. If nothing attacks within a few minutes, report back instead of improvising.
2. On death, read `status` and `journal`: body (ghost 0x192/0x193), position jump (Young
   transport to a healer?), gumps, messages, what stayed in the backpack.
3. Walk the ghost to a healer ("Marti the healer" / "Minka the healer", marker at 1908,2585):
   `ctl act goto <healer serial>`. The resurrection gump should appear.
   - **First time, close it** (`gump <serial> 0`, or the Cancel button if it's the only way).
   - Then step away and back so it is offered again, and accept (the Continue button).
   Report the gump id, text and buttons, and the re-offer distance and delay.
4. After resurrection: HP, what is equipped and in the pack (newbied items kept?), any robe.
   Walk back to the inn (`goto` the innkeeper).
5. Report the whole flow. Per policy: no corpse run, no `[TestRes`.

## When done
Post a summary with `ctl say`, then `ctl say "@user Young demos done"` and stop. The user then
takes TestWorth off Shelter and sets up the runebook and shelf.
