#!/usr/bin/env python3
"""Functional validation of packaged Lambdas against a disposable PostgreSQL 16.
Requires Java 25, Maven, Docker and OpenSSL. No AWS calls or persistent credentials.
Build both applications before running. Logs/results are written under target/validation.
"""
import base64
import copy
import http.server
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'target/validation'
DOCKER = os.environ.get('DOCKER', 'docker')
JAVA = str(Path(os.environ['JAVA_HOME']) / 'bin/java') if 'JAVA_HOME' in os.environ else 'java'
RESULTS = []
OBSERVATIONS = []


def check(condition, description):
    if not condition:
        raise AssertionError(description)
    RESULTS.append(description)
    print('PASS:', description, flush=True)


class Runtime:
    """Minimal local Lambda Runtime API: actual packaged Quarkus handler consumes events."""
    def __init__(self):
        self.events = queue.Queue()
        self.pending = {}
        runtime = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                if self.path != '/2018-06-01/runtime/invocation/next':
                    self.send_error(404)
                    return
                try:
                    request_id, event = runtime.events.get(timeout=90)
                except queue.Empty:
                    self.send_error(503)
                    return
                data = json.dumps(event).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Lambda-Runtime-Aws-Request-Id', request_id)
                self.send_header('Lambda-Runtime-Deadline-Ms', str(int(time.time()*1000)+60000))
                self.send_header('Lambda-Runtime-Invoked-Function-Arn', 'arn:aws:lambda:us-east-1:000000000000:function:local-validation')
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                parts = self.path.split('/')
                request_id = parts[-2]
                pending = runtime.pending.get(request_id)
                if pending:
                    pending['response'] = json.loads(body)
                    pending['done'].set()
                else:
                    runtime.init_error = body.decode(errors='replace')
                self.send_response(202)
                self.send_header('Content-Length', '0')
                self.end_headers()

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_port

    def invoke(self, method, path, body=None, token=None, headers=None):
        request_id = str(uuid.uuid4())
        h = {'content-type': 'application/json', 'host': 'localhost'}
        if token:
            h['authorization'] = 'Bearer ' + token
        h.update(headers or {})
        event = {'version': '2.0', 'routeKey': '$default', 'rawPath': path, 'rawQueryString': '',
                 'headers': h, 'body': json.dumps(body) if body is not None else None,
                 'isBase64Encoded': False, 'requestContext': {'accountId': '000000000000',
                 'apiId': 'local', 'domainName': 'localhost', 'domainPrefix': 'local',
                 'requestId': request_id, 'routeKey': '$default', 'stage': '$default',
                 'timeEpoch': int(time.time()*1000), 'http': {'method': method, 'path': path,
                 'protocol': 'HTTP/1.1', 'sourceIp': '127.0.0.1', 'userAgent': 'functional-validation'}}}
        pending = {'done': threading.Event()}
        self.pending[request_id] = pending
        self.events.put((request_id, event))
        if not pending['done'].wait(60):
            raise AssertionError('Lambda response timeout; inspect application logs in target/validation')
        response = pending['response']
        del self.pending[request_id]
        text = response.get('body') or ''
        if response.get('isBase64Encoded'):
            text = base64.b64decode(text).decode()
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            data = text
        return response.get('statusCode'), data, response.get('headers', {})

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def api(runtime, method, path, status, body=None, token=None, headers=None):
    actual, data, response_headers = runtime.invoke(method, path, body, token, headers)
    check(actual == status, f'{method} {path} -> {status} (actual {actual})')
    return data


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/'results.json').unlink(missing_ok=True)
    processes, runtimes, handles = [], [], []
    container = None
    with tempfile.TemporaryDirectory(prefix='yourmenu-validation-') as temporary:
        tmp = Path(temporary)
        env = dict(os.environ)
        # Do not inherit a real AWS runtime, datasource or key configuration.
        for k in list(env):
            if k.startswith(('AWS_', 'DB_', 'JWT_', 'QUARKUS_', 'SMALLRYE_', 'MP_JWT_')):
                del env[k]
        env.update(DB_USERNAME='validation', DB_PASSWORD=secrets.token_urlsafe(24),
                   DB_NAME='yourmenu_validation', DB_HOST='127.0.0.1', DB_SSLMODE='disable',
                   MAVEN_OPTS='-Xmx512m')
        env['POSTGRES_USER'] = env['DB_USERNAME']
        env['POSTGRES_PASSWORD'] = env['DB_PASSWORD']
        env['POSTGRES_DB'] = env['DB_NAME']
        private, public = tmp/'private.pem', tmp/'public.pem'
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-out', str(private)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        private.chmod(0o600)
        subprocess.run(['openssl', 'pkey', '-in', str(private), '-pubout', '-out', str(public)], check=True, stdout=subprocess.DEVNULL)
        env.update(JWT_PRIVATE_KEY_LOCATION=private.as_uri(), JWT_PUBLIC_KEY_LOCATION=public.as_uri())
        try:
            container = subprocess.check_output([DOCKER, 'run', '--rm', '-d', '--label', 'yourmenu-purpose=modular-functional-validation', '-e', 'POSTGRES_USER', '-e', 'POSTGRES_PASSWORD', '-e', 'POSTGRES_DB', '-p', '127.0.0.1::5432', 'postgres:16-alpine'], env=env, text=True).strip()
            port = subprocess.check_output([DOCKER, 'port', container, '5432/tcp'], text=True).strip().split(':')[-1]
            env['DB_PORT'] = port
            for _ in range(60):
                ready = subprocess.run([DOCKER, 'exec', container, 'pg_isready', '-U', env['DB_USERNAME'], '-d', env['DB_NAME']], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if ready.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise AssertionError('Disposable PostgreSQL did not become ready')

            def start(module, schema='none'):
                runtime = Runtime()
                runtimes.append(runtime)
                log = open(OUT/f'{module}-{schema}.log', 'w')
                handles.append(log)
                child_env = dict(env, AWS_LAMBDA_RUNTIME_API=f'127.0.0.1:{runtime.port}', AWS_LAMBDA_FUNCTION_MEMORY_SIZE='256', AWS_LAMBDA_FUNCTION_NAME=module, AWS_LAMBDA_FUNCTION_VERSION='$LATEST')
                if module == 'commerce':
                    child_env.pop('JWT_PRIVATE_KEY_LOCATION', None)
                jar = ROOT/module/'target'/f'yourmenu-{module}-1.0.0-SNAPSHOT-runner.jar'
                process = subprocess.Popen([JAVA, '-Xmx256m', f'-Dquarkus-internal.aws-lambda.test-api=127.0.0.1:{runtime.port}', f'-Dquarkus.hibernate-orm.schema-management.strategy={schema}', '-jar', str(jar)], env=child_env, stdout=log, stderr=subprocess.STDOUT)
                processes.append(process)
                return runtime, process

            # Only the dedicated new container is initialized; never accept an external DB URL.
            bootstrap, process = start('commerce', 'drop-and-create')
            api(bootstrap, 'GET', '/api/restaurantes/1/categorias', 200)
            process.terminate(); process.wait(timeout=20)
            commerce, _ = start('commerce')
            identity, _ = start('identity-admin')
            api(identity, 'GET', '/hello', 200)
            api(commerce, 'GET', '/api/restaurantes/1/categorias', 200)
            api(identity, 'GET', '/api/restaurantes/1/categorias', 404)
            api(commerce, 'POST', '/api/auth/login', 404, {})

            password = secrets.token_urlsafe(24)
            def register(email, roles):
                return api(identity, 'POST', '/api/auth/registrar', 201, {'nome':'Validation', 'email':email, 'senha':password, 'roles':roles})
            def login(email):
                response = api(identity, 'POST', '/api/auth/login', 200, {'email':email, 'senha':password})
                return response['token']
            register('admin@example.test', ['ADMINISTRADOR'])
            admin = login('admin@example.test')
            manager_dto = {'nome':'Manager', 'email':'manager@example.test', 'senha':password, 'roles':['GERENTE']}
            restaurant_dto = {'nome':'Restaurante A', 'descricao':'Teste', 'aberto':True, 'endereco':'Rua A', 'horaAbertura':'10:00', 'horaFechamento':'22:00', 'tempoDeEntrega':30, 'logoUrl':'logo', 'bannerUrl':'banner', 'gerenteEntidade':manager_dto}
            a = api(identity, 'POST', '/api/restaurante', 201, restaurant_dto, admin)['id']
            other = dict(restaurant_dto, nome='Restaurante B', gerenteEntidade=None)
            b = api(identity, 'POST', '/api/restaurante', 201, other, admin)['id']
            manager = login('manager@example.test')
            prefix = f'/api/restaurantes/{a}'
            # Actual token issued by identity-admin is verified in the commerce process.
            cat = api(commerce, 'POST', prefix+'/categorias', 201, {'nome':'Pizzas','descricao':''}, manager)['id']
            api(identity, 'GET', f'/api/restaurantes/{a}/colaboradores', 200, token=manager)
            api(commerce, 'POST', f'/api/restaurantes/{b}/categorias', 403, {'nome':'Denied','descricao':''}, manager)
            api(commerce, 'POST', f'/api/restaurantes/{b}/categorias', 400, {'nome':'Denied','descricao':''}, manager, {'x-restaurante-id':str(a)})
            api(commerce, 'POST', prefix+'/categorias', 400, {'nome':'Denied','descricao':''}, manager, {'x-restaurante-id':'invalid'})
            api(commerce, 'GET', prefix+'/categorias', 200)
            api(commerce, 'POST', prefix+'/categorias', 401, {'nome':'Denied','descricao':''})
            api(commerce, 'POST', prefix+'/categorias', 401, {'nome':'Denied','descricao':''}, 'invalid.token.signature')
            api(commerce, 'GET', prefix+'/zonas-de-entrega', 200, token=manager)
            # Expired but correctly signed JWT; no private key/token is retained.
            header, payload, _ = manager.split('.')
            claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
            claims['exp'] = int(time.time()) - 3600
            claims['iat'] = int(time.time()) - 7200
            encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
            signing_input = (header + '.' + encoded).encode()
            signature = subprocess.check_output(['openssl', 'dgst', '-sha256', '-sign', str(private)], input=signing_input)
            expired = signing_input.decode() + '.' + base64.urlsafe_b64encode(signature).decode().rstrip('=')
            api(commerce, 'GET', prefix+'/zonas-de-entrega', 401, token=expired)
            employee_id = register('employee@example.test', ['FUNCIONARIO'])['id']
            employee = login('employee@example.test')
            api(identity, 'POST', prefix+'/colaboradores', 201, {'usuarioId':employee_id, 'papel':'FUNCIONARIO', 'status':'ATIVO'}, manager)
            api(commerce, 'GET', prefix+'/zonas-de-entrega', 200, token=employee)
            api(commerce, 'POST', prefix+'/zonas-de-entrega', 403, {'localizacao':'Denied','valor':1}, employee)
            # Failed restaurant creation after user persistence must roll the user back too.
            invalid = dict(restaurant_dto, endereco=None, gerenteEntidade=dict(manager_dto, email='rollback@example.test'))
            api(identity, 'POST', '/api/restaurante', 500, invalid, admin)
            users = api(identity, 'GET', '/api/usuarios/all', 200, token=admin)
            check(not any(u['email']=='rollback@example.test' for u in users), 'Restaurant/user transaction rolls back together')
            api(identity, 'GET', '/api/usuarios/all', 403, token=manager)
            dish = api(commerce, 'POST', prefix+'/pratos', 201, {'nome':'Pizza', 'descricao':'Pizza de teste', 'categoriaId':cat, 'preco':40, 'imagemUrl':'image'}, manager)['id']
            zone = api(commerce, 'POST', prefix+'/zonas-de-entrega', 201, {'localizacao':'Centro','valor':7.5}, manager)['id']
            other_zone = api(commerce, 'POST', f'/api/restaurantes/{b}/zonas-de-entrega', 201, {'localizacao':'Tenant B only','valor':9}, admin)['id']
            status, leaked, _ = commerce.invoke('GET', f'/api/restaurantes/{b}/zonas-de-entrega', token=manager, headers={'x-restaurante-id':str(a)})
            check(status == 400, f'Conflicting header and path tenant -> 400 (actual {status})')
            check(not isinstance(leaked, list) or not any(z['id'] == other_zone for z in leaked),
                  'Conflicting tenant request does not expose zones from B')
            statuses = ('RASCUNHO', 'RECEBIDO', 'EM_PREPARO', 'PRONTO',
                        'SAIU_PARA_ENTREGA', 'ENTREGUE', 'CANCELADO')
            empty_summary = {'totalPedidos': 0, 'totalPedidosAbertos': 0,
                             'totalPedidosCancelados': 0, 'totalPedidosConcluidos': 0,
                             'pedidosPorStatus': {status: 0 for status in statuses}}
            check(api(commerce, 'GET', prefix+'/pedidos/resumo', 200, token=manager) == empty_summary,
                  'Empty restaurant has zero order summary')
            api(commerce, 'GET', prefix+'/pedidos/resumo', 401)
            api(commerce, 'GET', f'/api/restaurantes/{b}/pedidos/resumo', 403, token=manager)
            api(commerce, 'GET', f'/api/restaurantes/{b}/pedidos/resumo', 400,
                token=manager, headers={'x-restaurante-id':str(a)})
            order = {'tipoEntrega':'ENTREGA', 'cliente':{'primeiroNome':'Ana','ultimoNome':'Silva','telefone':'85999999999'}, 'endereco':{'zonaDeEntregaId':zone,'cep':'60000000','cidade':'Fortaleza','rua':'Rua A','numero':'1','bairro':'Centro'}, 'itens':[{'quantidade':2,'produtos':[{'pratoId':dish,'divisao':1}]}]}
            created = api(commerce, 'POST', prefix+'/pedidos', 201, order)
            check(created['preco'] == 87.5, 'Order preserves product price and delivery fee')
            received_summary = dict(empty_summary, totalPedidos=1, totalPedidosAbertos=1,
                                    pedidosPorStatus=dict(empty_summary['pedidosPorStatus'], RECEBIDO=1))
            check(api(commerce, 'GET', prefix+'/pedidos/resumo', 200, token=employee) == received_summary,
                  'Employee sees one received order in own restaurant summary')
            order_id = created['id']
            api(commerce, 'GET', prefix+f'/pedidos/{order_id}', 200, token=manager)
            api(commerce, 'DELETE', prefix+f'/categorias/{cat}', 400, token=manager)
            api(commerce, 'DELETE', prefix+f'/pratos/{dish}', 400, token=manager)
            api(commerce, 'DELETE', prefix+f'/zonas-de-entrega/{zone}', 400, token=manager)
            invalid_order = copy.deepcopy(order)
            invalid_order['itens'][0]['produtos'][0]['divisao'] = 0.5
            api(commerce, 'POST', prefix+'/pedidos', 400, invalid_order)
            orders = api(commerce, 'GET', prefix+'/pedidos', 200, token=manager)
            check(len(orders)==1, 'Invalid order leaves no partial persisted order')
            api(commerce, 'PATCH', prefix+f'/pedidos/{order_id}/status', 200, {'status':'EM_PREPARO'}, manager)
            api(commerce, 'DELETE', prefix+f'/pedidos/{order_id}', 204, token=manager)
            api(commerce, 'POST', prefix+'/pedidos', 201, order)
            delivered_id = api(commerce, 'POST', prefix+'/pedidos', 201, order)['id']
            for next_status in ('EM_PREPARO', 'PRONTO', 'SAIU_PARA_ENTREGA', 'ENTREGUE'):
                api(commerce, 'PATCH', prefix+f'/pedidos/{delivered_id}/status', 200,
                    {'status':next_status}, manager)
            mixed_summary = {'totalPedidos': 3, 'totalPedidosAbertos': 1,
                             'totalPedidosCancelados': 1, 'totalPedidosConcluidos': 1,
                             'pedidosPorStatus': dict(empty_summary['pedidosPorStatus'],
                                                      RECEBIDO=1, ENTREGUE=1, CANCELADO=1)}
            check(api(commerce, 'GET', prefix+'/pedidos/resumo', 200, token=manager) == mixed_summary,
                  'Summary counts open, cancelled and delivered orders once each')
            b_prefix = f'/api/restaurantes/{b}'
            b_cat = api(commerce, 'POST', b_prefix+'/categorias', 201,
                        {'nome':'B only','descricao':''}, admin)['id']
            b_dish = api(commerce, 'POST', b_prefix+'/pratos', 201,
                         {'nome':'B dish','descricao':'Only B','categoriaId':b_cat,
                          'preco':10,'imagemUrl':'image'}, admin)['id']
            b_order = copy.deepcopy(order)
            b_order['tipoEntrega'] = 'RETIRADA'
            b_order.pop('endereco')
            b_order['itens'][0]['produtos'][0]['pratoId'] = b_dish
            api(commerce, 'POST', b_prefix+'/pedidos', 201, b_order)
            b_summary = dict(empty_summary, totalPedidos=1, totalPedidosAbertos=1,
                             pedidosPorStatus=dict(empty_summary['pedidosPorStatus'], RECEBIDO=1))
            check(api(commerce, 'GET', b_prefix+'/pedidos/resumo', 200, token=admin) == b_summary,
                  'Administrator sees B order summary')
            check(api(commerce, 'GET', prefix+'/pedidos/resumo', 200, token=manager) == mixed_summary,
                  'B order does not change A summary')
            api(commerce, 'GET', b_prefix+'/pedidos/resumo', 403, token=manager)
            for p in processes:
                if p.poll() is None:
                    p.terminate(); p.wait(timeout=20)
            # Original tests + entity/CDI checks + packaged hello IT; same disposable DB.
            for module in ['identity-admin', 'commerce']:
                with open(OUT/f'maven-{module}-verify.log', 'w') as log:
                    subprocess.run([str(ROOT/'mvnw'), '-B', '-f', str(ROOT/'pom.xml'), '-pl', module, '-am', 'verify', '-DskipITs=false', '-DargLine=-Xmx256m'], env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
                check(True, module+' Maven verify (JUnit/CDI/JPA)')
            (OUT/'results.json').write_text(json.dumps({'status':'passed_with_known_baseline_risk' if OBSERVATIONS else 'passed', 'checks':RESULTS, 'observations':OBSERVATIONS}, indent=2)+'\n')
        finally:
            for p in processes:
                if p.poll() is None:
                    p.terminate()
                    try:
                        p.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        p.kill(); p.wait()
            for runtime in runtimes:
                runtime.close()
            for handle in handles:
                handle.close()
            if container:
                subprocess.run([DOCKER, 'rm', '-f', container], stdout=subprocess.DEVNULL, check=True)


if __name__ == '__main__':
    main()
