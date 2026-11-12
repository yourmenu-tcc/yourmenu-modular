#!/usr/bin/env python3
"""Smoke test the deployed modular API; remove the temporary test account."""

import base64
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
STATE = json.loads((ROOT/'verification/2026-11-06/aws-deployment-state.json').read_text())
BASE = STATE['url']
RESULTS = []


def check(actual, expected, label):
    if actual != expected:
        raise AssertionError(f'{label}: expected HTTP {expected}, got {actual}')
    RESULTS.append(f'{label}: HTTP {actual}')
    print(RESULTS[-1], flush=True)


def request(method, path, data=None, token=None, headers=None):
    payload = json.dumps(data).encode() if data is not None else None
    request_headers = {'content-type': 'application/json', **(headers or {})}
    if token:
        request_headers['authorization'] = 'Bearer ' + token
    req = urllib.request.Request(BASE+path, data=payload, method=method,
                                 headers=request_headers)
    try:
        response = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        raw = response.read()
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = raw.decode(errors='replace')
        return response.status, body, dict(response.headers)


def invoke_registration(email, password):
    path = '/api/auth/registrar'
    event = {'version': '2.0', 'routeKey': '$default', 'rawPath': path,
             'rawQueryString': '',
             'headers': {'content-type': 'application/json', 'host': 'localhost'},
             'body': json.dumps({'nome': 'AWS modular smoke', 'email': email,
                                 'senha': password, 'roles': ['ADMINISTRADOR']}),
             'isBase64Encoded': False,
             'requestContext': {
                 'accountId': STATE['account'], 'apiId': STATE['api_id'],
                 'domainName': 'localhost', 'domainPrefix': 'local',
                 'requestId': str(uuid.uuid4()), 'routeKey': '$default',
                 'stage': '$default', 'timeEpoch': int(time.time()*1000),
                 'http': {'method': 'POST', 'path': path, 'protocol': 'HTTP/1.1',
                          'sourceIp': '127.0.0.1', 'userAgent': 'modular-smoke'}}}
    with tempfile.TemporaryDirectory(prefix='yourmenu-aws-smoke-') as dirname:
        tmp = Path(dirname)
        tmp.chmod(0o700)
        event_path, response_path = tmp/'request.json', tmp/'response.json'
        event_path.write_text(json.dumps(event))
        event_path.chmod(0o600)
        completed = subprocess.run([
            'aws', 'lambda', 'invoke', '--function-name',
            'yourmenu-modular-identity-admin', '--payload', f'fileb://{event_path}',
            '--cli-binary-format', 'raw-in-base64-out', '--region', STATE['region'],
            '--output', 'json', '--no-cli-pager', str(response_path)],
            capture_output=True, text=True, check=True)
        metadata = json.loads(completed.stdout)
        if metadata.get('FunctionError'):
            raise AssertionError('Direct identity Lambda invocation failed')
        response = json.loads(response_path.read_text())
    raw = response.get('body') or ''
    if response.get('isBase64Encoded'):
        raw = base64.b64decode(raw).decode()
    body = json.loads(raw) if raw else None
    return response['statusCode'], body


def main():
    status, _, _ = request('GET', '/api/auth/test-auth')
    check(status, 200, 'Identity route')
    status, _, _ = request('GET', '/api/restaurantes/1/categorias')
    check(status, 200, 'Public commerce route and database')
    status, _, _ = request('GET', '/api/restaurantes/1/zonas-de-entrega')
    check(status, 401, 'Protected zones without JWT')
    status, _, _ = request('POST', '/api/auth/registrar', {})
    check(status, 403, 'Anonymous administrator registration blocked by IAM')
    status, _, _ = request('POST', '/api/auth/registrar/', {})
    check(status, 403, 'Registration with trailing slash blocked by IAM')
    status, _, _ = request('POST', '/api/auth/%72egistrar', {})
    check(status, 403, 'Encoded registration path blocked by IAM')

    status, _, headers = request('OPTIONS', '/api/restaurantes/1/categorias',
        headers={'Origin': 'http://localhost:3000',
                 'Access-Control-Request-Method': 'GET',
                 'Access-Control-Request-Headers': 'X-Restaurante-Id,Authorization'})
    check(status, 200, 'CORS preflight')
    if 'x-restaurante-id' not in headers.get('access-control-allow-headers', '').lower():
        raise AssertionError('CORS preflight does not allow X-Restaurante-Id')

    email = f'aws-smoke-{uuid.uuid4().hex[:12]}@example.test'
    password = secrets.token_urlsafe(24)
    token, user_id = None, None
    try:
        status, body = invoke_registration(email, password)
        check(status, 201, 'Temporary account created through IAM Lambda invoke')
        user_id = body['id']
        status, body, _ = request('POST', '/api/auth/login',
                                  {'email': email, 'senha': password})
        check(status, 200, 'Login through HTTP API')
        token = body['token']
        status, body, _ = request('GET', '/api/usuarios/all', token=token)
        check(status, 200, 'Identity JWT validation')
        if not any(user['id'] == user_id for user in body):
            raise AssertionError('Temporary user not present in identity result')
        status, _, _ = request('GET', '/api/restaurantes/1/zonas-de-entrega',
                                token=token)
        check(status, 200, 'Commerce validates JWT issued by identity')
        status, _, _ = request('GET', '/api/restaurantes/1/pedidos/resumo',
                                token=token)
        check(status, 200, 'Order summary through HTTP API')
    finally:
        if user_id and token:
            status, _, _ = request('DELETE', f'/api/usuarios/{user_id}', token=token)
            check(status, 204, 'Temporary account removed')
        elif user_id:
            print(f'Cleanup required for test user ID {user_id}; login did not complete',
                  flush=True)

    output = ROOT/'verification/2026-11-06/aws-smoke-results.json'
    output.write_text(json.dumps({'status': 'passed', 'checks': RESULTS}, indent=2)+'\n')
    print(f'Results saved: {output}', flush=True)


if __name__ == '__main__':
    main()
