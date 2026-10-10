# Games UX 2.0 — automated coverage and manual acceptance (GUX-15 / GUX-16)

Snapshot after #219, #221, #222, #223 and #224 merged into `dev` on
10 October 2026. This describes **backend GameStore state-machine tests**, not
complete Telegram E2E verification. See #194 for the complete release DoD.

## Automated GameStore lifecycles — eight of eight

| Game | Test | Verified path and sample |
|---|---|---|
| Dice / Дуэль кубиков | `tests/unit/test_game_gux15_dice_spy_quiz_lifecycles.py` (#219) | 2-player lobby → throws → winner → finish/new lobby, duplicate and cross-chat guard |
| Spy / Найди шпиона | same (#219) | 3-player lobby → secret-role assignment → majority vote → civilian win; stale and cross-chat votes refused |
| Quiz / Викторина | same (#219) | 2-player lobby → every question and answer → scores/winner; early resolution, stale question and cross-chat refusals |
| Mafia / Мини-мафия | `tests/unit/test_game_mafia_full_round_e2e.py` (#206) | 6-player lobby → night actions → night result → day vote → execution confirmation → next night/finish |
| WhoAmI / Кто я | `tests/unit/test_game_gux15_whoami_lifecycle.py` (#221) | 3-player lobby → question → answer → turns → all guess identities → finish; invalid actor, stale and cross-chat refusals |
| Bredovukha / Бредовуха | `tests/unit/test_game_gux15_bredovukha_lifecycle.py` (#222) | 3-player lobby → category → private lies → public vote → every round → score/final; stale/cross-chat guards |
| Bunker / Бункер | `tests/unit/test_game_gux15_bunker_full_lifecycle.py` (#223) | 6-player cards → each reveal → each vote/elimination → final seats; stale and cross-chat guards |
| Zlobcards / 500 Злобных Карт | `tests/unit/test_game_gux15_zlobcards_lifecycle.py` (#224) | 3-player private selection → public vote → all rounds → final; self-vote and stale/cross-chat guards |

All five new PRs listed above passed their full GitHub Actions CI checks
before merging. The Mafia test predates this wave. Passing tests demonstrate
these **specific sampled paths**, not every phase variant, player count, bot
restart, network failure, device, or permission role.

## Additional automated presentation contracts

| Area | Test | What is protected |
|---|---|---|
| Bunker private reveal and vote keyboards (6, 8, 12) | `tests/unit/test_game_bunker_keyboard_layout.py` | Hidden/revealed fields, target restrictions, row layout, labels, UTF-8 callback limits |
| Bunker/Mafia stale phase actions and timer guards | `tests/unit/test_game_gux_bunker_mafia_guards.py` (#201) | Stale callbacks/rounds are not allowed to mutate newer game state |
| Zlobcards early submit → voting timer | `tests/unit/test_game_gux_zlob_vote_timer_handoff.py` (#209) | Successful transition schedules timer for public vote |
| Gacha Genshin and HSR public-button contract | `tests/unit/test_game_gux_gacha_baseline.py` | Two distinct banners, owner-bound buttons, paid vs currency options, empty-collection guidance |
| Gacha callback authorization and failure paths | `tests/unit/test_text_commands_gacha_callbacks.py` (#203) | Ownership, duplicate actions, subscription, disabled-chat refusals, timeout and sell errors |

**Gacha is not a `GameStore` game.** Its backend transactions use a separate
service, so the eight-of-eight number excludes Gacha. Structural callback tests
do not prove a real group purchase/sale across its service boundary.

## Not yet verified — required for final #194 sign-off

- **GUX-16 live devices:** iOS and Android Telegram walkthroughs for all eight
  games and Genshin/HSR Gacha; Desktop is supplemental. Specifically test Bunker
  public boards at 6/8/12 players and Zlobcards long/private hands.
- **True transport end-to-end:** a second/third Telegram user, DM not started /
  blocked, real deep links and callback retries, mid-game reconnect, old buttons,
  slow provider, simultaneous group actions, and accurate user-visible errors.
- **Broader backend variants:** full combinatorial roles for Mafia, Bunker 8/12
  players beyond keyboard shape, multiple rounds of unusual events, realistic
  redis outage/recovery, all economic rewards and exact-once side effects.
- **Gacha transactions:** actual Genshin/HSR free pull, paid pull, coins→banner
  currency exchange, sale, collection, disabled gate, rate-limit, unknown
  payment/provider outcome and read-after-write balances. No fake real charges.
- **Zlobcards private timeout with fewer than two answers:** a documented
  unresolved recovery-policy choice. Do not silently weaken the requirement for
  at least two submitted answers or claim its idle state is fixed.
- **Screenshots and copy:** before/after readable board/keyboard captures,
  Unicode/long usernames, labels and private spoilers; complete per-game QA
  checklists in #194 remain to be signed off.

## Low-risk known issue

Bunker `gbkr/gbkv ... :noop` refresh callbacks do not carry the round number.
They re-render current state without mutation; they must not trigger reveals,
votes, rewards, or a replacement game. Recorded in #201 as LOW.

**Release policy:** CI on `dev` passing is a necessary condition, not sufficient
for full #194 or #184 completion. No `dev` → `main` release is established by
these tests. Only mark manual checks complete after performing them.
