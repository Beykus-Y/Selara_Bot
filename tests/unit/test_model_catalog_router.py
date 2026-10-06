from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIConnectionError
from pydantic import BaseModel

from selara.application.model_catalog import (
    CachedModelCatalogProvider, CatalogModel, CatalogSnapshot, ModelCapabilities, ModelProfile,
)
from selara.application.model_router import DefaultModelRouter
from selara.infrastructure.llm.client import LlmClient, LlmConfig, LlmClientError


MODEL = CatalogModel("test_model", "provider/model", "Test", prompt_price_usd_per_million=Decimal("1"),
                     completion_price_usd_per_million=Decimal("2"), aliases=("provider/model-snapshot",),
                     capabilities=ModelCapabilities(True, True, True))
PROFILE = ModelProfile("analytics", "Аналитик", MODEL.key)


def snapshot(model=MODEL, profile=PROFILE):
    return CatalogSnapshot((model,), (profile,))


def response(model="provider/model-snapshot", content="hello"):
    return SimpleNamespace(model=model, usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=2000, total_tokens=3000),
                           choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def client(provider=None, *, structured=False):
    llm = LlmClient(LlmConfig(api_key="test", model="legacy", summary_model="summary",
                             supports_structured_output=structured), model_catalog=provider)
    llm._client.chat.completions.create = AsyncMock(return_value=response())
    return llm


@pytest.mark.parametrize("field,value", [
    ("key", ""), ("key", "Bad-Key"), ("model_id", " "), ("model_id", "x" * 256),
    ("prompt_price_usd_per_million", Decimal("-1")), ("completion_price_usd_per_million", Decimal("NaN")),
    ("prompt_price_usd_per_million", Decimal("Infinity")), ("prompt_price_usd_per_million", 1.0),
    ("prompt_price_usd_per_million", Decimal("0.0000000001")),
    ("aliases", ("same", "same")), ("aliases", (MODEL.model_id,)), ("enabled", "true"),
])
def test_model_validation(field, value):
    with pytest.raises(ValueError):
        replace(MODEL, **{field: value})


@pytest.mark.parametrize("value", [Decimal("0"), Decimal("-1"), Decimal("1001"), Decimal("NaN"),
                                  Decimal("Infinity"), Decimal("-Infinity"), 5.0])
def test_multiplier_validation(value):
    with pytest.raises(ValueError):
        replace(PROFILE, ail_multiplier=value)


def test_identifiers_share_unique_namespace_and_snapshot_is_immutable():
    other = CatalogModel("other", "another", "Other", aliases=(MODEL.model_id,))
    with pytest.raises(ValueError):
        CatalogSnapshot((MODEL, other))
    with pytest.raises(TypeError):
        snapshot().models_by_key[MODEL.key] = other
    assert snapshot().models_by_id[MODEL.aliases[0]] is MODEL


@pytest.mark.parametrize("profile,model,required,expected", [
    (PROFILE, MODEL, ModelCapabilities(), MODEL.model_id),
    (replace(PROFILE, model_key=None), MODEL, ModelCapabilities(), "legacy"),
    (replace(PROFILE, enabled=False), MODEL, ModelCapabilities(), "legacy"),
    (PROFILE, replace(MODEL, enabled=False), ModelCapabilities(), "legacy"),
    (PROFILE, replace(MODEL, capabilities=ModelCapabilities()), ModelCapabilities(True), "legacy"),
    (PROFILE, replace(MODEL, capabilities=ModelCapabilities()), ModelCapabilities(False, True), "legacy"),
    (PROFILE, replace(MODEL, capabilities=ModelCapabilities()), ModelCapabilities(False, False, True), "legacy"),
])
async def test_router_assignment_and_capabilities(profile, model, required, expected):
    provider = CachedModelCatalogProvider(AsyncMock(return_value=snapshot(model, profile)))
    resolved = await DefaultModelRouter("legacy", provider).resolve(profile_key="analytics", required=required)
    assert resolved.model_id == expected
    assert resolved.is_fallback == (expected == "legacy")
    assert resolved.profile_key == "analytics"
    assert resolved.ail_multiplier == profile.ail_multiplier


async def test_router_legacy_summary_unknown_profile_and_provider_failure():
    provider = SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("offline")))
    assert (await DefaultModelRouter("legacy", provider).resolve(profile_key="basic")).model_id == "legacy"
    provider.get = AsyncMock(return_value=snapshot())
    router = DefaultModelRouter("legacy", provider)
    assert (await router.resolve(profile_key="missing")).is_fallback
    assert (await router.resolve(profile_key="basic", legacy_model="summary")).model_id == "summary"
    assert (await router.resolve()).model_id == "legacy"


