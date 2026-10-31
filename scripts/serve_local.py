#!/usr/bin/env python3
"""Run both packaged Lambda applications behind one disposable local HTTP gateway."""

import argparse
import http.server
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
from validate_local import Runtime


ROOT = Path(__file__).resolve().parents[1]


def run(*command, env=None):
    return subprocess.run(command, env=env, check=True, text=True, capture_output=True).stdout.strip()


def wait_for_postgres(container, user, database):
    for _ in range(60):
        result = subprocess.run(
            ['docker', 'exec', container, 'pg_isready', '-U', user, '-d', database],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError('PostgreSQL local não ficou pronto em 60 segundos.')


def start_app(module, runtime, env, logs, schema='none'):
    app_env = dict(env, AWS_LAMBDA_RUNTIME_API=f'127.0.0.1:{runtime.port}',
                   AWS_LAMBDA_FUNCTION_NAME=module, AWS_LAMBDA_FUNCTION_VERSION='$LATEST',
                   AWS_LAMBDA_FUNCTION_MEMORY_SIZE='256')
    if module == 'commerce':
        app_env.pop('JWT_PRIVATE_KEY_LOCATION', None)
    jar = ROOT / module / 'target' / f'yourmenu-{module}-1.0.0-SNAPSHOT-runner.jar'
    log = open(logs / f'{module}-{schema}.log', 'w', encoding='utf-8')
    process = subprocess.Popen(
        ['java', '-Xmx256m', f'-Dquarkus-internal.aws-lambda.test-api=127.0.0.1:{runtime.port}',
         f'-Dquarkus.hibernate-orm.schema-management.strategy={schema}', '-jar', str(jar)],
        env=app_env, stdout=log, stderr=subprocess.STDOUT,
    )
    return process, log


def gateway(identity, commerce, port):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            print(format % args, flush=True)

        def handle_api(self):
            path = self.path.split('?', 1)[0]
            identity_route = (path.startswith(('/api/auth/', '/api/usuarios/', '/api/restaurante', '/hello'))
                              and '/categorias' not in path and '/pratos' not in path
                              and '/zonas-de-entrega' not in path and '/pedidos' not in path)
            target = identity if identity_route else commerce
            length = int(self.headers.get('Content-Length', '0'))
            raw_body = self.rfile.read(length) if length else b''
            try:
                body = json.loads(raw_body) if raw_body else None
            except json.JSONDecodeError:
                self.send_error(400, 'O corpo deve ser JSON válido.')
                return
            headers = {}
            for name in ('Authorization', 'X-Restaurante-Id', 'Accept'):
                if name in self.headers:
                    headers[name.lower()] = self.headers[name]
            try:
                status, data, upstream_headers = target.invoke(self.command, path, body, headers=headers)
            except Exception as error:
                print(f'Falha ao invocar {self.command} {path}: {error}', flush=True)
                self.send_error(502, 'A aplicação Lambda não respondeu; veja target/local-server/*.log.')
                return
            is_json = not isinstance(data, str)
            content = json.dumps(data, ensure_ascii=False).encode() if is_json else data.encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json' if is_json else 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            location = next((value for key, value in upstream_headers.items() if key.lower() == 'location'), None)
            if location:
                self.send_header('Location', location)
            self.end_headers()
            self.wfile.write(content)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_api

    return http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8080, help='porta HTTP para o Insomnia (padrão: 8080)')
    parser.add_argument('--no-build', action='store_true', help='usa os artefatos já compilados')
    args = parser.parse_args()

    if not args.no_build:
        print('Compilando os dois módulos...', flush=True)
        subprocess.run([str(ROOT / 'mvnw'), '-q', '-f', str(ROOT / 'pom.xml'),
                        '-pl', 'identity-admin,commerce', '-am', 'clean', 'package', '-DskipTests'], check=True)

    logs = ROOT / 'target' / 'local-server'
    logs.mkdir(parents=True, exist_ok=True)
    logs.chmod(0o700)
    container = None
    processes, runtimes, handles = [], [], []
    with tempfile.TemporaryDirectory(prefix='yourmenu-modular-local-') as temporary:
        tmp = Path(temporary)
        private, public = tmp / 'private.pem', tmp / 'public.pem'
        run('openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-out', str(private))
        private.chmod(0o600)
        run('openssl', 'pkey', '-in', str(private), '-pubout', '-out', str(public))
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('AWS_', 'DB_', 'JWT_', 'QUARKUS_', 'SMALLRYE_', 'MP_JWT_'))}
        env.update(DB_USERNAME='yourmenu_local', DB_PASSWORD=secrets.token_urlsafe(24),
                   DB_NAME='yourmenu_local', DB_HOST='127.0.0.1', DB_SSLMODE='disable',
                   JWT_PRIVATE_KEY_LOCATION=private.as_uri(), JWT_PUBLIC_KEY_LOCATION=public.as_uri())
        try:
            container = run('docker', 'run', '--rm', '-d',
                            '--label', 'yourmenu-purpose=modular-local-server',
                            '-e', 'POSTGRES_USER', '-e', 'POSTGRES_PASSWORD', '-e', 'POSTGRES_DB',
                            '-p', '127.0.0.1::5432', 'postgres:16-alpine',
                            env=dict(env, POSTGRES_USER=env['DB_USERNAME'],
                                     POSTGRES_PASSWORD=env['DB_PASSWORD'], POSTGRES_DB=env['DB_NAME']))
            env['DB_PORT'] = run('docker', 'port', container, '5432/tcp').split(':')[-1]
            wait_for_postgres(container, env['DB_USERNAME'], env['DB_NAME'])

            bootstrap = Runtime()
            runtimes.append(bootstrap)
            process, log = start_app('commerce', bootstrap, env, logs, 'drop-and-create')
            processes.append(process); handles.append(log)
            status, _, _ = bootstrap.invoke('GET', '/api/restaurantes/1/categorias')
            if status != 200:
                raise RuntimeError(f'Falha ao preparar esquema: HTTP {status}.')
            process.terminate(); process.wait(timeout=20)
            bootstrap.close(); runtimes.remove(bootstrap)

            commerce = Runtime(); identity = Runtime()
            runtimes.extend((commerce, identity))
            for module, runtime in (('commerce', commerce), ('identity-admin', identity)):
                process, log = start_app(module, runtime, env, logs)
                processes.append(process); handles.append(log)
            if identity.invoke('GET', '/api/auth/test-auth')[0] != 200:
                raise RuntimeError('identity-admin não respondeu ao teste de autenticação.')
            if commerce.invoke('GET', '/api/restaurantes/1/categorias')[0] != 200:
                raise RuntimeError('commerce não respondeu à rota pública.')

            server = gateway(identity, commerce, args.port)
            print(f'API modular pronta em http://127.0.0.1:{args.port}', flush=True)
            print('Banco e chaves são descartáveis. Pressione Ctrl+C para encerrar.', flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
            for runtime in runtimes:
                runtime.close()
            for handle in handles:
                handle.close()
            if container:
                subprocess.run(['docker', 'rm', '-f', container], stdout=subprocess.DEVNULL, check=False)


if __name__ == '__main__':
    main()
