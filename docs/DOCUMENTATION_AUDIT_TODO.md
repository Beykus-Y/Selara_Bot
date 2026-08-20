# Documentation audit — TODO

Read-only audit ran 2026-08-20 (three parallel passes: USER/ADMIN guides minus
gacha/STT/LLM, gacha docs, technical/repo docs — plus a personal check of the
STT/LLM sections). Full findings write-up: artifact link sent to Ilya via
Telegram; this file tracks the corrected, Ilya-approved priority for
implementation.

Ilya's corrections to the raw audit (2026-08-20), applied below:
- Gacha animation feature: **P0 → P1** — it's an undocumented feature, not a
  false statement in an existing doc. Real gap, but not actively misleading.
- roles.md P0 item expanded: fix junior_admin's wrong permissions **and**
  document the missing `co_owner` tier and the full, correct role list.
- Admin-configurable triggers (welcome/captcha/antiraid) belong in
  ADMIN_GUIDE, not USER_GUIDE — USER_GUIDE should only describe behavior a
  regular member actually experiences, not chat-config triggers an admin
  sets up.
- Historical analysis/TODO/contradictions docs: default to **archiving with
  a date + pointer to the current source of truth**, not deleting or
  rewriting history, unless there's a real reason to remove one outright.
- New items from Ilya's own review of the audit:
  - USER_GUIDE.md must not point regular users at BUGS.md (a dev bugtracker)
    as if it were user-facing help.
  - Remove leftover technical jargon from USER_GUIDE.md ("gacha-сервис",
    raw setting-key names) — plain human wording only.

Rule for implementation: doc-sync checks (facts a test can mechanically
verify against code — role permissions, command/trigger lists, settings
keys) get a failing test first, then the fix. One-off prose corrections
(factual claims that aren't an ongoing "drift-guardable" fact) are edited
directly. Small, logical commits, P0 → P1 → P2. No push until final
self-review.

## P0 — actively misleading, fix first

- [x] **docs/bot_docs/roles.md** (fed directly to the bot's own LLM assistant —
  errors here become wrong answers to real admins):
  - junior_admin wrongly claims it can warn/pred/ban/rest (default template
    only grants `PERM_ANNOUNCE`, not `moderate_users`).
  - senior_admin wrongly claims it can use `set_rank` (default template has
    no `manage_roles`, only `co_owner`/`owner` do).
  - `co_owner` tier (rank 30) is completely missing from the role list.
  - `set_rank`'s `rank` enum in the doc (and in the tool's own schema
    description in `tools.py`) omits `co_owner` as a valid value.
  - Add a doc-sync test (mirrors `test_guide_docs_match_catalog.py`'s
    pattern) checking role-code completeness and permission-claim accuracy
    against `core/roles.py`'s `SYSTEM_ROLE_TEMPLATES`, so this can't
    silently drift again.

- [x] **docs/USER_GUIDE.md** — remove/correct claims that no longer match
  code, and stop referencing BUGS.md as user-facing help:
  - §4.2/§11.3: "когда был @user" text form doesn't resolve a target — fixed
    in code (`catalog.py`'s lastseen tail validator). Remove the false
    claim; keep only what's still true in plain wording, no BUGS.md pointer.
  - §11.4: claims reply-target beats explicit `@username` in `/pay` — code
    does the opposite (explicit recipient wins). Correct the claim.
  - §10 step 6 and §12 FAQ: remove the "see BUGS.md" pointers entirely —
    replace with the existing §13 report-template flow instead.

- [x] **BUGS.md** — close/correct the two items USER_GUIDE was citing:
  - #3 (lastseen text doesn't resolve) — confirmed fixed in code, close it.
  - #4 (`/pay` reply-priority) — confirmed backwards vs. code (explicit
    target wins, not reply) — correct the description.
  - Update the closing "что делать пользователю" list to match (remove the
    now-obsolete lastseen workaround advice).

