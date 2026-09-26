"""Optional conformance test against a running Manifest app, never a permissive stub.

MNFST_TEST_APP_URL must identify a disposable app allowing customer signup.
The app serves no patch until an operator approves one, so the test signs in
as an operator too: MNFST_TEST_OPERATOR_EMAIL and MNFST_TEST_OPERATOR_PASSWORD,
defaulting to the dev seed.
"""
import asyncio
import json
import os
import random
import string
import threading
import urllib.request
import uuid
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import aiohttp
import httpx
import pytest
import requests
from mnfst import manifest
from mnfst.outbound import uninstall_outbound
from tests.helpers import wait_for

ERROR = {'error': {'message': 'range of limit should be [1, 100]', 'param': 'limit',
                   'code': 'invalid_value', 'type': 'validation_error'}}


def signed_in(base, path, payload):
    client = httpx.Client(base_url=base, headers={'origin': base})
    client.post(path, json=payload).raise_for_status()
    return client


def approve_clamp(base, key, url):
    """Open the issue for `url` with one heal, then approve the clamp for it.
    The issue is keyed by the endpoint path, not the query, so every call
    below shares it."""
    opened = httpx.post(f'{base}/v1/heal', headers={'authorization': f'Bearer {key}'}, json={
        'traceId': uuid.uuid4().hex,
        'request': {'method': 'POST', 'url': url,
                    'headers': {'content-type': 'application/json'}, 'body': {'limit': 500}},
        'response': {'statusCode': 400, 'body': ERROR, 'truncated': False},
        'responseTimeMs': 1})
    opened.raise_for_status()
    assert opened.json()['status'] == 'no_patch', opened.text
    with closing(signed_in(base, '/api/auth/sign-in/email', {
            'email': os.environ.get('MNFST_TEST_OPERATOR_EMAIL', 'admin@colibri.local'),
            'password': os.environ.get('MNFST_TEST_OPERATOR_PASSWORD', 'colibridev')})) as operator:
        proposed = operator.post(f"/api/issues/{opened.json()['issueId']}/candidates", json={
            'operations': [{'type': 'clamp', 'args': {'path': '/limit', 'max': 100}}],
            'rationale': 'SDK conformance: the provider states the range'})
        proposed.raise_for_status()
        operator.post(f"/api/candidates/{proposed.json()['candidateId']}/approve",
                      json={}).raise_for_status()


def test_real_app_outcomes(monkeypatch):
    base = os.environ.get('MNFST_TEST_APP_URL')
    if not base:
        pytest.skip('set MNFST_TEST_APP_URL to a disposable Manifest app')
    with closing(signed_in(base, '/api/auth/sign-up/email', {
            'name': 'SDK conformance', 'email': f'sdk-{uuid.uuid4().hex}@example.test',
            'password': 'LocalSdkConformance-2026!'})) as customer:
        project = customer.post('/api/customer/projects', json={'name': 'SDK conformance'})
        project.raise_for_status()
        project_id = project.json()['id']
        key = customer.post(f'/api/customer/projects/{project_id}/keys')
        key.raise_for_status()
        key = key.json()['key']
    reports = []
    original_send = httpx.Client.send

    def observe(self, request, **kwargs):
        response = original_send(self, request, **kwargs)
        if '/v1/heal-attempts/' in request.url.path:
            reports.append((response.status_code, response.json()))
        return response

    monkeypatch.setattr(httpx.Client, 'send', observe)

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['content-length'])))
            mode = parse_qs(urlsplit(self.path).query).get('mode', [''])[0]
            if body['limit'] <= 100 and mode == 'transport':
                self.connection.shutdown(2)
                self.connection.close()
                return
            failed = body['limit'] > 100 or mode == 'ineffective'
            raw = json.dumps(ERROR if failed else body).encode()
            self.send_response(400 if failed else 200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # A fresh issue per run: an approved patch serves every project, and the
    # app keys issues by path, templating id-like segments (hex, digits) and
    # ignoring the port. A letters-only segment stays literal.
    token = ''.join(random.choices(string.ascii_lowercase, k=16))
    upstream = f'http://127.0.0.1:{server.server_port}/sdk-{token}/items'
    try:
        approve_clamp(base, key, upstream)
        manifest(key=key, url=base)
        # A single-slot pool must release the original error before retrying.
        with httpx.Client(limits=httpx.Limits(max_connections=1), timeout=5) as client:
            assert client.post(upstream + '?client=sync', json={'limit': 500}).json() == {'limit': 100}

        async def run_async():
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.post(upstream + '?client=async', json={'limit': 500})
                assert response.status_code == 200
            async with aiohttp.ClientSession() as session:
                async with session.post(upstream + '?client=aiohttp', json={'limit': 500}) as response:
                    assert await response.json() == {'limit': 100}
        asyncio.run(run_async())
        assert requests.post(upstream + '?client=requests', json={'limit': 500},
                             timeout=5).json() == {'limit': 100}
        with urllib.request.urlopen(urllib.request.Request(
                upstream + '?client=urllib', data=json.dumps({'limit': 500}).encode(),
                headers={'Content-Type': 'application/json'}), timeout=5) as response:
            assert json.loads(response.read()) == {'limit': 100}
        assert httpx.post(upstream + '?mode=ineffective', json={'limit': 500}).status_code == 400
        assert httpx.post(upstream + '?mode=transport', json={'limit': 500}).status_code == 400
        assert wait_for(lambda: len(reports) == 7, timeout=10), json.dumps(reports)
        assert all(code == 200 for code, _ in reports)
        assert sorted(body['status'] for _, body in reports) == [
            'failed', 'inconclusive'] + ['succeeded'] * 5
    finally:
        uninstall_outbound()
        server.shutdown()
        server.server_close()
        thread.join()
