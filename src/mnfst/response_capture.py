"""Bounded capture of failures without consuming the caller's response.

Keep a replayable raw prefix plus the remaining iterator. Successful responses
never enter this module. Capture uses the original client's read timeout.
"""
from __future__ import annotations

import io
import zlib
from typing import Iterator

import httpx

from .wire import RESPONSE_BODY_CAP


class ReplayStream(httpx.SyncByteStream):
    def __init__(self, chunks, iterator, original):
        self.chunks, self.iterator, self.original = chunks, iterator, original

    def __iter__(self):
        yield from self.chunks
        yield from self.iterator

    def close(self):
        self.original.close()


class AsyncReplayStream(httpx.AsyncByteStream):
    def __init__(self, chunks, iterator, original):
        self.chunks, self.iterator, self.original = chunks, iterator, original

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        async for chunk in self.iterator:
            yield chunk

    async def aclose(self):
        await self.original.aclose()


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


def capture_httpx(response: httpx.Response):
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
    restored = httpx.Response(response.status_code, headers=response.headers,
                              extensions={**response.extensions, "mnfst_capture_incomplete": incomplete},
                              stream=ReplayStream(chunks, iterator, response))
    raw = b''.join(chunks)[:RESPONSE_BODY_CAP + 1]
    return restored, _decode(raw, response.headers.get('content-encoding', ''))


async def capture_httpx_async(response: httpx.Response):
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
    restored = httpx.Response(response.status_code, headers=response.headers,
                              extensions={**response.extensions, "mnfst_capture_incomplete": incomplete},
                              stream=AsyncReplayStream(chunks, iterator, response))
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
    raw = b''.join(chunks)[:RESPONSE_BODY_CAP + 1]
    return response, _decode(raw, response.headers.get('content-encoding', ''))
