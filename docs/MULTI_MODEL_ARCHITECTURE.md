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

**`ail_multiplier` in PR 11 is metadata and does not participate in quota
consumption.** Its Decimal value is validated in (0, 1000], with 9 decimal places.
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
User model selection, auto-mode/classification, enabling actual AIL consumption,
and multi-provider credentials/transport are separate future PRs. This change
introduces none of those product behaviors or changes to payment semantics.