## P1 — real gaps, no active harm but limit users/admins

- [x] Gacha animated-pull reel + the "🎬 Анимация" per-user toggle button —
  document in USER_GUIDE.md §6.4 (what it is, that it can be turned off) and
  ADMIN_GUIDE.md §10.1 (that it exists, default-on, per-user not per-chat).
- [x] ADMIN_GUIDE.md §6 — add the ~36 missing chat settings keys
  (welcome/goodbye, entry captcha, antiraid, titles, auctions, craft,
  family tree, custom RP, interesting facts, persona display,
  llm_enabled/llm_context_threshold, etc. — full list in `chat_settings.py`
  `CHAT_SETTINGS_KEYS`). Put the welcome/captcha/antiraid *trigger words*
  here too (admin-configured, not user-facing), not in USER_GUIDE.
- [x] ADMIN_GUIDE.md §7 — add rest/persona as direct admin text commands
  ("выдать рест", "ресты", "забрать рест", "выдать образ", "снять образ",
  "образы"), not just as things the AI assistant can do.
- [x] USER_GUIDE.md — add family commands `/adoptdaughter`, `/escapefamily`,
  `/escapepet` (§7/§9.5); add "цитировать" (§9.4). These are things a
  regular member directly does, so USER_GUIDE, not ADMIN_GUIDE.
- [x] USER_GUIDE.md — remove "gacha-сервис" phrasing and any other
  internal/technical wording; rephrase in plain terms a non-developer would
  use.
- [x] README.md / INSTALLATION.md — add an STT/LLM env var + opt-in section
  (currently zero mentions despite `.env.example` already documenting them).
- [x] docs/bot_docs/glossary.md + capabilities.md — add `remove_from_glossary`,
  `list_glossary`, `get_current_time` (all real, currently-undocumented tools).
- [x] ADMIN_GUIDE.md §13 — mention `get_current_time`.
- [x] gacha/README.md — add the 5 missing API endpoints (`/pull/purchase`,
  `/banners/{banner}/cards`, `/users/{id}/collection`,
  `/admin/currency/grant`, `/pulls/{id}/sell`).
- [x] FUNCTIONAL_GAPS_ANALYSIS.md — resolve the internal self-contradiction (archived with a correcting note rather than rewritten in place)
  (item #3 vs #8 on lastseen) and correct item #1 (chat_write_locked already
  exists and works, `chat_write_lock.py`).

## P1 — Discoverability / Onboarding (added 2026-08-20, Ilya's follow-up)

`/start` in DM currently opens straight into the technical ЛС-panel (group
counts, Mini App/PC-panel links) with zero explanation of what Selara even
does — bad first-run experience for someone who's never used the bot.
This is scoped as part of the documentation modernization, not a separate
feature: the fix is a real onboarding path, not a standalone patch.

- [x] Redesign `/start` (`src/selara/presentation/handlers/private_panel.py`,
  `send_private_start_panel`/`_render_home_text`/`_build_home_keyboard`):
  keep it as the existing ЛС-panel entry point for returning users, but make
  the *first* thing a new user sees a short, plain-language intro (what
  Selara is, that it's for group chats, main areas: games/stats/economy/
  relationships-family/gacha), with one obvious **🚀 Как начать** button.
  Keep Mini App + PC-panel access. Keep the group-count info, but visually
  below the onboarding block, not as the headline. Short text — this is not
  a place to cram documentation.
