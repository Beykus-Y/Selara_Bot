# Games UX Modernization — read-only audit + roadmap

Status: **read-only audit complete, no code changed**. Per Ilya's instruction: no push,
no deploy, no implementation until this plan is reviewed. Source of truth for every
claim below is current `main` (`src/selara/presentation/handlers/game/router.py`,
`src/selara/presentation/game_state.py`, `src/selara/core/roles.py`), not
`USER_GUIDE.md` or prior TODOs.

Method: 4 parallel read-only investigations (catalog/lobby; DM+secret-role+state-safety;
Mafia/Spy/WhoAmI/Bunker; Dice/Quiz/Number/Bredovukha/Zlobcards), each tracing every
real game end-to-end as a first-time player with zero documentation knowledge,
answering at every step: *what does the user see → what should they understand → what
should they do next → is it obvious without docs?* A "no" on the last question is a
UX problem by definition.

**Hard constraints respected in this audit** (per Ilya): game mechanics/balance/roles/
probabilities are not touched or proposed for change, only presentation/interaction.
`GameStore`/game-state architecture is not proposed for a rewrite. UX problems are kept
separate from technical debt. No existing text shortcut is proposed for removal without
a stated reason. Anything that's a genuine product-behavior decision, not derivable from
current code, is marked **[?]** below for Ilya to decide.

---

## 1. Map of current UX

```
/game (needs manage_games permission — see [?] #1)
  │
  ├─ no args → catalog: 8 vertical title-only buttons (one message)
  │
  └─ explicit kind → same as tapping a catalog button
        │
        ▼
  game:new:<kind> → creates lobby, edits/sends ONE board message
        │
        ▼
  lobby: "➕ Присоединиться" / per-kind config (category/rounds/seats — often
         3 stacked buttons per setting) / "🎬 Старт" / "🛑 Отменить"
         (join/start already edit the board in place — no "leave" button exists)
        │
        ▼
  start → roles/cards DMed to players who have opened a DM with the bot
         (deep-link buttons on the board: "🕵️ Моя роль" etc. →
          t.me/<bot>?start=game_<id> → auto-shows role/card)
        │
        ▼
  game phases (per-kind, see individual sections) — board edits in place;
  2 of 8 games (mafia, zlobcards) also have server-side phase timers with
  restart-safe recovery; the rest advance only via a manual tap
        │
        ▼
  result → winner text on the board. No game has a rematch button —
  the only way to play again is to manually run /game from scratch.
```

**[?] #1 — biggest structural finding, needs Ilya's decision before any catalog
redesign is worth investing in:** `/game` (router.py:3537) requires the
`manage_games` bot permission. Under the default role templates
(`src/selara/core/roles.py`), the default `participant` role (rank 0 — what every
ordinary group member has) does **not** have `manage_games`; only `senior_admin`+
does. If this is accurate in production, an ordinary member in a chat that hasn't
customized roles cannot open the game catalog at all — they'd need an admin to grant
them `manage_games` first. Please confirm whether this is intentional (games are
meant to be admin-launched events) or a bug/regression, since it changes how much
the catalog/pagination work below actually matters relative to fixing this gate.

> **Резолюция Ilya:** так и задумано — оставить как есть, не менять.

---

## 2. Per-game audit

### Mafia (min 4 players, secret roles)
- **Current flow:** lobby → roles DMed → night (role-specific private action
  keyboards DMed, e.g. sheriff check, mafia kill) → `_advance_mafia_night` resolves,
  personal DM reports sent → day discussion → day vote (buttons on the public
  board **and** a separate private voting card — dual surfaces) → execution
  confirmation (separate message) → repeat until a win condition.
- **UX problems:**
  - Night-phase DM failures are **silently swallowed** (`continue` on
    `TelegramForbiddenError`, router.py:2831-2907) — no count, no board warning.
    Every other game with private phases (bunker/zlobcards/bredovukha) shows an
    "ЛС недоступно: N игрок(ов)" line on the board; mafia's night phase doesn't.
    A blocked player is invisibly unable to act, every night, forever.
  - Every phase transition also posts a separate silent feed-event message
    (`disable_notification=True`) in addition to editing the board — the board
    stays current, but the chat still accumulates a scrolling trail of
    near-duplicate messages over a full game.
  - No in-game rules explanation anywhere (see §3, universal to all 8 games).
- **Proposed flow:** add the same "ЛС недоступно" board line to the night phase.
  Fold feed-events into board history or make them collapsible instead of
  separate messages. Add a `❓ Правила` button on the start card.
- **Что унифицировать:** the failed-DM warning helper (exists in 3/4 secret-role
  games, missing here — should be one shared call site, not duplicated per game).
- **Что уникально:** night-action role variety (sheriff, veteran, journalist,
  child) — genuinely bespoke.
- **Risk of changing:** Medium (DM-warning fix is low-risk/additive; touching the
  board+feed dual-message pattern risks losing an audit trail some players read).

### Spy (min 3 players, secret roles)
- **Current flow:** lobby → roles DMed (spy doesn't know the location, everyone
  else does) → free-play accusation phase: vote via buttons (name + live count),
  re-votable → majority triggers resolution → roles revealed, game ends.
  Already fully button-driven — no required text.
- **UX problems:**
  - `GameStore.spy_guess_location` (game_state.py:2198) — the classic
    "spy guesses the location to win outright" counter-play — is fully
    implemented in the state layer but **has zero callback or button wired to
    it anywhere in router.py.** Either dead/removed-feature code, or a real
    half-shipped mechanic. **[?] #2 — needs Ilya's call**: wire it up (new UI:
    a "🕵️ Назвать локацию" button shown only to the spy), or leave it
    unreachable (out of scope since "no mechanic changes" — but dead code is
    worth a note either way).
    > **Резолюция Ilya:** подключить, но позже — не в текущем заходе.
  - No in-game rules explanation of the win condition.
