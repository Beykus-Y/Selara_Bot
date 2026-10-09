# Games UX 2.0 — automated coverage (GUX-15 / GUX-16)

Scope: what is proven by automated tests on `dev`, and what is not automated.
Screenshots and manual device runs are not part of the required checks. Behaviour is
pinned by unit tests that run in CI.

## Automated

| Area | Test file | What it pins |
|---|---|---|
| Bunker private reveal keyboard (fixed six-player game) | `tests/unit/test_game_bunker_keyboard_layout.py` | nine hidden fields plus refresh, one button per row, unique callbacks, each callback ≤ 64 bytes UTF-8, revealed fields removed |
| Bunker private vote keyboard at 6/8/12 players | same | every other alive player once, self excluded, labels truncated to 24 characters, current choice marked |
| Mafia full round through GameStore (6 players) | `tests/unit/test_game_mafia_full_round_e2e.py` | lobby → start → all night actions → night resolution → day vote → execution confirmation → next night, or a Mafia win by parity |
| Bunker/Mafia stale callbacks and timers | `tests/unit/test_game_gux_bunker_mafia_guards.py` (from #201) | old round/turn/phase callbacks do not mutate state |
| Gacha purchase/sale/currency callbacks | `tests/unit/test_text_commands_gacha_callbacks.py` | owner check, in-flight duplicates, sold copies, subscription prompt. Refusal, timeout and disabled-chat tests are added in #203 |

## Not automated (documented, not a blocker)

- **iOS and Android rendering of the Bunker public board** at 6/8/12 players. Telegram's
  layout on a physical device cannot be checked in CI. The keyboard structure is covered above;
  the public board text is covered by `test_game_gux_bunker_mafia_guards.py`. A device check
  stays a manual task for the owner.
- **Telegram Desktop checklist.** Same as above: manual, optional.
- **Live multi-user Telegram walkthroughs** (DM delivery, real timers over minutes). Covered at
  store level by the Mafia test and by the existing DM-failure tests; real-network timing is manual.

## Known non-blocking notes

- The `🔄 Обновить` refresh buttons on Bunker reveal and vote (`gbkr/gbkv ... :noop`) do not check
  the round number. They only re-render the current snapshot and edit the board with the current
  progress, so a stale click shows current data and changes nothing. Recorded in the #201 review
  as LOW; deliberately not changed here.