- [x] New public **Getting Started** page on the same server, using the
  existing Web UI/Jinja/design system — study `/app/docs/user` and
  `/app/docs/admin`'s existing architecture first (`build_user_docs_context`/
  `build_admin_docs_context` in `src/selara/web/user_docs.py`/`admin_docs.py`,
  their templates, responsive/nav/search) and do NOT build a second,
  independent doc system. `/app/docs/user` already renders without an
  authenticated session (`_load_user_from_request` returns `None` gracefully,
  no forced redirect) — the new route should follow the same pattern.
  Preferred URL: `/app/docs/getting-started`, unless investigation finds a
  better-fitting existing route convention (if so, use it and note why here).
  This page is "Selara in a minute" onboarding, not a shortened USER_GUIDE —
  structure: (1) works in group chats, nothing to configure; (2) a few
  example things to type (кто я / топ / игра / баланс / гача генш) plus one
  line that many features work in plain words, not just slash commands;
  (3) some social actions work as a reply to a message (e.g. reply + обнять);
  (4) the bot sometimes DMs you (hidden roles, some game features), in plain
  language; (5) links onward to full docs. Must read in under a minute —
  no walls of text.
- [x] Navigation on the Getting Started page into existing docs (e.g. 🚀
  Начало / ✨ Возможности / 🎮 Игры / 💰 Экономика и гача / 💞 Общение и семья
  / 🛠 Для администраторов) — reuse existing USER_GUIDE/ADMIN_GUIDE
  sections/anchors where the material already exists rather than duplicating
  content; add anchors only where genuinely missing. If a horizontal
  nav-strip pattern is used, verify mobile overflow/scroll behavior and that
  targets are comfortably tappable.
  Implemented with a `flex-wrap` nav (`.onboarding-nav`/`.onboarding-nav-link`
  in `panel.css`), deliberately *not* a horizontal-scroll strip — it wraps to
  full-width rows on mobile instead, so there's no scroll-strip accessibility
  concern to verify in the first place. Verified anyway (no page ever
  overflows horizontally at 390/820/1440px, every nav link ≥40px tall on
  mobile) via `tests/unit/test_web_getting_started_browser.py`. Links go to
  the real `user_docs.py`/`admin_docs.py` anchors (`user-docs-start`,
  `user-docs-games` — gacha lives here, not under economy —
  `user-docs-economy`, `user-docs-relationships`, `user-docs-social`,
  `/app/docs/admin`), resolution proven against the real rendered pages in
  `tests/unit/test_web_getting_started.py`.
- [x] Wire the full path together: Telegram `/start` → 🚀 Как начать →
  Getting Started page → the relevant USER_GUIDE/ADMIN_GUIDE section, with a
  way back to general docs from the Getting Started page itself. `/start`
  must never link to raw Markdown/GitHub.
