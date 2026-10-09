# Games UX 2.0 — reconciliation against issue #194

Checked against `dev` at the time of writing. Each item is marked **done** (merged, with PR and commit),
**covered by existing tests** (no new PR needed), **manual** (cannot be automated here), **partial** (merged, with a named gap), or **deferred** (owner decision).
No item is marked done without a merged PR or an existing test.

| Item | Status | PR / commit | Evidence or reason |
|---|---|---|---|
| GUX-00 baseline inventory | partial | #204 → d93ab84 | Doc and contract test that reads the doc. The per-game phase-by-phase matrix required by the baseline DoD is not filled, so GUX-00 stays open (see §Gaps). |
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

- The per-game phase matrix in GUX-00 is not filled. The baseline doc requires it before GUX-14 and GUX-15 rely on the baseline, so GUX-00 is reported as partial until it is done or the owner accepts it as out of scope.
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
