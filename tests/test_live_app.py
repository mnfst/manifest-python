"""Optional conformance test against a running Manifest app, never a permissive stub.

MNFST_TEST_APP_URL must identify a disposable app allowing customer signup.
"""
import asyncio
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import requests
from mnfst import manifest
from mnfst.outbound import uninstall_outbound
from tests.helpers import wait_for


def test_real_app_outcomes(monkeypatch):
    base = os.environ.get('MNFST_TEST_APP_URL')
    if not base:
        pytest.skip('set MNFST_TEST_APP_URL to a disposable Manifest app')
    with httpx.Client(base_url=base, headers={'origin': base}) as customer:
        signup = customer.post('/api/auth/sign-up/email', json={
            'name': 'SDK conformance', 'email': f'sdk-{uuid.uuid4().hex}@example.test',
            'password': 'LocalSdkConformance-2026!'})
        signup.raise_for_status()
        project = customer.post('/api/customer/projects', json={'name': 'SDK conformance'})
        project.raise_for_status()
        project_id = project.json()['id']
        key = customer.post(f'/api/customer/projects/{project_id}/keys')
        key.raise_for_status()
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
                if body['limit'] <= 100 and self.path.endswith('/transport'):
                    self.connection.shutdown(2)
                    self.connection.close()
                    return
                failed = body['limit'] > 100 or self.path.endswith('/ineffective')
                data = {'error': {'message': 'range of limit should be [1, 100]', 'param': 'limit',
                                  'code': 'invalid_value', 'type': 'validation_error'}} if failed else body
                raw = json.dumps(data).encode()
                self.send_response(400 if failed else 200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        upstream = f'http://127.0.0.1:{server.server_port}/sdk{uuid.uuid4().hex}'
        try:
            manifest(key=key.json()['key'], url=base)
            # A single-slot pool must release the original error before retrying.
            with httpx.Client(limits=httpx.Limits(max_connections=1), timeout=5) as client:
                assert client.post(upstream + '/sync', json={'limit': 500}).json() == {'limit': 100}
            async def run_async():
                async with httpx.AsyncClient(timeout=5) as client:
                    assert (await client.post(upstream + '/async', json={'limit': 500})).status_code == 200
            asyncio.run(run_async())
            assert requests.post(upstream + '/requests', json={'limit': 500}, timeout=5).status_code == 200
            assert httpx.post(upstream + '/ineffective', json={'limit': 500}).status_code == 400
            assert httpx.post(upstream + '/transport', json={'limit': 500}).status_code == 400
            assert wait_for(lambda: len(reports) == 5, timeout=10), json.dumps(reports)
            assert all(code == 200 for code, _ in reports)
            assert sorted(body['status'] for _, body in reports) == ['failed', 'inconclusive', 'succeeded', 'succeeded', 'succeeded']
        finally:
            uninstall_outbound()
            server.shutdown()
            server.server_close()
            thread.join()