async def test_cache_ttl_invalidate_lkg_and_single_flight():
    now = [0.0]
    load = AsyncMock(return_value=snapshot())
    cache = CachedModelCatalogProvider(load, ttl_seconds=10, clock=lambda: now[0])
    await asyncio.gather(*(cache.get() for _ in range(20)))
    assert load.await_count == 1
    now[0] = 11
    load.return_value = snapshot(replace(MODEL, enabled=False))
    refreshed = await cache.get()
    assert not refreshed.models[0].enabled
    assert load.await_count == 2
    cache.invalidate()
    load.side_effect = RuntimeError("offline")
    assert await cache.get() is refreshed
    assert load.await_count == 3
    now[0] = 22
    load.side_effect = None
    load.return_value = snapshot()
    assert (await cache.get()).models[0].enabled


async def test_cache_empty_or_invalid_database_and_timeout_use_legacy():
    for load in (AsyncMock(side_effect=RuntimeError("offline")), AsyncMock(return_value="invalid"),
                 AsyncMock(return_value=CatalogSnapshot())):
        cache = CachedModelCatalogProvider(load)
        assert (await DefaultModelRouter("legacy", cache).resolve(profile_key="basic")).model_id == "legacy"
    async def stalled():
        await asyncio.Event().wait()
    cache = CachedModelCatalogProvider(stalled, load_timeout_seconds=0.01)
    assert (await cache.get()).models == ()


async def test_invalidate_during_refresh_is_not_lost():
    entered, release = asyncio.Event(), asyncio.Event()
    async def load():
        entered.set()
        await release.wait()
        return snapshot()
    cache = CachedModelCatalogProvider(load)
    refresh = asyncio.create_task(cache.get())
    await entered.wait()
    cache.invalidate()
    release.set()
    await refresh
    assert cache._expires_at == 0


class Answer(BaseModel):
    answer: str


@pytest.mark.parametrize("method,expected", [("chat_simple", "legacy"), ("chat_with_tools", "legacy"),
                                             ("summarize", "summary"), ("chat_structured", "summary")])
async def test_client_defaults_and_overrides(method, expected):
    llm = client()
    llm._client.chat.completions.create.return_value = response(None, '{"answer":"ok"}')
    kwargs = {"tools": []} if method == "chat_with_tools" else {"response_model": Answer} if method == "chat_structured" else {}
    result = await getattr(llm, method)([], **kwargs)
    assert llm._client.chat.completions.create.await_args.kwargs["model"] == expected
    assert result.usages[0].model == expected
    result = await getattr(llm, method)([], model="explicit", **kwargs)
    assert llm._client.chat.completions.create.await_args.kwargs["model"] == "explicit"
    assert result.usages[0].model == "explicit"


async def test_profile_routes_alias_cost_and_records_snapshot_profile():
    cache = CachedModelCatalogProvider(AsyncMock(return_value=snapshot()))
    llm = client(cache)
    result = await llm.chat_simple([], model_profile="analytics")
    assert llm._client.chat.completions.create.await_args.kwargs["model"] == MODEL.model_id
    assert result.usages[0].model == MODEL.aliases[0]
    assert result.usages[0].model_profile == "analytics"
    assert result.usages[0].estimated_cost_usd == Decimal("0.005")


async def test_price_frozen_before_provider_response_and_in_corrective_rounds():
    load = AsyncMock(return_value=snapshot())
    cache = CachedModelCatalogProvider(load)
    llm = client(cache)
    async def call(**kwargs):
        load.return_value = snapshot(replace(MODEL, prompt_price_usd_per_million=Decimal("100")))
        cache.invalidate()
        return response(content="bad" if llm._client.chat.completions.create.await_count == 1 else '{"answer":"ok"}')
    llm._client.chat.completions.create.side_effect = call
    result = await llm.chat_structured([], response_model=Answer, model_profile="analytics")
    assert len(result.usages) == 2
    assert all(u.estimated_cost_usd == Decimal("0.005") for u in result.usages)
    second = await llm.chat_simple([], model_profile="analytics")
    assert second.usages[0].estimated_cost_usd == Decimal("0.104")
    assert result.usages[0].estimated_cost_usd == Decimal("0.005")


@pytest.mark.parametrize("model,expected", [(replace(MODEL, prompt_price_usd_per_million=None), None),
    (replace(MODEL, prompt_price_usd_per_million=Decimal("0"), completion_price_usd_per_million=Decimal("0")), Decimal("0")),
    (replace(MODEL, enabled=False), Decimal("0.005"))])
