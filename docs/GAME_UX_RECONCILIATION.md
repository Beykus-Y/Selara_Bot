# Games UX 2.0 — reconciliation against issue #194

Checked against `dev` at the time of writing. Each item is marked **done** (merged, with PR and commit),
**covered by existing tests** (no new PR needed), **manual** (cannot be automated here), or **deferred** (owner decision).
No item is marked done without a merged PR or an existing test.

| Item | Status | PR / commit | Evidence or reason |
|---|---|---|---|
| GUX-00 baseline inventory | done, partial | #204 → d93ab84 | Doc and contract test that reads the doc. Per-game phase-by-phase matrix not yet filled (see §Gaps). |
| GUX-01–03 rematch permissions, safe stop, shared board | done | #197 → 136616e | `manage_games` gate on rematch, confirmed stop and Spy reveal, `/gameboard` control panel. |
| GUX-04–06 Dice, Quiz, Spy | done | #199 → d76a1c0 | Score table, versioned quiz buttons, Spy vote clarity. |
| GUX-07–09 WhoAmI, Bredovukha, Zlobcards | done | #200 → 6174ec0 | Versioned callbacks, round-scoped votes, numbered hand choices. |
| GUX-10–11 Bunker and Mafia | done | #201 → 1be7ac7 | Stale callback guards, timer races, round-scoped actions. |
| GUX-12 gacha journeys | done, partial | #195 → 7e9aa1c, #198 → 8e84fe0 | Separate pull and currency buttons, coin cost, onboarding, free-pull help, sell price. Empty-collection and banner copy were not re-audited line by line. |
| GUX-13 gacha safety | done | #195, #198, #203 → 7236d41 | Owner checks, in-flight duplicates, sold copies, subscription prompt, refusal, timeout and disabled-chat tests. |
| GUX-14 discovery and /help | covered by existing tests | #184 series (dev) | `test_help_callbacks.py::test_games_menu_reaches_every_launchable_game_with_rules`, `test_navigation_feature_cards.py`. Game group screens and gacha card are in the nav tree. No new PR needed. |
| GUX-15 eight-game tests | done, partial | #206 → 7ab9d46 | Bunker keyboard layout at 6/8/12 players and a Mafia full round through GameStore. Not every game has an end-to-end test. |
| GUX-16 iOS/Android smoke | manual | — | Not automatable here. Listed in `GAME_UX_TEST_COVERAGE.md`. Does not block closing #194. |
| GUX-17 docs and rollout | this document | — | Reconciliation only. No release notes, as nothing has been released from dev yet. |

## Deferred (owner decisions, not blockers)

- `spy_guess_location` in Telegram: exists only in the Mini App. Needs a product decision.
- Auto-timers for Bredovukha, Bunker and Quiz: change phase rules.
- Bunker public board redesign: waits on iOS/Android screenshots at 6/8/12 players, which are not required for this closure.
- Splitting `game/router.py`: needs its own RFC.
- Shared-board personalisation for admins: a shared Telegram keyboard cannot be personalised per user.

## Gaps and risks

- The per-game phase matrix in GUX-00 is not filled. It is a documentation gap, not a behaviour gap.
- Not every game has an end-to-end test. Coverage per game is listed in `GAME_UX_TEST_COVERAGE.md`.
- Multi-worker state ownership for GameStore is tracked in #65 and #67.
- Mafia timers after a restart are covered by the restored-timer tests in #201, but were not exercised against a live Redis. Bunker has no timer code on dev, so there is nothing to restore.
- Release: dev is ahead of main. Nothing is released until an explicit dev-to-main request.
