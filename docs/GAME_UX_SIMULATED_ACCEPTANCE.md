# Games UX simulated acceptance — #194

On 10 October 2026 the owner requested simulated results as the merge gate.
This report records what actually ran. It does not claim live Telegram devices,
real provider purchases, production deployment, or restore a historical baseline.

## Reproducible game-size scenarios

`tests/unit/test_game_gux15_bunker_full_lifecycle.py` now walks complete games at
6, 8 and 12 players: every sequential reveal, every elimination vote and the
final seats (2, 2 and 5 respectively). Each phase exercises foreign-chat and
stale-action rejection. Final winners and repeat-resolution denial are checked.

`tests/unit/test_game_mafia_full_round_e2e.py` now walks 4, 6 and 10-player
sampled rounds with RNG seed 0: real role assignment, one night action per
acting player (first valid target), night outcome, discussion, day vote,
execution confirmation and finish/next night. The seeded allocations are
4 players: one Mafia and three civilians; 6 players: Doctor, Commissar, two
Mafia and two civilians; 10 players: ten roles including Красотка, Оборотень,
Отравитель, Психолог and Телохранитель. These are three role allocations, not
all special-role permutations. The original six-player path remains in the
same parameterized test.

Run both with the repository's frozen development dependencies:

```sh
uv sync --frozen --extra dev
uv run --no-sync pytest -q tests/unit/test_game_gux15_bunker_full_lifecycle.py tests/unit/test_game_mafia_full_round_e2e.py
```

## Executed regression set — 104 passed

All paths below are under `tests/unit/`. This set was run together with
`--junitxml=/tmp/simulated-ux-194.xml` and passed on 10 October 2026.

| Simulated scenario | Test file | Boundary and result |
|---|---|---|
| Dice/Spy/Quiz complete lifecycles | `test_game_gux15_dice_spy_quiz_lifecycles.py` | Real GameStore, winners, stale/cross-chat actions |
| WhoAmI free-text questions/answers/guesses | `test_game_gux15_whoami_lifecycle.py` | Real GameStore, all sampled identities guessed, finish |
| Bredovukha category/lie/vote/score | `test_game_gux15_bredovukha_lifecycle.py` | Real GameStore, every round through finish |
| Zlobcards private selection/public vote | `test_game_gux15_zlobcards_lifecycle.py` | Real GameStore happy path, self-vote/stale denial |
| Bunker complete 6/8/12 games | `test_game_gux15_bunker_full_lifecycle.py` | Real GameStore, bounded eliminations to final seats |
| Mafia sampled 4/6/10 rounds | `test_game_mafia_full_round_e2e.py` | Real GameStore, reproducible sampled roles |
| Bunker private buttons, 6/8/12 | `test_game_bunker_keyboard_layout.py` | Actual keyboard builder, target restrictions/layout/callback bytes |
| Blocked DM and next-player warning | `test_game_bunker_ux.py`, `test_game_mafia_night_dm_failure.py`, `test_game_warn_on_failed_dm_helper.py` | Fake Telegram Bot raising delivery errors; group warning |
| Private-phase DM deep links | `test_game_dm_deep_links.py` | Keyboard builder output only: `?start=game_<id>` on private-phase DM buttons; no handler or Bot API call |
| Stop/reveal permissions and phase race | `test_game_gux_core_lifecycle.py` | Actual handler/store contracts, simulated callbacks |
| Rematch manager gate, cross-chat and double click | `test_game_lobby_leave_and_rematch.py` | Actual handler with fake query; run separately, not part of the 104-test set |
| Stale Bunker/Mafia phase and timer races | `test_game_gux_bunker_mafia_guards.py` | Actual handler/store logic, simulated timer/callback context |
| Genshin and HSR transaction sequences | `test_gacha_http_two_banner_transaction_contract.py` | Real HTTP client with stateful mock service: currency, duplicate key, paid pull, collection/profile, ownership/sale, banner isolation |
| Gacha failures and repeated/foreign clicks | `test_text_commands_gacha_callbacks.py` | Fake service/Telegram, insufficient coins, timeout, disabled gate, subscription, animation, duplicate sale/callback |
| Local/global coin explanation | `test_gacha_group_payment_context_ux.py` | Actual rendered purchase copy, mocked context |
| Gacha banner/button/empty-state contracts | `test_game_gux_gacha_baseline.py` | Actual presentation builders, simulated collections |

## Additional Dice/Spy player-count regression coverage

A later #194 follow-up adds five GameStore scenarios in
`tests/unit/test_game_gux15_dice_spy_quiz_lifecycles.py`, independent of
the historical 104-test run above:

- Dice with **4 and 10 players**: distinct actual participants join, every
  roll is recorded once, early repeat/foreign-chat actions fail, the final
  roll resolves the winner, and old rolls are rejected after completion.
- Spy with **5 and 10 players**: confirm one/two spies respectively and
  require an actual majority (3/6 votes) before exposing the winner.
- Spy with **10 players**, a 5:5 split: no candidate is selected, the
  game only ends after all ten votes, and the spy wins the tie.

These are deterministic method-level test fixtures, not real Telegram
interaction, device QA, a comprehensive Spy re-vote matrix or a live
provider/economy acceptance. The new scenarios are gated by GitHub CI
separately; they must not be counted in the historical 104-pass result.

## Limits of the accepted simulation

Real Telegram text wrapping, accessibility, touch accuracy, live multi-account
DM delivery, network reconnection, subscription/payment/provider finality and
Redis process restarts are not established by this set. Backend GameStore
lifecycles do not execute actual economic reward settlement. Gacha mock-service
transactions do not charge or verify a real external provider.

The separate post-change phase fixtures pin all eight games' public boards and
manager keyboards. Synthetic Chromium captures generated from those renderers
are browser approximations and must be labeled as such; they cannot be described
as before/after screenshots taken on real iOS or Android Telegram clients.

The bounded Zlobcards fewer-than-two-answers timer policy belongs to #230 and its
own fix/regressions. Do not infer it passes from the happy-path lifecycle here.
No dev-to-main release or deployment was performed by this acceptance work.