- **Proposed flow:** keep the button-accusation pattern as-is (it's good). Add
  `❓ Правила`. Location-guess wiring is Ilya's call, not a default UX fix.
- **Что унифицировать:** the vote-button-with-live-count pattern is nearly
  identical to mafia's day vote and bunker's elimination vote — shared-component
  candidate (see §7).
- **Что уникально:** single-phase, no night/day cycle — simplest of the 4
  secret-role games.
- **Risk of changing:** Low.

### WhoAmI (min 3 players, secret roles)
- **Current flow:** lobby → cards assigned (nobody knows their own identity) →
  turn-based: current actor types a free-text question containing `?` in the
  group chat → table answers via 4 buttons (Да/Нет/Не знаю/Неважно) → turn
  advances → at any point the actor can guess, **but only by typing one of three
  exact phrases** — "я думаю, что я X" / "моя догадка: X" / "кажется, я X" — no
  button equivalent exists anywhere.
- **UX problems — this is the exact case Ilya named as the target example:**
  - The guess mechanic is completely undiscoverable without prior knowledge of
    the magic phrases. The status text says "делает догадку" but never shows an
    example. A first-time player has no way to learn how to act.
  - Questions without a literal `?` are silently dropped with zero feedback —
    a valid-looking question just vanishes.
- **Proposed flow:** add a persistent `🎯 Угадать себя` button, visible only to
  the current actor, that either opens a text-prompt or is paired with an
  explicit example shown in the status text ("Например: «Я думаю, что я ...»").
  Keep free-text *questions* as-is (asking questions is the actual gameplay,
  correctly not buttonized) but reply with a hint instead of silence when a
  message doesn't match the expected `?`-question shape.
- **Что унифицировать:** the 4-answer-button pattern is bespoke to this game's
  Q&A structure.
- **Что уникально:** the only one of the 4 secret-role games where a core action
  (guessing) has zero button fallback — **highest-priority single UX fix found
  in this whole audit** given Ilya named this exact pattern.
- **Risk of changing:** Low — additive, doesn't touch `whoami_guess_identity`'s
  resolution logic.

### Bunker (min 6 players, secret roles)
- **Current flow:** lobby → 9-field hidden card each → sequential turn-based
  reveal (current actor picks which field to reveal via private buttons; public
  board shows N/9 revealed per player) → elimination vote (private, re-votable)
  → repeat until seat limit.
- **UX problems:**
  - Most information-dense board of all 8 games (9 fields × N players) — real
    risk of being unreadable on a phone with 6+ players. **Not visually
    confirmed** (code-only audit) — needs an actual mobile screenshot check
    before any redesign decision here.
  - min_players=6 is the highest bar of any game and isn't explained anywhere
    the catalog is shown before commit (cross-references §4).
  - Non-active players have no explicit "waiting for X" framing during another
    player's reveal turn — just implicit from status text.
- **Proposed flow:** add explicit "⏳ Ждём: <actor>" framing for idle players.
  Consider a compact/expandable board view given the density risk (needs visual
  validation first). Add `❓ Правила`.
- **Что унифицировать:** the private-keyboard-per-actor-with-🔄-refresh pattern
  is shared with mafia's private vote card.
- **Что уникально:** the 9-field progressive-reveal mechanic — entirely bespoke.
- **Risk of changing:** Medium-high for board layout (most complex render
  function in the system); low for adding waiting-state text.

### Dice (min 2 players, no secret roles)
- **Current flow:** lobby → "🎲 Бросить" button → board edits with each roll →
  ranking + winner once everyone's rolled.
- **UX problems:** minor only — no rematch button; no explicit "last roller"
  callout.
- **Proposed flow:** add "🔁 Играть ещё раз" on the finished board.
- **Что унифицировать:** the cleanest, simplest pattern in the whole system —
  good reference for "single-button-action, board edits, toast confirms."
- **Что уникально:** nothing — fully generic.
- **Risk of changing:** Low.

### Quiz (min 2 players, no secret roles, fixed 5 rounds)
- **Current flow:** each round shows question + A/B/C/D buttons + a live
  "N/M answered" progress button → submit → auto-resolves when everyone's
  answered.
- **UX problems:** the confirmation toast doesn't echo back which option was
  picked, so a player can't re-verify their own choice afterward. Minor.
- **Proposed flow:** toast text `"Ответ принят: {letter}"`.
- **Что унифицировать:** near-identical to bredovukha's and zlobcards' vote-tally
  UI — 3 separate implementations of the same pattern (see §7).
- **Что уникально:** only game with a fixed round count and no private phase.
- **Risk of changing:** Low.

### Bredovukha (min 3 players, no secret roles)
- **Current flow:** lobby (round-count stepper, 3 buttons) → category pick →
  private DM question, free-text lie submission → public vote among
  submitted-lies-plus-truth → resolution reveals authorship.
- **UX problems:**
  - The in-group "✍️ Сдать ложь в ЛС" button links to a **bare** bot DM URL with
    no `?start=game_<id>` payload — every other private-phase button in the
    same function (spy/mafia/whoami/zlobcards/bunker) has the payload.
    Tapping it opens a DM with no auto-context. **This reads as an oversight,
    not a deliberate choice** — worth fixing regardless of the broader redesign.
  - Lobby round-count is 3 stacked buttons (➖ / count / ➕) for one value.
- **Proposed flow:** fix the deep-link payload (one-line, additive). Consider
  collapsing the stepper triple into a single cycling button.
- **Что унифицировать:** the "waiting on N private replies" tracker could share
  a component with zlobcards' card-submission phase.
- **Что уникально:** the only game requiring genuine free-text composition
  (writing a plausible lie) — correctly NOT buttonized; this is the right kind
  of exception per Ilya's "don't buttonize when text is the point" carve-out.
- **Risk of changing:** Low for the deep-link fix; low-medium for stepper
  consolidation (shared shape with bunker/zlobcards lobby configs).

### Zlobcards ("500 Злобных Карт", min 3, no secret roles)
- **Current flow:** lobby (3 stacked stepper-triples: category / rounds /
  target-score — up to 9 buttons before Start/Cancel) → black card dealt →
  private hand shown **as buttons** (single cards or auto-generated combos, no
  typing at all) → submission **edits the private message in place** → anonymous
  public vote → resolution reveals card owners + winner(s).
- **UX problems:** the lobby's 3 stacked stepper-triples is exactly the
  "8-15 vertical buttons" problem, just relocated to lobby-config instead of the
  catalog. Everything else here is already close to the target pattern.
- **Proposed flow:** collapse steppers to single cycling buttons.
- **Что унифицировать:** **this game's private-hand-as-buttons-with-edit-in-place
  pattern should be the reference implementation for the shared game UI layer**
  — it's the one place already doing exactly what Ilya wants generalized.
- **Что уникально:** player-generated content (card combos) needing dynamic
  per-hand button generation.
- **Risk of changing:** Low.

### Number ("Угадай число") — orphaned, not in the catalog
Confirmed **intentional retirement, not a bug**: `game_command` explicitly
refuses `kind="number"` with *"Игра «Угадай число» больше не доступна для новых
запусков."*, while the full implementation (`number_guess_handler`,
`GameStore.number_register_guess`) still exists and works if somehow reached.
**Recommendation, not a UX item:** either delete the dead code or leave an
explicit code comment marking it deprecated-but-kept — right now a working
implementation and an active "not available" refusal coexist, which is
confusing to any future developer, not to end users. **[?] #3** only if Ilya
wants it revived; otherwise this is a P2 cleanup, not a UX roadmap item.

