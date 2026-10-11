# Games UX 2.0 — reconciliation against issue #194

Checked against `dev` at the time of writing. Each item is marked **done** (merged, with PR and commit),
**covered by existing tests** (no new PR needed), **manual** (cannot be automated here), **partial** (merged, with a named gap), or **deferred** (owner decision).
No item is marked done without a merged PR or an existing test.

| Item | Status | PR / commit | Evidence or reason |
|---|---|---|---|
| GUX-00 baseline inventory | done | #204 → d93ab84, matrix in `GAME_UX_PHASE_MATRIX.md` (merged with this change) | Doc, contract test that reads the doc, and the per-game phase → keyboard → actor → surface → error → transition matrix for all eight games, cited from code. |
| GUX-01–03 rematch permissions, safe stop, shared board | done | #197 → 136616e | `manage_games` gate on rematch, confirmed stop and Spy reveal, `/gameboard` control panel. |
| GUX-04–06 Dice, Quiz, Spy | done | #199 → d76a1c0 | Score table, versioned quiz buttons, Spy vote clarity. |
| GUX-07–09 WhoAmI, Bredovukha, Zlobcards | done | #200 → 6174ec0 | Versioned callbacks, round-scoped votes, numbered hand choices. |
| GUX-10–11 Bunker and Mafia | done | #201 → 1be7ac7 | Stale callback guards, timer races, round-scoped actions. |
| GUX-12 gacha journeys | partial | #195 → 7e9aa1c, #198 → 8e84fe0 | Separate pull and currency buttons, coin cost, onboarding, free-pull help, sell price. Empty-collection and banner copy were not re-audited line by line. |
| GUX-13 gacha safety | done | #195, #198, #203 → 7236d41 | Owner checks, in-flight duplicates, sold copies, subscription prompt, refusal, timeout and disabled-chat tests. |
| GUX-14 discovery and /help | covered by existing tests | #184 series (dev) | `test_help_callbacks.py::test_games_menu_reaches_every_launchable_game_with_rules`, `test_navigation_feature_cards.py`. Game group screens and gacha card are in the nav tree. No new PR needed. |
| GUX-15 eight-game tests | partial | #206 → 7ab9d46 | Bunker keyboard layout at 6/8/12 players and a Mafia full round through GameStore. Not every game has an end-to-end test. |
| GUX-16 iOS/Android smoke | manual | — | Not automatable here. Device checklist below; not yet run. Listed in `GAME_UX_TEST_COVERAGE.md`. |
| GUX-17 docs and rollout | this document | — | Reconciliation only. No release notes, as nothing has been released from dev yet. |

## Deferred (owner decisions, not blockers)

- `spy_guess_location` in Telegram: exists only in the Mini App. Needs a product decision.
- Auto-timers for Bredovukha, Bunker and Quiz: change phase rules.
- Bunker public board redesign: waits on iOS/Android screenshots at 6/8/12 players, which are not required for this closure.
- Splitting `game/router.py`: needs its own RFC.
- Shared-board personalisation for admins: a shared Telegram keyboard cannot be personalised per user.

## Gaps and risks

- Code divergences found while writing the matrix (`GAME_UX_PHASE_MATRIX.md` §5), not fixed here: Mini App cancel and reveal skip confirmation (D1); the Telegram zlobcards early-submit path schedules no vote timer (D2); the bunker vote path has no chat-id check (D3); the mafia group day-vote board offers targets the handler refuses (D4).
- Not every game has an end-to-end test. Coverage per game is listed in `GAME_UX_TEST_COVERAGE.md`.
- Multi-worker ownership of GameStore is guarded by the single-writer lease from #87 and #140, which predates this wave. #65 (reconciliation after Redis outage) and #67 (per-game lock granularity) are separate merged fixes, not ownership work.
- Timers after a restart: `restore_phase_timers` (`src/selara/presentation/handlers/game/router.py`) restores Mafia and Zlobcards timers. No test on dev calls it directly. #201 adds a stale-timer guard test for Mafia. Nothing was exercised against a live Redis. Bunker has no timer, so there is nothing to restore.
- Rollout: at the time of writing `origin/main` is `befaac9` and `origin/dev` is `7ab9d46`. None of the implementation commits above (136616e, d76a1c0, 6174ec0, 1be7ac7, 7e9aa1c, 8e84fe0, 7236d41, d93ab84, 7ab9d46) is an ancestor of main. Nothing is released until an explicit dev-to-main request.

## GUX-16 device checklist (not run)

Run each game on the iOS and Android Telegram clients: start, join, play one round, finish, then rematch. Tick a box only after the device run.

- [ ] Zlobcards (500 Злобных Карт): iOS [ ] Android [ ]
- [ ] Spy (Найди шпиона): iOS [ ] Android [ ]
- [ ] WhoAmI (Кто я): iOS [ ] Android [ ]
- [ ] Mafia (Мини-мафия): iOS [ ] Android [ ]
- [ ] Dice (Дуэль кубиков): iOS [ ] Android [ ]
- [ ] Quiz (Викторина): iOS [ ] Android [ ]
- [ ] Bredovukha (Бредовуха): iOS [ ] Android [ ]
- [ ] Bunker (Бункер), public board at 6, 8 and 12 players: iOS [ ] Android [ ]


---

## 10 October 2026 follow-up — current `dev` status

The original table above is a historical snapshot and must not be interpreted
as the present release gate. Since it was written:

- GUX-09–12, destructive-action confirmation, and Gacha baseline fixes were
  integrated through #209–#215 (see #194 issue timeline).
- GUX-14 / help: additional discovery and in-place pagination fixes were merged
  via #216 and #217; real Telegram UX acceptance remains tracked in #184.
- **GUX-15 state-machine lifecycles for all eight `GameStore` games now exist:**
  Mafia (#206), Dice/Spy/Quiz (#219), WhoAmI (#221), Bredovukha (#222),
  Bunker (#223), Zlobcards (#224). See the up-to-date
  `docs/GAME_UX_TEST_COVERAGE.md` for the precise sample sizes and limits.
- **Gacha is independent of `GameStore`** and remains outside that eight-game
  count. The group Genshin/HSR journey inventory and existing callback tests
  are recorded in `docs/GAME_UX_GACHA_PHASE_MATRIX.md`, but a complete
  real-service transaction walkthrough is still not documented as done.
- **GUX-16 is still OPEN:** no complete real iOS/Android multi-user evidence,
  before/after screenshots, or Bunker 6/8/12 device sign-off was provided.
- **Zlobcards idle private phase with fewer than two submissions** still needs
  an explicit product recovery decision; the early-submit timer fix does not
  resolve this distinct state.

Do not close the #194 epic or issue a production release based on automated
coverage alone. `main` has not been updated by this follow-up.
