import gzip
import io
import time

import anyio
import httpx
import pytest

from mnfst.config import resolve_config
from mnfst.heal_api import HealApi, AsyncHealApi
from mnfst.response_capture import capture_httpx, capture_requests
from mnfst.wire import capped_response_body
from tests.test_outbound_httpx import rig, wait_for


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def test_healed_success_preserves_stream(rig):
    provider, stub = rig
    stub.result['healedRequest'] = {'url': provider.url + '/v1/stream'}
    with httpx.Client() as client:
        with client.stream('POST', provider.url + '/v1/old', json={}) as response:
            assert response.status_code == 200
            assert not response.is_stream_consumed
            assert b''.join(response.iter_bytes()) == b'chunk0chunk1chunk2'


@pytest.mark.anyio
async def test_async_healed_success_preserves_stream(rig):
    provider, stub = rig
    stub.result['healedRequest'] = {'url': provider.url + '/v1/stream'}
    async with httpx.AsyncClient() as client:
        async with client.stream('POST', provider.url + '/v1/old', json={}) as response:
            assert response.status_code == 200
            assert not response.is_stream_consumed
            assert b''.join([chunk async for chunk in response.aiter_bytes()]) == b'chunk0chunk1chunk2'


def test_failed_retry_reports_provider_body(rig):
    provider, stub = rig
    stub.result['healedRequest'] = {'body': {'temperature': 99}}
    response = httpx.post(provider.url+'/v1/generate', json={'temperature': 2})
    assert response.status_code == 400
    assert wait_for(lambda: bool(stub.outcomes))
    assert stub.outcomes[0][1]['response']['body'] == response.json()


def test_error_capture_is_bounded_and_replays_original():
    class Chunks(httpx.SyncByteStream):
        reads = 0
        def __iter__(self):
            for _ in range(32):
                self.reads += 1
                yield b'x' * 65536
    stream = Chunks()
    original = httpx.Response(400, stream=stream)
    response, captured = capture_httpx(original)
    assert stream.reads == 2
    assert len(captured) == 65537
    assert not response.is_stream_consumed
    assert response.read() == b'x' * (32 * 65536)
    assert capped_response_body(captured)[1]


def test_requests_error_capture_is_bounded_and_replays_original():
    import requests
    from urllib3.response import HTTPResponse
    body = b'x' * 1_000_000
    response = requests.Response()
    response.status_code = 400
    response.raw = HTTPResponse(body=io.BytesIO(body), preload_content=False)
    response, captured = capture_requests(response)
    assert len(captured) == 65537
    assert response.content == body


def test_compressed_errors_preserve_original_and_bound_decompression():
    raw = gzip.compress(b'x' * 1_000_000)
    response = httpx.Response(400, headers={'content-encoding': 'gzip'}, stream=httpx.ByteStream(raw))
    restored, captured = capture_httpx(response)
    assert len(captured) == 65537
    assert restored.read() == b'x' * 1_000_000


def test_sync_heal_has_wall_clock_deadline(monkeypatch):
    import mnfst.heal_api as module
    monkeypatch.setattr(module, 'HEAL_TIMEOUT_SECONDS', .05)
    def slow(request):
        time.sleep(.3)
        return httpx.Response(200, json={'status': 'no_patch'})
    api = HealApi(resolve_config(api_key='test', url='http://test'), transport=httpx.MockTransport(slow))
    start = time.monotonic()
    assert api.heal({}) is None
    assert time.monotonic() - start < .2


@pytest.mark.anyio
async def test_async_heal_has_wall_clock_deadline(monkeypatch):
    import mnfst.heal_api as module
    monkeypatch.setattr(module, 'HEAL_TIMEOUT_SECONDS', .05)
    async def slow(request):
        await anyio.sleep(.3)
        return httpx.Response(200, json={'status': 'no_patch'})
    api = AsyncHealApi(resolve_config(api_key='test', url='http://test'), transport=httpx.MockTransport(slow))
    start = time.monotonic()
    assert await api.heal({}) is None
    assert time.monotonic() - start < .2


def test_report_failures_are_observable():
    api = HealApi(resolve_config(api_key='test', url='http://test'),
                  transport=httpx.MockTransport(lambda _: httpx.Response(400)))
    api.report_outcome('attempt', 200)
    api.join_pending_reports()
    assert api.report_failures == 1