> **Резолюция Ilya:** удалить игру.

---

## 3. Universal finding — zero in-game help anywhere

Grepped the entire router.py for "как играть" / "правила" / "❓": **zero
matches.** No game has any in-product rules explanation. Every game's onboarding
is whatever fits in one line of `build_<game>_start_text()` (e.g. Mafia:
*"Мини-мафия началась. Ночь 1. Ночные роли уже получили ЛС-карточки. У стола N
сек. до рассвета."*). This is the single most universal gap in the whole system
and directly blocks Ilya's progressive-disclosure requirement — there is
currently no "what's this game / what do I do now / ❓ more rules" layering at
all, anywhere.

---

## 4. Game Catalog UX

- **Current flow:** `/game` with no args sends `"Выберите игру:"` + one
  vertical **title-only** button per launchable kind (8 buttons: zlobcards,
  spy, whoami, mafia, dice, quiz, bredovukha, bunker). No description,
  min-players, DM requirement, or duration is shown — the user must tap blind.
- **UX problems:** fails "should understand" and "obvious without docs" for
  every single entry. Exactly the pattern Ilya wants replaced.
- **Proposed flow:** edit the same message into a paginated catalog — 3-5 games
  per page, bottom nav row `◀️ 1/2 ▶️` (7 real launchable kinds today = 2
  pages at 4/page, comfortably under the 3-5 cap). Tapping a game edits into a
  detail card:
  ```
  🕵️ Шпион
  3+ игроков · роли в ЛС
  Один игрок не знает общую локацию. Остальные должны вычислить его по ответам.

  ▶️ Создать игру   ❓ Как играть   ← К списку игр
  ```
  `short_description` already exists on every `GameDefinition` in
  `game_state.py` and is **currently completely unused** — this is a free win,
  no new content needs to be written for the card body. `min_players` and
  `secret_roles` are likewise already-structured data, just not rendered
  anywhere yet.
  Arrows disabled/hidden at page 1 and the last page; show the page number
  (only 2 pages today, but cheap to always show). `← К списку игр` must return
  to the **origin page**, not always page 1 — needs a page index encoded into
  callback_data (there is currently zero "page" concept anywhere in the
  callback system — grepped, zero hits — but the existing `game:new:{kind}:u{id}`
  convention already has a precedent for tail-encoding extra state, so this is
  additive, not a new pattern).
- **Scaling:** `GAME_DEFINITIONS`/`GAME_LAUNCHABLE_KINDS` are already a plain
  dict + tuple, not hardcoded per-button logic — pagination is a
  rendering/callback-encoding change only. Scales cleanly to 15-30 games with
  no data-model change needed.
- **Risk of changing:** Low — purely additive rendering + one new callback
  field; doesn't touch any `GameStore` method.

## 5. Lobby UX

- **Current flow:** already edit-in-place for join/start (confirmed via
  `_safe_edit_or_send_game_board`, `bot.edit_message_text` on the stored
  `message_id`, only falling back to a new message if none exists yet or the
  edit fails) — **this part is already good and not spammy.** Join gives a
  toast, not a new message.
- **UX problems:**
  - **There is no "leave" action at all** — grepped every lobby callback
    branch; only join/start/cancel exist. A player who joins by mistake cannot
    undo it; only the whole lobby can be cancelled by someone with
    `manage_games`.
  - `short_description` is unused in the lobby board text too (same free win
    as the catalog).
  - Per-game lobby configuration (category/rounds/seats/target-score) is
    consistently rendered as 3 stacked buttons (➖ / value / ➕) even for a
    single value — contributes to "too many vertical buttons" in bunker and
    zlobcards specifically.
- **Proposed flow:** add a `➖ Покинуть` button mirroring join (identical
  code shape to the already-working join path — low risk). Collapse
  stepper-triples into single cycling buttons. Surface `short_description`
  under the player list.
- **Risk of changing:** Low — extends an already-proven edit-in-place path.

## 6. DM / secret-role UX

- **Current flow — better foundation than expected:** a working Telegram
  deep-link mechanism already exists and is the established pattern for
  *follow-up* DM access. `_build_game_controls` attaches persistent board
  buttons ("🕵️ Моя роль", "🌙 Ночной ход", "🪪 Карточки", "🃏 Рука в ЛС",
  "🔐 Действие в ЛС") using
  `url=https://t.me/{bot_username}?start=game_{game.game_id}`. `/start
  game_<id>` (and `/role` in DM) resolve straight to the right role/card view.
  **This is exactly Ilya's target pattern — "🔐 Твоя роль готова → Открыть в
  Selara" — already shipped for this specific path**, just not consistently
  applied everywhere (see below) and not yet reused for the *initial*
  role-push failure case.
- **Confirmed bugs, not [?]:**
  - Bredovukha's private-submission button is the one exception: it links to a
    bare `https://t.me/{bot_username}` with **no** `?start=game_{id}` payload —
    inconsistent with every other kind, reads as an oversight.
  - The initial role-push (`_send_role_to_user`) failure path — used when a
    player has never opened a DM with the bot at all — falls back to a plain
    text warning on the board: *"Не удалось отправить ЛС для N игрок(ов). Им
    нужно открыть диалог с ботом через кнопку роли или карточки."* Two
    problems: it names a **count**, not **who** (an affected player has to
    guess it's them), and it *describes* a button instead of *containing* one
    — the actual deep-link button is on a separate message they have to find.
    This is the exact "Проверьте личку"-style bad pattern Ilya called out, just
    with slightly better wording than the literal example.
  - This same warning is **missing entirely** from Mafia's night phase (see
    Mafia section) — a blocked player there gets no signal at all, which is
    worse than the count-only message elsewhere.
- **Proposed flow:** put the deep-link button directly inside the failed-DM
  warning message itself and `@mention` the specifically affected players,
  instead of a bare count with no actionable control. Fix Bredovukha's missing
  payload. Add the same warning pattern to Mafia's night phase. Reuse the
  existing `?start=game_{id}` mechanism everywhere — **do not build new
  deep-link infrastructure**, it already works correctly.
- **Never-pressed-/start scenario:** confirmed this is exactly the
  `TelegramForbiddenError` path above — Telegram blocks bot-initiated DMs until
  the user has started a conversation, and the code does detect this
  (`failed_dm` counting), it just doesn't act on it with a button/mention yet.
- **Risk of changing:** Low — additive to a mechanism that already works
  correctly; no state-machine changes needed.

## 7. In-game phase UX / Что можно унифицировать (reusable layer candidates)

Cross-referencing all 8 games surfaces the same handful of interaction shapes
duplicated 3-6 times each — the clearest concrete candidates for a shared,
reusable game-UI layer (Ilya explicitly asked what could be factored out):

1. **"Vote with live tally" button group** — nearly identical implementations
   in Quiz (`_build_quiz_answer_buttons`), Bredovukha (`_build_bred_vote_buttons`),
   Zlobcards (`_build_zlob_vote_buttons`), Spy (`_build_spy_vote_buttons`),
   Bunker's elimination vote, and Mafia's day vote — 5-6 near-duplicate
   button+tally+resolution shapes today.
2. **Private-hand/action-as-buttons, edit-in-place** — Zlobcards' card
   submission is the one place already doing this correctly end-to-end; the
   same shape half-exists in Bunker's reveal/vote private keyboards and
   Mafia's private day-vote card, all ending in a `🔄 Обновить` no-op button.
3. **Failed-DM warning + deep-link recovery** — currently duplicated with
   inconsistent completeness across bunker/zlobcards/bredovukha, missing
   entirely from mafia's night phase; see §6.
4. **Lobby config stepper** (➖/value/➕ triples) — repeated 3× (bredovukha
   rounds, zlobcards category/rounds/target-score, bunker seats) and is itself
   a mini version of the "too many vertical buttons" problem.
5. **Paginated catalog / detail-card** — new component, but built once,
   reusable for any future catalog-shaped listing (§4).
6. **Rematch button** — doesn't exist anywhere today (§9); one shared
   "recreate same-kind lobby" action would cover all 8 games identically.
7. **Progressive-disclosure `❓ Как играть`** — doesn't exist anywhere today
   (§3); one shared pattern (short "what's happening now" line + a button that
   reveals 3-4 lines of rules) would cover all 8 games.

## 8. Voting/actions UX

Ownership/validation is **already solid and consistent** at the `GameStore`
layer — spot-checked whoami/spy/mafia handlers: none check `from_user.id`
in the router handler itself, all delegate to `GameStore` methods that validate
under a per-`game_id` lock and return clear Russian errors ("Вы не участник
этой игры", "Игрок, задавший вопрос, не может отвечать сам себе") surfaced via
`query.answer(error, show_alert=True)` — a real modal, not a silent failure.
This is not a bug area; the per-game_id lock also mitigates double-click/
simultaneous-vote races at the state layer. The one real gap is WhoAmI's guess
mechanic (§2) having no button path at all — everything else button-based is
already well-guarded.

## 9. Errors / timeouts / stale UI

- **Finished-game board correctly loses its keyboard** — `_build_game_controls`
  returns `None` for `status=="finished"`, and the edit call always applies
  whatever `reply_markup` it's given — confirmed no dead-button problem on the
  main board.
- **Stale-callback handling exists** (`_is_stale_callback_query_error` /
  `_safe_callback_answer`) but only guards the `.answer()` call, not the edit
  itself — not confirmed whether an edit against a too-old message ever
  surfaces a confusing error to the user.
- **Phase timers are inconsistent across games** — only Mafia and Zlobcards
  have server-side phase timers with restart-safe recovery
  (`_schedule_phase_timer` / `restore_phase_timers`, recomputing elapsed time
  from `phase_started_at`). **Bredovukha, Bunker, and Quiz have no timer at
  all** — those phases only advance via a manual tap on a "⏭" button; if
  nobody taps it, the phase hangs indefinitely with no countdown or reminder.
  This is a real cross-game inconsistency, independent of the "restart safety"
  question (restart safety itself is solid where it exists — Redis-backed
  `GroupGame` state means a plain process restart is transparent when Redis is
  up).
- **Not confirmed (flagging as an open question, not a finding):** whether
  individual **private** phase-action DMs (e.g. a mafia night-action keyboard)
  get their buttons cleared when the phase moves on without that specific
  player acting. Plausible stale-DM-keyboard gap; needs a live test to confirm
  either way before prioritizing a fix.

## 10. Game results / rematch

**No game in the system has a rematch button.** Confirmed across all 8 —
every finished game requires manually running `/game` from scratch, re-picking
the kind, and re-forming a lobby even if the same people want to play again
immediately. This is a single small, low-risk, uniformly-applicable fix
(§7 item 6) with outsized impact on session-to-session friction.

## 11. Mobile Telegram UX

No visual/screenshot check was possible in this read-only code audit — this
section is a flag for the next phase, not a finding. The one specific risk
identified from code alone: **Bunker's board renders up to 9 fields × N
players** (`_render_bunker_public_profiles`) — this is the single highest
candidate for being cramped or truncated on a phone screen and should get an
actual rendered/screenshot check before any layout decision is finalized. Every
other game's board is comparatively short.

---

## 12. Roadmap — approved by Ilya (2026-08-20), staged implementation order

Ilya accepted the audit direction and made 5 corrections before implementation,
plus set a strict stage order (below). These corrections **override** the P0/P1/P2
sketch from the original audit — the staged plan is now the source of truth for
implementation order. Rule for the whole implementation: failing test →
implementation → relevant tests → visual/manual review, small logical commits,
no push/deploy without separate permission.

### Corrections to the original audit (apply throughout, not stage-specific)

1. **Timers (Bredovukha/Bunker/Quiz) — NOT added in this pass.** Auto-advancing
   a phase via a server timer is a game-behavior change, not pure UX, even
   though §9 originally proposed it as a UX fix. This round only adds clear
   waiting/progress *state text* ("Ждём: <actor>", "Ответили 3/5") — no new
   auto-advance logic. Automatic timers for these 3 games are moved to a
   **new [?] #4, deferred to a future round**, not part of this roadmap.
2. **Lobby steppers — do not blanket-replace with single cycling buttons.**
   The real problem is a vertical wall of buttons, not the existence of −/+.
   Preferred pattern for numeric settings: `➖ Раунды: 5 ➕` as **three buttons
   in one row** (`builder.adjust(3)` for that row), not three stacked rows.
   Only use a single cycling button where it's genuinely clearer for that
   specific parameter (e.g. a short enum like category, not a numeric range).
3. **Rematch — exact behavior:** `🔁 Ещё раз` creates a **new lobby of the
   same game kind, with settings copied from the just-finished game**
   (category/rounds/seats/target-score etc., whatever that kind has).
   Previous participants are **not** auto-added to the new lobby — everyone,
   including the same players, joins again via the normal join button. No
   game-mechanic changes.
4. **`❓ Как играть` — scoped by context.** Catalog/detail/lobby screens can
   freely use edit-in-place + a "← назад" button for rules. **During an
   already-active game, do not replace the state-board with a long rules
   text** — that risks conflicting with in-flight phase edits/timers (mafia,
   zlobcards). An active board's primary job stays: **что происходит сейчас →
   что нужно сделать → чего ждём**, kept short; rules access during an active
   game (if added at all) must not compete with that board for space/edits.
5. **Mafia feed-events — no "collapsible history" yet.** Classify each
   existing feed-event message against this rule during implementation:
   - current state → **edit** the existing board (no new message)
   - an important game event worth preserving in the chat's history → a
     **new** message (kept)
   - duplicate/technical noise → **don't send at all**
   Do this classification as part of the Mafia implementation work (Stage 3),
   not as a separate architecture change.

### Stage 1 — shared navigation foundation — DONE (2026-08-20, commit 6591334)
- [x] Paginated game catalog, 4 games/page (`_GAME_CATALOG_PAGE_SIZE = 4`,
  within Ilya's 3-5 range; 8 launchable kinds → 2 pages today)
- [x] `◀️ N / total ▶️` bottom nav row (arrows hidden, not disabled, at the
  respective bound)
- [x] Game detail card (title, min_players, secret_roles/DM note,
  `short_description` — all already-existing data, now actually rendered)
- [x] `← К списку игр` returns to the **origin page** (page threaded through
  every `game:detail:`/`game:rules:` callback)
- [x] Rules access from detail (`❓ Как играть` → short per-game rules text,
  authored as a first draft per Ilya's "текст не финальный" note — not from
  an active game board, per correction #4)
- [x] Everything via `bot.edit_message_text` on the existing catalog message;
  ownership restricted to whoever ran `/game` (same `:u{id}` convention as
  the existing `game:new:` callback)
- Tests: `test_game_catalog_pagination.py` (keyboard/text builders, 12 tests),
  `test_game_catalog_navigation.py` (handler-level, 7 tests), updated
  `test_game_callbacks.py` for the new contract. Full unit suite green
  (1371 passed, 1 skipped). Manual textual review of every catalog
  page/detail/rules render done (Telegram bot UI — no browser/screenshot
  path applies here; this is the equivalent check).

### Stage 2 — Spy as the reference game — DONE (2026-08-20, commit c4e713f)
- [x] New lobby UX — Spy has no numeric config (only a category-cycle
  button), so there was no stepper-triple to row-pack here; leave button
  added (see below).
- [x] Leave button — `GameStore.leave()` (mirrors `join()`, rejects the
  owner and rejects once started) + `game:leave:` callback +
  `➖ Покинуть` in the lobby keyboard. This is shared lobby infra (same
  code path as join/start), so every game's lobby gets it, not just
  Spy's — consistent with Stage 1's catalog being shared infra too.
- [x] DM/deep-link recovery — verified already correct for Spy (the
  `?start=game_{id}` mechanism + "🕵️ Моя роль" button both work as-is,
  per the original audit). No code change needed here; the broader
  unified-warning fix (button + @mention replacing the bare-count
  message) is explicitly Stage 3 scope, not Stage 2.
- [x] Clear current-state instructions — Spy's board text now separates
  "Сейчас: обсуждение..." from "Что делать: задавайте вопросы...
  голосуйте кнопкой ниже." instead of one blended sentence.
- [x] Result screen — verified already clear for Spy (reveals who was
  the spy, whether the accusation was correct, the location, plus the
  generic "Итог:" winner line). No change needed.
- [x] Rematch (`🔁 Ещё раз`, per correction #3) — `game:rematch:`
  callback + button on finished boards. New lobby, same kind, settings
  copied (category/rounds/target-score/seats where the kind has them),
  players NOT carried over. Shared infra like leave — applies to every
  game's finished board.
- Tests: `test_game_lobby_leave_and_rematch.py` (10 tests: GameStore
  methods, handler-level leave/rematch, keyboard presence). Full unit
  suite green (1381 passed, 1 skipped).
- Manual review: full Spy walkthrough (empty lobby → 3-player lobby →
  started/freeplay → finished) rendered and read end-to-end — board
  text, keyboard rows, and the finished state (rematch button only, no
  leftover vote buttons) all confirmed correct. Telegram bot UI has no
  browser/screenshot path; direct board+keyboard text rendering is the
  equivalent manual check here.

**STOPPED after Stage 2 per Ilya's instruction.** Sent for his review before
starting Stage 3 — do not propagate anything further until he confirms the
Spy reference.

**Review round 1 (2026-08-20):** Ilya confirmed Stage 1/2 direction is good
and that shared leave/rematch across all games (not just Spy) is fine, since
it's genuinely shared lobby/result infra with no per-game mechanic change —
same category as Stage 1's catalog. He asked for 3 concrete verifications
before formally accepting:
- [x] Full catalog walkthrough (page 1 → page 2 → detail → back), confirming
  the origin page is preserved — verified via actual keyboard callback_data
  (`game:list:1:u555` encodes page 2, not page 1).
- [x] Rematch walkthrough via an actual handler call (not just reading code):
  finished Spy with players `{1,2,3}` and an explicitly-set category
  ("Отдых и туризм") → `🔁 Ещё раз` by player 1 → new lobby has players
  `{1}` only and the same category. Both confirmed programmatically.
- [x] Gap found and fixed: `❓ Как играть` was only reachable from the
  catalog detail card. Added to the lobby too (`game:lrules:`/`game:lback:`,
  see commit d87a410) — now available in both places, satisfying "available
  at least until the game starts."

All 3 sent to Ilya with full text+keyboard output. **Ilya confirmed: Stage 1/2
PASS, accepted as-is (2026-08-20).**

**Pre-Stage-3 check (requested by Ilya):** owner-leave and last-player-leave
behavior, verified via actual handler calls, not just reading code:
- Owner tapping "Покинуть" → blocked with a clear alert directing them to
  "🛑 Отменить" instead; nobody removed.
- Last non-owner player leaving → lobby correctly shrinks to just the owner,
  stays in `lobby` status; starting still correctly fails with the existing
  "нужно минимум N игроков" check.
- Since the owner can never leave (only cancel destroys the lobby), it's
  structurally impossible for "Покинуть" to ever empty a lobby completely.
  Unambiguous, no [?] needed, nothing changed.

### Stage 3 — low-risk fixes — DONE (2026-08-20)
- [x] WhoAmI guess UX (commit 74f45cb): example phrasing added to the
  in-game status text; a current-actor message matching neither the guess
  patterns nor "?" now gets an explicit hint reply instead of silently
  vanishing. Kept text-based per Ilya's "don't buttonize when text is the
  point" carve-out — guessing/asking is the actual gameplay here.
- [x] Mafia DM-failure warning (commit bdc1a6c): night-phase DM failures
  used to be a bare `continue`, no count, no board warning — now returns
  the failed list and reuses the standalone warning message at all 3 call
  sites (initial start + after each night re-opens).
- [x] Bredovukha deep-link fix (commit 830a2cc): the DM button was missing
  `?start=game_{id}`, **and** `_show_role_for_user` (the deep-link
  handler) had no branch for bredovukha at all — fixing just the URL would
  have been a no-op. Both fixed.
- [x] Unified failed-DM recovery (commit 2413241): the bare-count warning
  ("Не удалось отправить ЛС для N игрок(ов)...") is now a message that
  @mentions the specific players and includes a "🔐 Открыть в Selara"
  deep-link button, reusing the existing `?start=game_{id}` mechanism —
  Ilya's named target pattern, applied everywhere the warning already
  fires (game start for spy/mafia/bunker/whoami/zlobcards, and Mafia's
  per-night re-opens).
- [x] Mafia feed-event classification per correction #5 (commit pending
  below). Full classification of every Mafia feed-event message against
  Ilya's rule (current state → edit board; important event worth history →
  new message; duplicate/technical noise → don't send):

  | Call site | Content | Classification |
  |---|---|---|
  | Night resolved → game finished | Elimination + winner + roles reveal | **Keep** — game result |
  | Night resolved → day begins | "Ночь завершена" + elimination outcome + "День начался" | **Keep** (elimination outcome is a real historical event) — not trimmed this round, see note below |
  | Day vote opened | "Голосование открыто (раунд N)... Голосуйте на доске или в ЛС" | **Drop** — pure current state; board edit already shows it, every alive player already gets a private DM prompt (`_notify_mafia_day_vote_private`). Implemented: this feed event no longer sends. |
  | Day vote resolved → execution-confirm opened | Candidate identified, confirm window opens | **Keep** — real historical moment |
  | Day vote resolved → game finished | Vote result + winner + roles reveal | **Keep** — game result |
  | Day vote resolved → night begins (tie/no candidate) | Vote outcome + "Ночь началась" | **Keep** (vote outcome is real) — not trimmed this round |
  | Execution confirm resolved → game finished | Execution result + winner + roles reveal | **Keep** — game result |
  | Execution confirm resolved → night begins | Execution result + "Ночь началась" | **Keep** (execution result is real) — not trimmed this round |
  | Game start | "Мини-мафия началась. Ночь 1..." | **Keep** — marks the start of the game in chat history |

  Only the one unambiguous, zero-information-loss case ("day vote opened")
  was dropped this round. The 3 "Keep, not trimmed" rows above all mix a
  genuinely historical outcome (who died / vote result / execution result)
  with a trailing current-state phrase ("День начался" / "Ночь началась")
  that duplicates the board — trimming just that trailing phrase would
  reduce redundancy further, but touches the same text-assembly code in 3
  different resolution functions for a marginal readability gain. Left as
  a **noted-but-not-implemented refinement** rather than risking those
  well-tested functions in this "low-risk fixes" stage — matches Ilya's
  explicit "don't implement the collapsible-history idea yet" boundary.

### Stage 4 — remaining games — DONE (2026-08-20, 7 commits, one per game)
Ported the Spy-verified pattern to all 7 remaining games, one at a time,
in Ilya's specified order, each with tests + a real end-to-end walkthrough
before moving to the next. No mechanic changed in any game. Commits:
bad65b2 (Dice), d00f6ea (Quiz), b1ff578 (Bredovukha), e261bec (WhoAmI),
0d52a37 (Zlobcards), ada7fa6 (Bunker), 4ca8635 (Mafia).

Per-game summary (also see the "actual renders" report sent to Ilya):
- **Dice** — board text split; progress line now names who hasn't rolled.
- **Quiz** — board text split (waiting list pre-existed); answer toast now
  echoes the chosen letter.
- **Bredovukha** — board text split for all 3 phases; trimmed now-redundant
  instruction lines inside the question/vote blocks.
- **WhoAmI** — board text split for both phases (guess discoverability
  already fixed in Stage 3 + its follow-up).
- **Zlobcards** — board text split; **found and fixed a real bug while
  implementing correction #2**: the lobby's blanket `adjust(2)` split every
  numeric stepper across row boundaries (e.g. "➕ Раунды" landing next to
  "➖ Цель" — a misclick risk). Replaced with explicit per-row sizing for
  the lobby keyboard so every stepper (Bredovukha, Zlobcards x2, Bunker)
  lands on its own row.
- **Bunker** — board text split + explicit "остальные пока не ходят"
  framing (the audit's specific finding for this game); found and fixed a
  DM-recovery gap (mid-game reveal-turn advances weren't using the unified
  button+@mention warning); mobile-density check done (1092 chars for an
  8-player game, well under Telegram's limit — Telegram bubbles wrap text
  natively, so the web-style overflow concern doesn't apply the way the
  original audit worried); found-but-deliberately-not-fixed: a minor
  redundant double-DM at game start (noted, not touched).
- **Mafia** — board text split for the 4 remaining phases (DM
  recovery/feed-event classification already done in Stage 3); added a
  waiting list to execution-confirm voting (day_vote already had one).

**What actually repeated across games (candidates for Stage 5):**
- The "Сейчас" / "Что делать" board-text split — all 7 games.
- "Ждём: <names>" waiting lists for simultaneous-answer phases (Dice,
  Quiz, Bredovukha private-answers, Bredovukha public-vote, Zlobcards
  private-answers, Zlobcards public-vote, Mafia day_vote, Mafia
  execution-confirm, Bunker vote) — 9 near-identical implementations.
- The lobby per-row-sizing fix for numeric steppers (Bredovukha,
  Zlobcards x2, Bunker) — same underlying bug, same fix shape.
- Rematch/leave/rules/catalog — already shared infra since Stage 1/2, not
  re-implemented per game.

**What stayed genuinely unique (not forced into a shared shape):**
- WhoAmI's free-text question/guess mechanic — correctly not buttonized.
- Bredovukha's free-text lie submission — same carve-out.
- Zlobcards' private-hand-as-buttons — already the reference implementation
  for that specific sub-pattern, not generalized further this stage.
- Bunker's 9-field progressive reveal and turn-based idle framing — no
  other game has this shape.
- Mafia's night-action role variety and dual day-vote surfaces (public
  board + private card) — untouched, bespoke.

**STOPPED before Stage 5 per Ilya's instruction.** Full report (per-game
changes, actual renders, repeated vs. unique patterns, full test result)
sent to Ilya. Stage 5 consolidation waits for his separate confirmation.

### Stage 5 — consolidation — DONE (2026-08-20, commit 5ef50fe)
Scoped tightly per Ilya's explicit boundaries — 3 helpers, each only where
duplication was real and verified, nothing guessed upfront:

1. **`_append_waiting_line`** — the "who hasn't answered yet" line, 8
   duplicate sites across 6 games (Spy, Dice, Bunker vote, Quiz, Bredovukha
   x2, Zlobcards x2, Mafia x2).
2. **`_add_stepper_row`** — the ➖/value/➕-one-row lobby shape, 4 duplicate
   sites (Bredovukha, Zlobcards x2, Bunker) — the exact steppers Stage 4
   fixed the row-packing bug for. Keyboard output verified byte-identical
   before/after.
3. **`_warn_on_failed_dm`** — the "if failed: warn" guard, 7 duplicate
   sites (game start x2, Mafia night x2, Bunker reveal x3).

**Deliberately NOT touched**, per Ilya's instruction: Сейчас/Что делать
(stays a convention each renderer writes itself, not an abstraction),
vote/tally handlers (Mafia/Spy/Bunker/Quiz — too different, would need
strategy params), private-hand, Mafia night actions, Bunker reveal logic.
Bunker mobile readability: not touched — left as an unverified visual item
for a real Telegram smoke test after a future deploy, not something more
code changes can resolve.

**Honest result, not oversold:** net `+80/-56` lines in router.py —
roughly line-count-neutral, since each helper's body (with an explanatory
comment) is about the same size as what it saved per call site. The real
win is duplicate *logic* going from 19 call sites across 3 patterns down
to 3 single implementations — a future bug/change in any of the 3 patterns
now needs fixing once, not N times. Full unit suite green (1431 passed, 1
skipped) with **zero existing game tests needed changing** — confirms this
was a behavior-preserving refactor, not a rewrite. 2 new test files cover
the 2 newly-introduced helpers directly.

**STOPPED after Stage 5 per Ilya's instruction — no push/deploy.** Full
report (diff review, helper list, duplication removed, full test result)
sent to Ilya.

### Separate small cleanup commits (not tied to a stage)
- [x] Delete the orphaned "Number" game — **резолюция Ilya: удалить**
  (commit f8aaa54, done during the cold-review pass — was previously only
  marked resolved in this doc, not actually implemented; caught and fixed
  before the review). Also removed stale user-facing mentions found in
  `/help`, the command catalog, USER_GUIDE.md, and README.md that had been
  describing a game that could never be started. Tests-first, full suite
  green (1432 passed, 1 skipped).
- [ ] `spy_guess_location` — **резолюция Ilya: подключить позже** — not part
  of this roadmap, remains deferred. **Correction found during Number
  cleanup:** the Telegram-bot UI genuinely has no callback for it (audit
  was right there), but the **web panel / Mini App already has it wired**
  (`form_action == "spy_guess"` in `app.py`, calling
  `GAME_STORE.spy_guess_location`). So this mechanic is reachable today,
  just not from the bot's own inline keyboards — worth Ilya knowing before
  deciding on "connect later."

### New deferred item
- [?] **#4** Automatic phase timers for Bredovukha/Bunker/Quiz — explicitly
  out of scope for this round (correction #1 above); revisit as its own,
  separate product decision later.

---

## 13. Summary

**5 самых болезненных UX-проблем сейчас:**
1. (Снят — резолюция Ilya: manage_games-гейт задуман, не трогаем.)
2. WhoAmI's identity-guess requires a memorized magic phrase with zero button
   fallback and zero in-context hint — the exact pattern Ilya named.
3. The catalog is 8 stacked title-only buttons with no context — users tap
   blind, `short_description`/`min_players`/DM-note all exist in data and are
   simply never rendered.
4. No game has a rematch button — every session ends in a manual restart.
5. Mafia's night-phase DM failures are completely silent — worse than the
   partial warning the other secret-role games already show.

**5 изменений с максимальным эффектом:**
1. Paginated catalog + detail card (Ilya's named target pattern) — reuses
   already-structured but unrendered data, no new content needed.
2. Universal deep-link DM-failure recovery (button + @mention), reusing the
   `?start=game_{id}` mechanism that already works, extended to every game
   including Mafia's night phase and fixed for Bredovukha.
3. WhoAmI guess-button/hint.
4. Lobby leave button + rematch button (two small, independent, low-risk
   additions with outsized session-friction impact).
5. Unify the 3 near-identical vote-tally implementations and 3 lobby-stepper-
   triples — simultaneously reduces code duplication and UX inconsistency.

**Какую игру модернизировать первой как эталон:**
**Spy** — recommended as the full end-to-end pilot. It's the simplest of the 4
secret-role games (single phase, no night/day cycle), already fully
button-driven with no required text, has a real DM/deep-link component worth
proving the fix on, and is short enough to demonstrate catalog → lobby → DM →
voting → result → rematch as one complete reference without Mafia/Bunker's
extra complexity. **Zlobcards** should be the reference *specifically* for the
private-hand-as-buttons sub-pattern (§7.2), since it's already the strongest
existing implementation of that piece — but as a full pilot it carries more
lobby-config complexity (3 stepper-triples) than Spy. If an even smaller first
slice is wanted (catalog + lobby + rematch only, no DM complexity at all),
**Dice** is the minimal option.

**Что можно вынести в общий reusable game UI layer:** see §7 in full — vote-
with-tally, private-hand-as-buttons, failed-DM recovery, lobby config stepper,
paginated catalog/detail-card, rematch button, and progressive-disclosure
rules button. All 7 are drawn from patterns that already exist in at least one
game today, not invented from scratch.

---

**Резолюции Ilya по всем 3 открытым вопросам (получены после отправки аудита):**
1. `manage_games`-гейт на `/game` — так и задумано, не менять.
2. `spy_guess_location` — подключить, но позже, не в текущем заходе.
3. "Угадай число" — удалить игру.

Всё остальное в этом документе — read-only находки и предложения, ждут ревью
плана перед реализацией. Ничего не реализовано, не запушено, не задеплоено.

---

## 14. Cold review (после Stage 5) — находки и фиксы

Независимый cold-review агент прошёлся по полному diff Stage 1-5 как
отдельная проверка перед push. Критичных/высоких находок не было. 2 мелкие
находки, обе исправлены (tests-first, отдельные коммиты):

1. **LOW** — `tests/unit/test_web_templates.py` содержал 5 копий устаревшего
   фикстур-ключа `"show_number_guess": False,`, оставшегося от Number-игры
   после её удаления (f8aaa54); файл не входил в исходный Stage 1-5 diff.
   Fix: `e1cd0b6`.
2. **NIT → на деле реальный баг** — `game:rematch:` для Bunker всегда звал
   `set_bunker_seats()`, чтобы скопировать число мест из завершённой игры.
   Побочный эффект: новое лобби всегда помечалось `bunker_seats_tuned=True`,
   даже если исходное число было чисто авто-вычислено — это ломало
   авто-масштабирование мест по числу игроков для каждого Bunker-реванша.
   При более глубокой проверке выяснилось, что перенос вообще молча не
   срабатывал: `set_bunker_seats()` отклоняет `seats >= players_count`, а в
   момент вызова в новом лобби есть только создатель (1 игрок) — так что
   `seats >= 1` почти всегда истинно. Fix: новый метод
   `GameStore.carry_over_bunker_seats()` (не валидирует по числу игроков
   живого лобби, в отличие от `set_bunker_seats()`), вызывается только когда
   `bunker_seats_tuned` у исходной игры было `True`. Fix: `f3780a9`.

Полный unit suite после фиксов: **1434 passed, 1 skipped** (188.77s).

Сознательно оставшийся backlog (не в рамках этого захода):
- `spy_guess_location` — подключён в веб-панели (`form_action == "spy_guess"`
  в `app.py`), но не в Telegram-боте (кнопки/колбэки для этого пока нет).
- Реальный mobile-smoke Bunker-доски в живом Telegram — отложен до следующего
  деплоя (доказано только что текст `<4096` символов, не читаемость).
- Автоматические таймеры фаз для Bredovukha/Bunker/Quiz — отложено, отдельный
  `[?]` для будущего захода (это поведенческое изменение, не чистый UX).
- Небольшой избыточный двойной DM при старте Bunker (role-card +
  reveal-ready card одному игроку) — замечен, не фиксился, не влияет на
  корректность.

Push/deploy не выполнялись — ждут отдельного разрешения.