- [x] Audience discipline (same rule as the rest of this TODO, reiterated
  because it's easy to violate by accident while wiring links): Getting
  Started and USER-facing docs stay in plain human language — no Python
  function/class names, DB table names, API endpoints, env var names,
  "gacha-service"/"repository" wording, or raw setting keys like
  `text_commands_enabled` where "администратор может отключить текстовые
  команды" says the same thing in plain words. ADMIN_GUIDE is for a regular
  Telegram chat admin, not a developer, same as before.
- [x] Verification checklist (regression checks + manual, per Ilya's
  explicit list): `/start` contains the "Как начать" button; the button
  links to a real, existing public route; Getting Started opens with no
  user/admin session; internal doc links/anchors aren't broken; existing
  Mini App/PC-panel `/start` flows still work; the user's group count still
  renders correctly; mobile render has no page-wide horizontal overflow.
  After implementation: desktop screenshot, mobile screenshot, manual visual
  check of both, full relevant unit suite, existing docs HTML/anchor tests,
  `git diff --check`, self-review of the whole diff.
  All done: `tests/unit/test_private_panel_onboarding.py` (7 tests, /start
  side), `tests/unit/test_web_getting_started.py` (route reachable with a
  DB-session-factory that raises if touched at all — stricter than the
  existing `/app/docs/user` anonymous-access test — plus own-page and
  cross-page anchor soundness), `tests/unit/test_web_getting_started_browser.py`
  (Playwright: no horizontal overflow at 390/820/1440px, ≥40px nav tap
  targets, desktop+mobile screenshots reviewed manually — clean single-column
  stack on mobile, no overflow, four-then-one card grid on desktop). Full
  unit suite green (1348 passed, 1 skipped) after bumping the
  `test_server_ui_baseline.py` template inventory/`panel.css` line-count
  budget to account for the new template and CSS. `git diff --check` clean.
  Independent adversarial self-review (fresh-context agent) found 2 real
  bugs before this was reported done: (1) `getting_started_url` was only
  threaded into the `/start` call site of `_build_home_keyboard` — the
  other 6 places that rebuild the same home screen ("🔄 Обновить" and every
  cancel/empty-state path) silently dropped the "Как начать" button on
  every re-render; (2) `_render_home_text` unconditionally told the user to
  tap "🚀 Как начать" even when `WEB_ENABLED=False` and no such button
  exists. Both fixed (all 7 call sites now thread the URL through;
  the text line is now conditional on the URL being present), with a
  failing-test-first regression guard for each (including a static
  AST-based check that every `_build_home_keyboard` call site passes
  `getting_started_url`, so a future call site can't silently reintroduce
  the same class of bug) — verified failing on the pre-fix code before
  re-verifying green after. Full suite green again after the fix
  (1351 passed, 1 skipped).
- [ ] Scope discipline: no opportunistic changes outside documentation/
  onboarding. If a genuine product decision comes up that can't be
  unambiguously derived from current behavior, mark it `[?]` here and ask
  rather than guessing.

## P2 — cleanup, no confusion caused today

- [x] Archive (date + pointer to current source, not delete) unless a file
  has zero remaining value even as a historical record:
  - CONTRADICTIONS.md — all 4 items resolved; archive with a note pointing
    at README.md/INSTALLATION.md as the now-current source.
  - docs/gacha-subproject-analysis.md — describes the pre-MVP single-banner/
    SQLite stage; archive with a pointer to `gacha/README.md` as current.
  - IDEAS.md — several proposals already shipped; archive or refresh with
    a status pass, whichever is less work per item.
  - ACTIONS.md — diverged from the code-generated action list
    (`build_social_action_docs()` / `/app/docs/user`); either resync or
    archive with a pointer to the generator as the real source of truth.
- [x] CHANGELOG.md — resume or archive-with-date (currently 6+ days /
  40+ commits stale).
- [x] docs/GACHA_MODERNIZATION_TODO.md — reconcile the self-contradicting
  status section (top says "all closed", later entries say "in progress"/
  "not fixed" for something the code shows is actually done) before any
  archiving decision.
- [x] frontend/README.md — replace the unedited Vite scaffold text with a
  real description (lint pipeline, Docker/nginx deploy, backend relationship).
- [x] ADMIN_GUIDE.md §7/§10.1 — minor alias completeness (missing
  разпред/анпред/разварн/анварн/анбан + English forms), subscription-gate
  wording precision.
- [x] ADMIN_GUIDE.md — a proper first-class Mini App / web panel section.
  Added §10.2 "Веб-панель (Mini App и ПК-панель)": the two entry paths
  (Mini App button, no separate login; `/login` bot command → one-time
  code → PC-panel), what's inside the cabinet (group list, per-group
  overview/achievements/settings/audit+aliases/triggers tabs, games,
  account achievements, docs incl. the new Getting Started page), and that
  web actions require the same bot permissions as chat commands. Drift-guard
  test added (`test_admin_guide_web_panel_section_cites_real_permission_codes`
  in `test_guide_docs_match_catalog.py`) checking the cited permission codes
  and `/login` are real.

## Confirmed accurate, no change needed

MISSING_LOGIC.md, docs/WEB_UI_ROUTE_INVENTORY.md, docs/WEB_UI_SLICE_WORKFLOW.md,
docs/WEB_UI_MODERNIZATION_TODO.md, docs/bot_docs/moderation.md,
docs/bot_docs/rests.md, docs/bot_docs/personas.md, docs/bot_docs/identity.md,
docs/STT_LLM_AUDIT_TODO.md.
