# Model Catalog + Router

The existing `DefaultModelRouter` now resolves logical profiles to immutable
`ResolvedModel` values. The physical `model_id` is sent through the existing
OpenAI-compatible endpoint and API key; credentials remain in environment settings.
No additional SDKs or provider transport are introduced.

## Configuration

`llm_model_catalog` stores stable internal keys, physical model IDs, display names,
enabled flags, capabilities, timestamps and nullable Decimal USD rates per **1M**
prompt/completion tokens. NULL means unknown; zero is a valid free rate.
Prices are bounded to 0..1,000,000 USD/1M and 9 decimal places.

`llm_model_identifiers` stores both canonical IDs and explicitly declared
provider response aliases in one globally unique namespace. Matching is exact;
future snapshot suffixes are not inferred. Aliases cannot collide with another
model's canonical ID or alias. Store writes are transactional, serialized on
PostgreSQL, and invalidate the local cache only after commit.

`llm_model_profiles` has five stable keys: `basic` (Базовая), `analytics` (Аналитик),
`freeform` (Свободная), `creative` (Творческая), and `fast` (Быстрая). Display names
are editable. Each profile can assign a nullable catalog key and an enabled flag.
The migration seeds profiles with no assignment and `ail_multiplier = 1`.

**`ail_multiplier` in PR 11/12 was metadata; since PR 13 it prices Personal AI
requests only when the owner switches Personal to AI Limits mode (see below).** Its Decimal value is validated in (0, 1000], with 9 decimal places.
Personal quotas remain 5 free / 150 paid requests by default, and one request
consumes exactly one unit regardless of the selected model or multiplier.

## Runtime

`build_model_catalog` returns the cached provider and `SqlAlchemyModelCatalogStore`.
The store's `load` lists all models/profiles as typed values; `save_model` replaces
prices, capabilities, aliases and enabled state, while `save_profile` changes
assignment, display name, enabled state and multiplier. These application/repository
interfaces prepare the next admin UI PR; no new public HTTP endpoints are exposed.

The cache uses a 15-second TTL, single-flight refresh, a 2-second load timeout,
repeatable-read snapshots, and last-known-good state. Invalidation expires the
snapshot without discarding it. Concurrent invalidation during refresh remains
pending. Other processes observe writes at the next TTL refresh. A database outage
or invalid snapshot keeps the last valid state; a cold cache returns an empty
snapshot and routing falls back to legacy settings. Refresh failures are logged.

`LlmClient.chat_simple`, `chat_with_tools`, `summarize` and `chat_structured` accept
optional `model` or `model_profile` keyword arguments (mutually exclusive).
Features specify profiles rather than hardcoded physical model IDs. Existing calls
pass neither, so ordinary/tool calls keep `LLM_MODEL` and summary/structured calls
keep `LLM_SUMMARY_MODEL`. No existing feature implicitly changes its model just
because an administrator assigns a profile.

Missing/disabled/unassigned profiles and disabled/incompatible models use the
per-operation legacy model. Unknown profiles also fall back in a controlled way.
Tools require tool capability when tools are supplied; native JSON-schema requests
require structured-output support. Known incompatible explicit model overrides
are rejected before provider calls. Legacy capabilities are unknown, so legacy
behavior remains unchanged. There is no automatic complexity classifier or
retry chain between different models.

## Accounting

Each logical client call captures an immutable pricing snapshot before the provider
request. Transport retries and structured correction rounds reuse that snapshot.
`llm_usage_log.model` keeps the actual provider response identifier, or the requested
identifier when absent. Exact catalog aliases determine the price; an unknown
response identifier remains unpriced even when its requested model was known.
Disabled models are still priced for completed calls.

A catalog row with a NULL price explicitly produces unknown pricing. Models absent
from the catalog retain the existing exact legacy prices for `gpt-4o-mini`, `gpt-4o`
and their declared snapshots. Pricing failures never block provider inference.
`estimated_cost_usd` is calculated with Decimal and saved with `pricing_status`.
`model_profile` is stored per call, including failed attempts, as historical text
without a catalog foreign key. Older/unprofiled calls leave it NULL; history is
never reconstructed from current assignment. The profile/time index prepares
future analytics aggregation without changing existing physical-model analytics.

**Historical costs are never recalculated from current prices.** The persisted
`llm_usage_log.estimated_cost_usd` remains the source of truth after price changes,
model deletion or profile reassignment. Usage model IDs now support 255 characters.
Downgrade refuses if IDs longer than the previous 64-character column exist rather
than silently truncating them.

## Later PRs

PR 12 supplies owner administration UI/HTTP endpoints using these store interfaces.
User model selection and AIL consumption arrived in PR 13; auto-mode/classification
and multi-provider credentials/transport are separate future PRs. This change
introduces none of those product behaviors or changes to payment semantics.

## Accounting storage boundaries

