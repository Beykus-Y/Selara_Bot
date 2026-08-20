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

- [ ] **docs/bot_docs/roles.md** (fed directly to the bot's own LLM assistant —
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

- [ ] **docs/USER_GUIDE.md** — remove/correct claims that no longer match
  code, and stop referencing BUGS.md as user-facing help:
  - §4.2/§11.3: "когда был @user" text form doesn't resolve a target — fixed
    in code (`catalog.py`'s lastseen tail validator). Remove the false
    claim; keep only what's still true in plain wording, no BUGS.md pointer.
  - §11.4: claims reply-target beats explicit `@username` in `/pay` — code
    does the opposite (explicit recipient wins). Correct the claim.
  - §10 step 6 and §12 FAQ: remove the "see BUGS.md" pointers entirely —
    replace with the existing §13 report-template flow instead.

- [ ] **BUGS.md** — close/correct the two items USER_GUIDE was citing:
  - #3 (lastseen text doesn't resolve) — confirmed fixed in code, close it.
  - #4 (`/pay` reply-priority) — confirmed backwards vs. code (explicit
    target wins, not reply) — correct the description.
  - Update the closing "что делать пользователю" list to match (remove the
    now-obsolete lastseen workaround advice).

## P1 — real gaps, no active harm but limit users/admins

- [ ] Gacha animated-pull reel + the "🎬 Анимация" per-user toggle button —
  document in USER_GUIDE.md §6.4 (what it is, that it can be turned off) and
  ADMIN_GUIDE.md §10.1 (that it exists, default-on, per-user not per-chat).
- [ ] ADMIN_GUIDE.md §6 — add the ~36 missing chat settings keys
  (welcome/goodbye, entry captcha, antiraid, titles, auctions, craft,
  family tree, custom RP, interesting facts, persona display,
  llm_enabled/llm_context_threshold, etc. — full list in `chat_settings.py`
  `CHAT_SETTINGS_KEYS`). Put the welcome/captcha/antiraid *trigger words*
  here too (admin-configured, not user-facing), not in USER_GUIDE.
- [ ] ADMIN_GUIDE.md §7 — add rest/persona as direct admin text commands
  ("выдать рест", "ресты", "забрать рест", "выдать образ", "снять образ",
  "образы"), not just as things the AI assistant can do.
- [ ] USER_GUIDE.md — add family commands `/adoptdaughter`, `/escapefamily`,
  `/escapepet` (§7/§9.5); add "цитировать" (§9.4). These are things a
  regular member directly does, so USER_GUIDE, not ADMIN_GUIDE.
- [ ] USER_GUIDE.md — remove "gacha-сервис" phrasing and any other
  internal/technical wording; rephrase in plain terms a non-developer would
  use.
- [ ] README.md / INSTALLATION.md — add an STT/LLM env var + opt-in section
  (currently zero mentions despite `.env.example` already documenting them).
- [ ] docs/bot_docs/glossary.md + capabilities.md — add `remove_from_glossary`,
  `list_glossary`, `get_current_time` (all real, currently-undocumented tools).
- [ ] ADMIN_GUIDE.md §13 — mention `get_current_time`.
- [ ] gacha/README.md — add the 5 missing API endpoints (`/pull/purchase`,
  `/banners/{banner}/cards`, `/users/{id}/collection`,
  `/admin/currency/grant`, `/pulls/{id}/sell`).
- [ ] FUNCTIONAL_GAPS_ANALYSIS.md — resolve the internal self-contradiction
  (item #3 vs #8 on lastseen) and correct item #1 (chat_write_locked already
  exists and works, `chat_write_lock.py`).

## P2 — cleanup, no confusion caused today

- [ ] Archive (date + pointer to current source, not delete) unless a file
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
- [ ] CHANGELOG.md — resume or archive-with-date (currently 6+ days /
  40+ commits stale).
- [ ] docs/GACHA_MODERNIZATION_TODO.md — reconcile the self-contradicting
  status section (top says "all closed", later entries say "in progress"/
  "not fixed" for something the code shows is actually done) before any
  archiving decision.
- [ ] frontend/README.md — replace the unedited Vite scaffold text with a
  real description (lint pipeline, Docker/nginx deploy, backend relationship).
- [ ] ADMIN_GUIDE.md §7/§10.1 — minor alias completeness (missing
  разпред/анпред/разварн/анварн/анбан + English forms), subscription-gate
  wording precision.
- [ ] ADMIN_GUIDE.md — a proper first-class Mini App / web panel section.

## Confirmed accurate, no change needed

MISSING_LOGIC.md, docs/WEB_UI_ROUTE_INVENTORY.md, docs/WEB_UI_SLICE_WORKFLOW.md,
docs/WEB_UI_MODERNIZATION_TODO.md, docs/bot_docs/moderation.md,
docs/bot_docs/rests.md, docs/bot_docs/personas.md, docs/bot_docs/identity.md,
docs/STT_LLM_AUDIT_TODO.md.
