"""httpx2 is httpx published under a second import name; the same transport
hook covers it. Also proves the case agent tools hit: a tool that calls an
external API through a client, with no wrapping, has its rejected call
repaired underneath it."""
import httpx
import pytest

from tests.helpers import wait_for
from tests.test_outbound_httpx import build_rig
from mnfst.outbound import _Retry, _rebuild, uninstall_outbound

httpx2 = pytest.importorskip("httpx2")


@pytest.fixture
def rig():
    provider, stub = build_rig()
    yield provider, stub
    uninstall_outbound()
    stub.stop()
    provider.stop()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_httpx2_is_a_separate_package():
    assert httpx2.HTTPTransport is not httpx.HTTPTransport


def test_sync_client_heals(rig):
    provider, stub = rig
    response = httpx2.post(f"{provider.url}/v1/generate", json={"model": "m", "temperature": 0.2})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert isinstance(response, httpx2.Response)
    assert len(stub.heals) == 1
    assert stub.heals[0]["request"]["url"] == f"{provider.url}/v1/generate"
    assert wait_for(lambda: len(stub.outcomes) == 1)
    assert stub.outcomes[0][1]["response"]["statusCode"] == 200


def test_sync_client_untouched_on_success(rig):
    provider, stub = rig
    response = httpx2.post(f"{provider.url}/v1/generate", json={"model": "m"})
    assert response.status_code == 200
    assert stub.heals == []


def test_no_patch_returns_original_400_unread(rig):
    provider, stub = rig
    stub.result = None
    response = httpx2.post(f"{provider.url}/v1/generate", json={"model": "m", "temperature": 1})
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "temperature unsupported"


@pytest.mark.anyio
async def test_async_client_heals(rig):
    provider, stub = rig
    async with httpx2.AsyncClient() as client:
        response = await client.post(f"{provider.url}/v1/generate",
                                     json={"model": "m", "temperature": 0.2})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert len(stub.heals) == 1


@pytest.mark.anyio
async def test_async_no_patch_returns_original_400_unread(rig):
    provider, stub = rig
    stub.result = None
    async with httpx2.AsyncClient() as client:
        response = await client.post(f"{provider.url}/v1/generate",
                                     json={"model": "m", "temperature": 1})
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "temperature unsupported"


def test_existing_client_is_covered(rig):
    """A client built before manifest() ran, the shape a framework holds."""
    provider, stub = rig
    uninstall_outbound()
    client = httpx2.Client()
    from mnfst.config import resolve_config
    from mnfst.heal_api import AsyncHealApi, HealApi
    from mnfst.outbound import install_outbound
    config = resolve_config(api_key="mnfx_k", url=stub.url)
    install_outbound(config, HealApi(config), AsyncHealApi(config))
    response = client.post(f"{provider.url}/v1/generate", json={"model": "m", "temperature": 0.2})
    assert response.status_code == 200
    assert len(stub.heals) == 1


def test_tool_calling_an_external_api_is_repaired(rig):
    """An agent tool: a plain function that calls an API and raises on 4xx.
    Nothing is wrapped; the repaired call is what the tool sees."""
    provider, stub = rig
    client = httpx2.Client(base_url=provider.url)

    def create_order(args: dict) -> dict:
        response = client.post("/v1/orders", json=args)
        response.raise_for_status()
        return response.json()

    result = create_order({"model": "m", "temperature": 0.9})
    assert result["ok"] is True
    assert result["received"] == {"model": "m"}
    assert len(stub.heals) == 1
    assert stub.heals[0]["request"]["body"] == {"model": "m", "temperature": 0.9}


def test_tool_error_passes_through_when_unrepaired(rig):
    provider, stub = rig
    stub.result = None
    client = httpx2.Client(base_url=provider.url)

    def create_order(args: dict) -> dict:
        response = client.post("/v1/orders", json=args)
        response.raise_for_status()
        return response.json()

    with pytest.raises(httpx2.HTTPStatusError) as raised:
        create_order({"model": "m", "temperature": 0.9})
    assert raised.value.response.status_code == 400


def test_rebuild_uses_the_request_module():
    request = httpx2.Request("POST", "http://h/p", content=b'{"a": 1}',
                             extensions={"timeout": {"read": 1.0}})
    rebuilt = _rebuild(request, _Retry("http://h/p", dict(request.headers), b'{"a": 2}'), httpx2)
    assert isinstance(rebuilt, httpx2.Request)
    assert rebuilt.extensions["timeout"] == {"read": 1.0}
    assert rebuilt.content == b'{"a": 2}'


def test_httpx_and_httpx2_are_both_hooked(rig):
    provider, stub = rig
    httpx.post(f"{provider.url}/v1/generate", json={"model": "m", "temperature": 0.2})
    httpx2.post(f"{provider.url}/v1/generate", json={"model": "m", "temperature": 0.2})
    assert len(stub.heals) == 2


def test_uninstall_restores_both():
    from mnfst.outbound import _originals
    assert "httpx2_sync" not in _originals
    assert httpx2.HTTPTransport.handle_request.__name__ == "handle_request"
    assert httpx.HTTPTransport.handle_request.__name__ == "handle_request"