Migration `0085_model_catalog_router` follows `0084_ai_pet_dialogue` and widens cost
snapshots and daily-summary cost aggregates to NUMERIC(20,9). A maximum-rate 100,000-token call costs $100,000
and remains persistable. Runtime overrides are validated before inference:
model IDs are at most 255 characters and profile names at most 64; both must
be nonempty trimmed strings. Unknown profiles within that limit still use
controlled legacy fallback. Downgrade locks usage writes and refuses atomically
if any absolute call or summary cost is at least $100,000 (outside NUMERIC(14,9)), or any model
identifier exceeds the old 64-character limit. Historical data is not truncated.

## Owner administration (PR 12)

In Selara Admin → **AI и монетизация → Модели AI**, the owner can create and
edit physical models, exact aliases, capability flags and Decimal USD prices
per 1M input/output tokens. The stable catalog key is immutable in the UI.
An empty price is NULL (unknown); zero is a known free price. Prices and current
profile assignments never recalculate or relabel historical usage rows.

Profiles expose their assignment, enabled state, AIL multiplier and effective
default route. Clear the assignment to return to the operation's legacy default:
LLM_MODEL, or LLM_SUMMARY_MODEL for summary operations. Capability requirements
may also cause an operation-specific fallback. Disabled models remain visible
and preserve usage history; they cannot be newly assigned. Disabling a referenced
model requires explicit confirmation. Physical deletion is deliberately omitted.

Owner-only endpoints under /api/miniapp/admin:
- GET/POST /ai/models
- PUT /ai/models/{key}
- GET /ai/model-profiles
- PUT /ai/model-profiles/{profile_key}

Reads use the database directly. Updates require the revision from the loaded
record; stale edits receive HTTP 409 and should be reloaded. All catalog writers
advance revision under the existing PostgreSQL transaction advisory lock.
The record stores updated_by (owner Telegram ID) and updated_at. Canonical model
IDs and exact aliases continue to share the existing unique identifier table;
collisions roll back the entire transaction and return 409.

The existing cache is invalidated only after successful commit. The web worker's
cache invalidates immediately; other bot/web workers pick up changes at their
existing 15-second TTL. Failed commits do not invalidate last-known-good runtime
state. No runtime restart or provider call is required by these forms.

In the default requests mode a request still consumes one quota unit, with the
existing free/paid limits 5/150; AIL consumption is a separate owner switch (PR 13). Stars billing and subscription terms are unchanged.
Historical profile breakdown is grouped directly by usage.model_profile,
including unassigned calls, without joining current profile assignments.
Physical model statistics remain in the existing expenses breakdown. User selection and real AIL consumption are described in the PR 13 section below.

## User model selection and AI Limits (PR 13)

Migration `0092_personal_model_ail` adds `personal_ai_profiles.model_profile_key`
(default `basic`), `selara_personal_config.quota_mode` (`NULL`/`requests`/`ail`) with
`free_daily_ail`/`paid_daily_ail`, and `ai_feature_quota_usage.model_profile`. All
columns are additive with defaults, so the previous image keeps working. Downgrade
refuses while users' profile choices, AIL mode/budgets or AIL reservations exist.

**Modes.** `requests` (default after deploy) keeps Personal at 5/150 requests in the
`personal_daily` pool; the multiplier never changes the cost and Personal keeps calling
the legacy model, so an expensive profile cannot be used for one request. `ail` charges
`ModelProfile.ail_multiplier` against the separate `personal_ail_daily` pool with the
owner-configured `free_daily_ail`/`paid_daily_ail` budgets. Request rows and AIL rows
never share a pool: switching modes does not turn spent requests into AIL. History is
kept in both directions. A broken or unreadable override never fails open: the cache
keeps the last known good config, and without one falls back to requests 5/150.

**Enabling AIL** (owner only, `GET`/`PUT /api/miniapp/admin/monetization/quota-mode`,
UI «Система лимитов Personal»): both budgets set, `0 < free < paid ≤ 100000`, `basic`
enabled and assigned to an enabled model, every enabled profile's multiplier in
(0, 1000] with at most 2 decimals (AIL units are stored in `NUMERIC(10,2)`), and an
explicit confirmation (409 without `confirm: true`). Changes apply within the 15 s TTL.

**One resolution per turn.** `personal_models.choose_from_snapshot` resolves the user's
profile against one catalog snapshot (`model_router.resolve_from_snapshot`). The
returned `ResolvedModel` carries `profile_key`, `catalog_key`, `model_id`,
`ail_multiplier`, capabilities and the snapshot itself as the pricing reference. The
quota reservation takes `units` from it, and `LlmClient.chat_simple(resolved_model=...)`
calls exactly that model and prices usage from the same snapshot without resolving
again. An admin edit after resolution affects only the next request. Unusable selected
profiles fall back to `basic` (then to the legacy model at 1 AIL) and are charged at
the effective profile's cost.

**Accounting.** Historical AIL = the `units` actually reserved
(`ai_feature_quota_usage.units`, grouped by `model_profile`); it is never recomputed
from current multipliers. Owner-exempt and released reservations are not counted. The
admin breakdown shows `ail_profiles`/`ail_consumed` next to (not instead of) the USD
per-model and per-profile statistics. AIL and USD are independent: model prices never
change multipliers and multipliers never enter USD estimates. Internal operations
(memory extraction, compression), `/autocfg`, pets and group AI do not spend AIL and
keep their own routing; there is no auto mode or classifier.
