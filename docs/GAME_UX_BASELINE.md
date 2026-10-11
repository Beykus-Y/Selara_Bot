# Games UX 2.0 — baseline inventory (GUX-00)

Snapshot of `dev` at `6174ec0` (after #200, GUX-07–09 merged). This is the post-change baseline:
it records what the code does now, not the behaviour before GUX-01. The historical starting point
is available at the commit before #197 if needed.

Status tags: **[fixed]** shipped in dev, **[confirmed]** reproduced or proven by code reading and still open,
**[hypothesis]** needs manual or E2E confirmation, **[owner]** needs a product decision.

## 1. Launchable games (`GAME_LAUNCHABLE_KINDS`)

| kind | title | min players | secret roles | pinned by |
|---|---|---|---|---|
| zlobcards | 500 Злобных Карт | 3 | no | `test_game_baseline_contract.py` |
| spy | Найди шпиона | 3 | yes | same |
| whoami | Кто я | 3 | yes | same |
| mafia | Мини-мафия | 4 | yes | same |
| dice | Дуэль кубиков | 2 | no | same |
| quiz | Викторина | 2 | no | same |
| bredovukha | Бредовуха | 3 | no | same |
| bunker | Бункер | 6 | yes | same |

`«Угадай число»` is intentionally absent and must not be restored.

Gacha (Genshin / HSR) is a separate product in `text_commands.py` and is not a game kind.
Per owner decision it stays available in group chats.

## 2. Phases (`GamePhase`)

`lobby`, `freeplay`, `whoami_ask`, `whoami_answer`, `category_pick`, `private_answers`, `public_vote`,
`bunker_reveal`, `bunker_vote`, `night`, `day_discussion`, `day_vote`, `day_execution_confirm`, `finished`.

The contract test pins this list, so any new phase must update this document in the same PR.

## 3. Status of the #194 items at this snapshot

| Item | Status | Where |
|---|---|---|
| Rematch uses the `manage_games` gate, no auto-join | [fixed] | #197 (GUX-01) |
| Safe confirmation for stop and Spy role reveal, atomic expected-state finish | [fixed] | #197 (GUX-02) |
| Shared board separates participant actions from manager controls, `/gameboard` | [fixed] | #197 (GUX-03) |
| Dice, Quiz, Spy UX and versioned quiz buttons | [fixed] | #199 (GUX-04–06) |
| WhoAmI, Bredovukha, Zlobcards UX and versioned callbacks | [fixed] | #200 (GUX-07–09) |
| Bunker and Mafia stale-callback guards and timer races | [fixed] merged into dev after this snapshot, at 1be7ac7 | #201 (GUX-10/11) |
| Bunker public board redesign | [owner] deliberately not done before iOS/Android screenshots at 6/8/12 players | #194 comment |
| Gacha purchase and sale UX, subscription prompt, owner checks | [fixed] | #195, #198, GUX-13 tests |
| Telegram iOS/Android smoke for all eight games | [confirmed] not performed, no device access in the coding environment | GUX-16 |
| Context-specific admin controls in a shared Telegram board | [owner] architectural: a shared keyboard cannot be personalised per user | #194 §2.1 item 4 |
| `spy_guess_location` in Telegram | [owner] deferred; exists only in Mini App | GAME-FUTURE-A |
| Auto-timers for Bredovukha, Bunker, Quiz | [owner] deferred; changes phase rules | GAME-FUTURE-B |
| Splitting `game/router.py` | [owner] deferred to its own RFC | GAME-FUTURE-C |

## 4. Per-game phase matrix

The matrix of phase → visible keyboard → actor → chat or DM → error → transition for all eight games is in
`docs/GAME_UX_PHASE_MATRIX.md`. It is read from code at `dev` a9bc99d, with file and line citations on every row.
Its section 5 lists four divergences found while reading (D1–D4) and three observations. They are follow-up
candidates, not fixed in GUX-00.

## 5. Stable post-change public presentation fixtures

`tests/unit/test_game_phase_render_fixtures.py` pins 32 deterministic samples in
`tests/fixtures/game_phase_render/`: lobby and finished for each of the eight
games, plus every game-specific active phase. The inventory test checks all
eight launchable kinds and all fourteen `GamePhase` values, and rejects missing
or extra fixture files.

Each JSON file records complete HTML text (as readable lines), public keyboard
rows, callback payloads and deep links, and the separate manager keyboard.
Mafia's execution-confirmation feed text and keyboard are pinned separately
because they are not rendered on the shared board. Samples include partial
progress, scores, HTML-special characters and Unicode labels. Sentinel private
values must stay absent from active public boards; callback payloads must fit
Telegram's 64-byte UTF-8 limit.

These fixtures capture the **current post-change contract**, not the missing
historical pre-GUX-01 baseline. They do not cover every private role/hand, player
count, error path, or prove Telegram iOS/Android readability. Existing focused
private-keyboard and callback tests still apply; GUX-16 real-client screenshots
and walkthroughs remain required before closing #194.

When an intentional presentation change causes a failure, review the actual
text/keyboard diff and update only the affected JSON fixture in the same PR.
The test never rewrites expectations. To regenerate a reviewed sample locally,
use `render_sample(kind, phase)` from the test module and serialize its result
with `json.dumps(..., ensure_ascii=False, indent=2)` plus a trailing newline.