async def test_null_zero_and_disabled_prices(model, expected):
    llm = client(CachedModelCatalogProvider(AsyncMock(return_value=snapshot(model))))
    result = await llm.chat_simple([], model=MODEL.model_id)
    assert result.usages[0].estimated_cost_usd == expected
    assert result.value == "hello"


@pytest.mark.parametrize("model,expected", [("gpt-4o-mini", Decimal("0.00135")),
    ("gpt-4o-mini-2024-07-18", Decimal("0.00135")), ("gpt-4o", Decimal("0.0225")),
    ("gpt-4o-2024-08-06", Decimal("0.0225")), ("gpt-4o-2024-11-20", Decimal("0.0225")),
    ("provider/model-unknown-snapshot", None)])
async def test_legacy_prices_and_unknown_reported_model(model, expected):
    llm = client(CachedModelCatalogProvider(AsyncMock(return_value=snapshot())))
    llm._client.chat.completions.create.return_value = response(model)
    result = await llm.chat_simple([], model=MODEL.model_id)
    assert result.usages[0].model == model
    assert result.usages[0].estimated_cost_usd == expected


async def test_explicit_catalog_null_overrides_legacy_price():
    model = replace(MODEL, model_id="gpt-4o-mini", aliases=(), prompt_price_usd_per_million=None)
    llm = client(CachedModelCatalogProvider(AsyncMock(return_value=snapshot(model))))
    llm._client.chat.completions.create.return_value = response("gpt-4o-mini")
    assert (await llm.chat_simple([])).usages[0].pricing_status == "unknown"


async def test_structured_catalog_capability_controls_native_schema():
    llm = client(CachedModelCatalogProvider(AsyncMock(return_value=snapshot())))
    llm._client.chat.completions.create.return_value = response(content='{"answer":"ok"}')
    await llm.chat_structured([], response_model=Answer, model_profile="analytics")
    assert llm._client.chat.completions.create.await_args.kwargs["response_format"]["type"] == "json_schema"
    llm = client(CachedModelCatalogProvider(AsyncMock(return_value=snapshot(replace(MODEL, capabilities=ModelCapabilities())))), structured=True)
    llm._client.chat.completions.create.return_value = response(content='{"answer":"ok"}')
    await llm.chat_structured([], response_model=Answer, model_profile="analytics")
    assert llm._client.chat.completions.create.await_args.kwargs["model"] == "summary"


async def test_unknown_pricing_failure_does_not_block_call_and_overrides_validate():
    llm = client(SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("offline"))))
    assert (await llm.chat_simple([], model_profile="analytics")).value == "hello"
    assert llm._client.chat.completions.create.await_args.kwargs["model"] == "legacy"
    with pytest.raises(ValueError):
        await llm.chat_simple([], model="x", model_profile="analytics")
    with pytest.raises(ValueError):
        await llm.chat_simple([], model=" ")


async def test_failed_attempts_keep_selected_model_profile():
    llm = client(CachedModelCatalogProvider(AsyncMock(return_value=snapshot())))
    llm._client.chat.completions.create.side_effect = APIConnectionError(request=httpx.Request("POST", "https://example.com"))
    with pytest.raises(LlmClientError) as raised:
        await llm.chat_simple([], model_profile="analytics")
    assert len(raised.value.usages) == 3
    assert all(u.model == MODEL.model_id and u.model_profile == "analytics" for u in raised.value.usages)


@pytest.mark.parametrize("method", ["chat_simple", "chat_with_tools", "summarize", "chat_structured"])
@pytest.mark.parametrize("overrides", [
    {"model": "x" * 256}, {"model_profile": "x" * 65}, {"model_profile": ""},
    {"model_profile": " basic "}, {"model": 123}, {"model_profile": 123},
])
async def test_invalid_runtime_identifiers_are_rejected_before_inference(method, overrides):
    llm = client()
    kwargs = {"tools": []} if method == "chat_with_tools" else {"response_model": Answer} if method == "chat_structured" else {}
    with pytest.raises(ValueError):
        await getattr(llm, method)([], **kwargs, **overrides)
    llm._client.chat.completions.create.assert_not_awaited()


async def test_runtime_identifier_exact_storage_boundaries_are_accepted():
    llm = client()
    llm._client.chat.completions.create.return_value = response(None)
    model = "x" * 255
    assert (await llm.chat_simple([], model=model)).usages[0].model == model
    profile = "x" * 64
    usage = (await llm.chat_simple([], model_profile=profile)).usages[0]
    assert usage.model == "legacy" and usage.model_profile == profile
