"""Focused checks against an explicitly configured, real SearXNG instance.

Run with WORKBENCH_TEST_SEARXNG_ENDPOINT=http://127.0.0.1:8888 pytest ... .
No recorded or generated directory is substituted for the upstream service.
"""

import asyncio
import os

import httpx
import pytest
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import now
from workbench.modules.search import dto, service


@pytest.fixture(scope="module")
def endpoint():
    value = os.environ.get("WORKBENCH_TEST_SEARXNG_ENDPOINT")
    if not value:
        pytest.skip("Set WORKBENCH_TEST_SEARXNG_ENDPOINT to a real SearXNG instance.")
    return value.rstrip("/")


@pytest.fixture(scope="module")
def catalog(endpoint):
    return asyncio.run(service.engine_catalog(endpoint))


def test_catalog_matches_real_instance(endpoint, catalog):
    response = httpx.get(endpoint + "/config", timeout=20, trust_env=False)
    response.raise_for_status()
    actual = response.json()["engines"]
    expected = {item["name"]: (item["categories"], item["enabled"]) for item in actual}
    assert {
        item.name: (item.categories, item.default_enabled) for item in catalog.engines
    } == expected
    assert catalog.endpoint == endpoint


def test_search_suffix_resolves_same_catalog(endpoint, catalog):
    suffixed = asyncio.run(service.engine_catalog(endpoint + "/search/"))
    assert suffixed.engines == catalog.engines


@pytest.mark.parametrize(
    "endpoint", ["file:///etc/passwd", "http://user:pass@localhost", "http://localhost?x=1"]
)
def test_catalog_rejects_invalid_endpoint_before_io(endpoint):
    with pytest.raises(AppError) as failure:
        asyncio.run(service.engine_catalog(endpoint))
    assert failure.value.status == 422


def test_nondefault_engines_are_explicitly_selectable(catalog):
    engine = next((item for item in catalog.engines if not item.default_enabled), None)
    if engine is None:
        pytest.skip("The configured instance has no nondefault engines.")
    service._validate_engines([engine.name], catalog)
    service._validate_engines([], catalog)


def test_unknown_selection_cannot_fall_back_to_defaults(endpoint, catalog):
    # This name is intentionally absent, to exercise refusal before /search is sent.
    unknown = "workbench-missing-engine"
    assert unknown not in {item.name for item in catalog.engines}
    connection = dto.SearchConnectionView(
        id="catalog-validation-only",
        provider_type="searxng",
        scope="user",
        workspace_id=None,
        name="catalog validation",
        endpoint=endpoint,
        enabled=True,
        engines=[catalog.engines[0].name, unknown],
        language="auto",
        region=None,
        max_results=1,
        timeout_seconds=20,
        version=1,
        status="unchecked",
        checked_at=None,
        last_error=None,
    )
    with pytest.raises(AppError) as failure:
        asyncio.run(service.query_connection(connection, dto.SearchQuery(query="SearXNG")))
    assert failure.value.code == "search_engines_unknown"
    assert unknown in failure.value.message
    assert catalog.fetched_at <= now()
