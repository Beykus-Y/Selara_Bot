# GUX-00 completion — Genshin/HSR group Gacha flow matrix

> **Post-change documentation only.** Source snapshot: `dev` at `88785b0` (9 October 2026), *after* GUX-01–11 and the later #204/#208 baseline PRs. This is **not** the original pre-change GUX-00 baseline; the historical sequencing gap cannot be erased. This supplements `GAME_UX_BASELINE.md` and `GAME_UX_PHASE_MATRIX.md`, which cover the eight `GameStore` games.
>
> Source of truth: `src/selara/presentation/handlers/text_commands.py` (**TC**) and `src/selara/application/use_cases/gacha/`. Gacha does **not** use `GAME_STORE` lobbies/phases. The rows below are user **journey steps**, not `GamePhase` enum values. Exact responses can depend on the gacha service and active settings. **No manual Telegram screenshots or end-to-end group transactions are claimed.**

## 1. Context, permissions and shared economics

- **Surface:** both Genshin and HSR can be played in the **same group chat** without requiring a move to personal messages. Commands are parsed as intents `gacha_pull`, `gacha_profile`, `gacha_info` (TC:6286–6314); commands include `гача генш` and `гача хср` (TC:1453–1457), and collection/profile `моя гача генш`, `моя гача хср` (TC:2543–2554).
- **Group account:** `_gacha_economy_mode` reads the **current group's** `chat_settings.economy_mode`; for private chats it uses `global` (TC:2163–2172). Group `chat_id` is supplied for coin balances and exchanges, so local and shared modes must not accidentally fall back to DM-wide economy (TC:2516–2539, 5487–5488, 5617–5630).
- **Gate:** `chat_settings.gacha_enabled` is checked in the text-intent entrypoint and callbacks (TC:6286–6292, 5480–5482). Subscription check is performed before callback mutation (TC:5484–5485), with a link to `@SelaraBot_Chanel` and notification cooldown (TC:1438–1448).
- **Owner binding:** the public inline controls contain `u{owner_user_id}`, and the callback handler checks it against `query.from_user.id` **before purchase, sale or settings changes** (TC:2306–2336, 5469–5478). A different member pressing the button is refused; public visibility is not authorization.
- **Duplicate guard:** `_GACHA_CALLBACK_IN_FLIGHT` prevents two simultaneous interactions on one Telegram message (TC:5417–5456). This guard is presentation-level and is not proof of provider-level exactly-once delivery.

## 2. Phase/action matrix for **each** banner

| User step | Trigger and on-screen action | Who / location | Result / error / transition | Code evidence |
|---|---|---|---|---|
| Discover Gacha | `гача инфо` (intent `gacha_info`) from group | Any enabled, subscribed participant; group | One informational message containing **both** Genshin and HSR summaries, balance/rank/collection, free and paid rules, owner-bound buttons | TC:2516–2593, 6286–6314 |
| Select banner | Choose the banner row in the info message: `Крутка • Геншин` or `Крутка • HSR` | Owner of generated controls; group | Paid pull for chosen banner, *not* currency purchase; other user's button refused | TC:2362–2414, 5469–5504 |
| First-time / empty collection | Banner summary with 0 unique cards | Reader; same group | `Коллекция пока пуста` and the exact free-pull text command; no mock cards displayed | TC:2456–2469 |
| Free pull | `гача генш` or `гача хср` | Sender; group | Free pull request. Banner-specific cooldown enforced in use case/service; notice tells user wait when unavailable | TC:1453–1457, 2754–2821, 2545–2548, 6293–6303 |
| Paid pull | `gacha:buy:genshin:u<ID>` / `gacha:buy:hsr:u<ID>` | Control owner; group | Price displayed before tap: **160 banner currency**. Provider operation may refuse insufficient funds/other errors. Animation optional; official result remains posted publicly | TC:1433–1435, 2381–2389, 5490–5568 |
| Buy banner currency | `gacha:currency:{banner}:{amount}:u<ID>` | Control owner; group | Button displays exact currency amount and cost in bot coins; uses current group's local/shared economy mode and `chat_id`. Refusal surfaced as toast | TC:2070–2074, 2310–2331, 2391–2402, 5617–5678 |
| Inspect balances | `гача инфо` again | Reader; group | Per-banner currency balances and current bot coin balance; failed banner loads reported individually | TC:2435–2476, 2516–2593 |
| View collection / history | `моя гача генш` or `моя гача хср` | Requesting player; group | Stats for chosen banner and recent pulls; empty history explicitly `нет` | TC:2479–2513, 2822–2844, 6304–6312 |
| Pull result | Separate result message following optional animation | Requesting player; group visible | Card name, rarity and linked owner; original result retained even when GIF unavailable | TC:2281–2303, 2596–2821, 5516–5529 |
| Sell permitted card | `gacha:sell:{banner}:{pull_id}:u<ID>` on result | Pull owner; group | Button shows sale price; provider validates sale. Successful sale removes the used button, errors shown without removing it | TC:2314–2355, 2417–2425, 5680–5706 |
| Animation choice | `gacha:animtoggle:u<ID>` | Control owner; group | Toggles only owner's presentation preference, refreshes info; not RNG/economy | TC:2358–2414, 5570–5615 |
| Disabled / unsubscribed | Any protected step | Participant; group | Gate/refusal before mutation, subscription link with cooldown; no hidden paid charge | TC:1438–1448, 5480–5488, 6286–6292 |
| Provider failure / ambiguous timeout | Any remote operation | Participant; group | `GachaUseCaseError` gives user-facing refusal; operational exceptions reported. Verify unknown-outcome/idempotency separately under gacha backend's contracts | TC:5490–5504, 5617–5638, 5684–5700 |

`<ID>` denotes the owner’s numeric Telegram ID, never a public shared authorization token. Price constants are pinned to the code **on this snapshot**; any later price updates must update both the code and this matrix.

## 3. Contract/snapshot fixtures

`tests/unit/test_game_gux_gacha_baseline.py` pins **structural** properties: two distinct banner choices, paid vs currency rows, callback owner IDs, no unexpected cross-banner callback, 64-byte limit, banner empty-collection help and safe no-custom-emoji fallback. These are intentionally not screenshot or fragile whole-paragraph golden files; custom emoji IDs and account balances are environment-specific.

**Remaining scope of the original GUX-00 DoD:** stable sample render/keyboard fixtures for **each phase of all eight GameStore games**, a historical pre-change comparison with commit before #197, and real Telegram 6/8/12-player Bunker screenshots. The eight-game phase matrix already exists in #208 and should not be duplicated. Real group transactions, mobile UX and negative cases (blocked subscription, insufficient coins, expired cool-down, service unavailable, stale/forged callback, duplicate request) remain part of GUX-12/13/15/16 acceptance and must be marked as unverified until tested, not silently turned green by this documentation PR.
