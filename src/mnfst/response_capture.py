"""Bounded capture of failures without consuming the caller's response.

Keep a replayable raw prefix plus the remaining iterator. Successful responses
never enter this module. Capture uses the original client's read timeout.
"""
from __future__ import annotations

import io
import warnings
import zlib
from types import ModuleType
from typing import Iterator

import httpx

from .wire import RESPONSE_BODY_CAP

# httpx and httpx2 are separate packages with the same API. A restored
# response must be built from the package that produced the original, and
# its stream must subclass that package's byte stream, so the replay
# classes are made per module.
_streams: dict = {}


def replay_streams(mod: ModuleType = httpx):
    if mod.__name__ not in _streams:
        class ReplayStream(mod.SyncByteStream):
            def __init__(self, chunks, iterator, original):
                self.chunks, self.iterator, self.original = chunks, iterator, original

            def __iter__(self):
                yield from self.chunks
                yield from self.iterator

            def close(self):
                self.original.close()

        class AsyncReplayStream(mod.AsyncByteStream):
            def __init__(self, chunks, iterator, original):
                self.chunks, self.iterator, self.original = chunks, iterator, original

            async def __aiter__(self):
                for chunk in self.chunks:
                    yield chunk
                async for chunk in self.iterator:
                    yield chunk

            async def aclose(self):
                await self.original.aclose()

        _streams[mod.__name__] = (ReplayStream, AsyncReplayStream)
    return _streams[mod.__name__]


ReplayStream, AsyncReplayStream = replay_streams(httpx)


def _decode(raw: bytes, encoding: str) -> bytes:
    encoding = encoding.lower().strip()
    if not encoding or encoding == 'identity':
        return raw
    if encoding not in ('gzip', 'deflate'):
        # Unsupported content coding: capture metadata, never interpret bytes
        # as provider prose. The original wire response remains untouched.
        return b''
    try:
        decoder = zlib.decompressobj(31 if encoding == 'gzip' else 15)
        return decoder.decompress(raw, RESPONSE_BODY_CAP + 1)
    except zlib.error:
        return b''


def capture_httpx(response, mod: ModuleType = httpx):
    if response.is_stream_consumed:
        return response, response.content[:RESPONSE_BODY_CAP + 1]
    chunks, size = [], 0
    incomplete = True
    iterator = iter(response.stream)
    try:
        for chunk in iterator:
            chunks.append(chunk)
            size += len(chunk)
            if size > RESPONSE_BODY_CAP:
                break
        else:
            incomplete = False
            response.close()
    except Exception:
        # Replay consumed bytes and the original exception to the caller.
        # A failed capture must not turn a truncated response into success.
        import sys
        error = sys.exception() if hasattr(sys, 'exception') else sys.exc_info()[1]
        def failed():
            raise error
            yield  # pragma: no cover
        iterator = failed()
    restored = mod.Response(response.status_code, headers=response.headers,
                            extensions={**response.extensions, "mnfst_capture_incomplete": incomplete},
                            stream=replay_streams(mod)[0](chunks, iterator, response))
    raw = b''.join(chunks)[:RESPONSE_BODY_CAP + 1]
    return restored, _decode(raw, response.headers.get('content-encoding', ''))


async def capture_httpx_async(response, mod: ModuleType = httpx):
    if response.is_stream_consumed:
        return response, response.content[:RESPONSE_BODY_CAP + 1]
    chunks, size = [], 0
    incomplete = True
    iterator = response.stream.__aiter__()
    try:
        async for chunk in iterator:
            chunks.append(chunk)
            size += len(chunk)
            if size > RESPONSE_BODY_CAP:
                break
        else:
            incomplete = False
            await response.aclose()
    except Exception as exc:
        error = exc
        async def failed():
            raise error
            yield  # pragma: no cover
        iterator = failed()
    restored = mod.Response(response.status_code, headers=response.headers,
                            extensions={**response.extensions, "mnfst_capture_incomplete": incomplete},
                            stream=replay_streams(mod)[1](chunks, iterator, response))
    raw = b''.join(chunks)[:RESPONSE_BODY_CAP + 1]
    return restored, _decode(raw, response.headers.get('content-encoding', ''))


class _Reader(io.RawIOBase):
    def __init__(self, chunks: Iterator[bytes], original):
        self.iterator, self.original, self.buffer = chunks, original, b''

    def readable(self):
        return True

    def readinto(self, target):
        while not self.buffer:
            self.buffer = next(self.iterator, b'')
            if not self.buffer:
                return 0
        size = min(len(target), len(self.buffer))
        target[:size], self.buffer = self.buffer[:size], self.buffer[size:]
        return size

    def close(self):
        self.original.close()
        super().close()


def capture_requests(response):
    from itertools import chain
    from urllib3.response import HTTPResponse
    if response._content is not False:
        return response, response.content[:RESPONSE_BODY_CAP + 1]
    original = response.raw
    chunks, size = [], 0
    incomplete = True
    iterator = original.stream(amt=65536, decode_content=False)
    try:
        for chunk in iterator:
            chunks.append(chunk)
            size += len(chunk)
            if size > RESPONSE_BODY_CAP:
                break
        else:
            incomplete = False
    except Exception as exc:
        error = exc
        def failed():
            raise error
            yield  # pragma: no cover
        iterator = failed()
    response._mnfst_capture_incomplete = incomplete
    reader = io.BufferedReader(_Reader(iter(chain(chunks, iterator)), original))
    response.raw = HTTPResponse(body=reader, headers=dict(response.headers),
                                status=response.status_code, preload_content=False,
                                decode_content=False)
    # requests extracts Set-Cookie from this stdlib response metadata.
    response.raw._original_response = getattr(original, "_original_response", None)
    raw = b''.join(chunks)[:RESPONSE_BODY_CAP + 1]
    return response, _decode(raw, response.headers.get('content-encoding', ''))


async def capture_aiohttp(response):
    """Read up to the cap from an aiohttp failure, then push those bytes back
    onto the head of its stream: read(), json() and iterating `.content` all
    still see the whole body. Returns (decoded prefix, incomplete)."""
    stream = response.content
    chunks, size = [], 0
    incomplete = True
    try:
        while size <= RESPONSE_BODY_CAP:
            chunk = await stream.readany()
            if not chunk:
                incomplete = False
                break
            chunks.append(chunk)
            size += len(chunk)
    finally:
        # On a read error the bytes go back all the same; the caller's own
        # read then meets the error.
        data = b''.join(chunks)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            stream.unread_data(data)
    raw = data[:RESPONSE_BODY_CAP + 1]
    # aiohttp decompresses on the fly unless told not to; only undecoded
    # bytes still need it. (The shared empty-body stream has no counter.)
    if getattr(stream, 'total_compressed_bytes', None) is None:
        raw = _decode(raw, response.headers.get('content-encoding', ''))
    return raw, incomplete
