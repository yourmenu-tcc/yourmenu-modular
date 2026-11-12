#!/usr/bin/env python3
"""Deploy the two native YourMenu Lambdas through the installed AWS CLI.

Reads the existing serverless Lambda's DB configuration without printing it.
Creates only resources whose names start with ``yourmenu-modular-``.
Run from any directory with ``python3 scripts/deploy_aws_modular.py --apply``.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
REGION = 'us-east-1'
ACCOUNT = '778081807833'
BASELINE = 'yourmenu-quarkus-serverless'
API_NAME = 'yourmenu-modular-http'
FUNCTIONS = {
    'identity-admin': 'yourmenu-modular-identity-admin',
    'commerce': 'yourmenu-modular-commerce',
}
EXPECTED_ZIP_SHA256 = {
    'identity-admin': '450bd48ff67ec811c82b9c0a9424292c70e3643019f16ae7f32c4efe92cd6654',
    'commerce': 'b834cde3901825996f1e2ad27cab852c7038c1a17fe5ad1513c66b013708670d',
}
KEY_SOURCE = ROOT.parent / 'yourmenu-serverless/src/main/resources'
ROUTES_IDENTITY = (
    'ANY /api/usuarios',
    'ANY /api/usuarios/{proxy+}',
    'ANY /api/restaurante',
    'ANY /api/restaurante/{proxy+}',
    'ANY /api/restaurantes/{restauranteId}/colaboradores',
    'ANY /api/restaurantes/{restauranteId}/colaboradores/{proxy+}',
)


class DeploymentError(RuntimeError):
    pass


def run_aws(*args, secret=None):
    cmd = [shutil.which('aws') or 'aws', *args, '--region', REGION,
           '--output', 'json', '--no-cli-pager']
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        if secret:
            detail = detail.replace(secret, '[REDACTED]')
        raise DeploymentError(f"AWS CLI failed: {' '.join(args[:3])}: {detail}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def describe_existing():
    identity = run_aws('sts', 'get-caller-identity')
    if identity['Account'] != ACCOUNT:
        raise DeploymentError('AWS account differs from the reviewed TCC account')
    current = run_aws('lambda', 'get-function-configuration',
                      '--function-name', BASELINE)
    if current.get('Runtime') != 'provided.al2023':
        raise DeploymentError('Baseline runtime changed; review deployment settings')
    variables = current.get('Environment', {}).get('Variables', {})
    required = ('DB_USERNAME', 'DB_PASSWORD', 'DB_HOST', 'DB_PORT', 'DB_NAME')
    if any(not variables.get(key) for key in required):
        raise DeploymentError('Baseline Lambda lacks one of the required DB variables')
    vpc = current.get('VpcConfig', {})
    if not vpc.get('SubnetIds') or not vpc.get('SecurityGroupIds'):
        raise DeploymentError('Baseline Lambda lacks VPC subnets or security groups')
    rds = run_aws('rds', 'describe-db-instances',
                  '--db-instance-identifier', 'yourmenu-database')
    instance = rds['DBInstances'][0]
    if instance['DBSubnetGroup']['VpcId'] != vpc['VpcId']:
        raise DeploymentError('Baseline Lambda and RDS use different VPCs')
    if instance['DBInstanceStatus'] not in ('available', 'backing-up'):
        raise DeploymentError('RDS is not in a usable state')
    return current['Role'], vpc, variables


def check_artifacts():
    paths = {}
    for module, expected in EXPECTED_ZIP_SHA256.items():
        path = ROOT / 'verification/2026-11-06/artifacts' / f'{module}-native.zip'
        with path.open('rb') as artifact:
            actual = hashlib.file_digest(artifact, 'sha256').hexdigest()
        if actual != expected:
            raise DeploymentError(f'Native ZIP hash changed for {module}')
        with zipfile.ZipFile(path) as archive:
            if archive.namelist() != ['bootstrap'] or archive.testzip():
                raise DeploymentError(f'Invalid native ZIP for {module}')
            if not (archive.getinfo('bootstrap').external_attr >> 16) & 0o111:
                raise DeploymentError(f'bootstrap is not executable in {module}')
        paths[module] = path
    return paths


def ensure_new_names():
    for name in FUNCTIONS.values():
        try:
            run_aws('lambda', 'get-function', '--function-name', name)
        except DeploymentError as exc:
            if 'ResourceNotFoundException' not in str(exc):
                raise
        else:
            raise DeploymentError(f'Function {name} already exists; no overwrite attempted')
    apis = run_aws('apigatewayv2', 'get-apis').get('Items', [])
    if any(api['Name'] == API_NAME for api in apis):
        raise DeploymentError(f'API {API_NAME} already exists; no duplicate attempted')


def check_keys():
    private = KEY_SOURCE / 'privateKey.pem'
    public = KEY_SOURCE / 'publicKey.pem'
    derived = subprocess.run(['openssl', 'pkey', '-in', str(private), '-pubout',
                              '-outform', 'DER'], capture_output=True, check=True).stdout
    supplied = subprocess.run(['openssl', 'pkey', '-pubin', '-in', str(public),
                               '-pubout', '-outform', 'DER'], capture_output=True, check=True).stdout
    if derived != supplied:
        raise DeploymentError('Existing RSA private/public keys do not match')
    return public, private


def make_layer_zip(path, source, archive_name):
    info = zipfile.ZipInfo(f'keys/{archive_name}')
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    path.chmod(0o600)


def publish_layer(name, zip_path):
    result = run_aws('lambda', 'publish-layer-version', '--layer-name', name,
                     '--zip-file', f'fileb://{zip_path}', '--compatible-runtimes',
                     'provided.al2023', '--compatible-architectures', 'x86_64')
    print(f'Layer created: {name} version {result["Version"]}', flush=True)
    return result['LayerVersionArn']


def create_function(name, zip_path, role, vpc, layers):
    config = {'SubnetIds': vpc['SubnetIds'],
              'SecurityGroupIds': vpc['SecurityGroupIds']}
    result = run_aws('lambda', 'create-function', '--function-name', name,
                     '--runtime', 'provided.al2023', '--handler', 'bootstrap',
                     '--architectures', 'x86_64', '--role', role,
                     '--zip-file', f'fileb://{zip_path}', '--memory-size', '1024',
                     '--timeout', '10', '--vpc-config', json.dumps(config),
                     '--layers', *layers)
    print(f'Function created: {name}', flush=True)
    run_aws('lambda', 'wait', 'function-active-v2', '--function-name', name)
    return result['FunctionArn']


def configure_environment(name, original, include_private_key, temp_dir):
    variables = {key: original[key] for key in
                 ('DB_USERNAME', 'DB_PASSWORD', 'DB_HOST', 'DB_PORT', 'DB_NAME')}
    variables.update(DB_SSLMODE=original.get('DB_SSLMODE', 'require'),
                     DISABLE_SIGNAL_HANDLERS='true',
                     JWT_PUBLIC_KEY_LOCATION='file:///opt/keys/publicKey.pem')
    if include_private_key:
        variables['JWT_PRIVATE_KEY_LOCATION'] = 'file:///opt/keys/privateKey.pem'
    document = temp_dir / f'{name}-environment.json'
    document.write_text(json.dumps({'FunctionName': name,
                                    'Environment': {'Variables': variables}}))
    document.chmod(0o600)
    try:
        run_aws('lambda', 'update-function-configuration', '--cli-input-json',
                f'file://{document}', '--query',
                '{FunctionName:FunctionName,LastUpdateStatus:LastUpdateStatus}',
                secret=variables['DB_PASSWORD'])
        run_aws('lambda', 'wait', 'function-updated-v2', '--function-name', name)
    finally:
        document.unlink(missing_ok=True)
    print(f'Environment configured: {name}', flush=True)


def create_http_api(arns):
    cors = {'AllowOrigins': ['*'],
            'AllowMethods': ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
            'AllowHeaders': ['content-type', 'authorization', 'x-restaurante-id']}
    api = run_aws('apigatewayv2', 'create-api', '--name', API_NAME,
                  '--protocol-type', 'HTTP', '--cors-configuration', json.dumps(cors))
    api_id = api['ApiId']
    print(f'HTTP API created: {api_id}', flush=True)
    integrations = {}
    for module, arn in arns.items():
        response = run_aws('apigatewayv2', 'create-integration', '--api-id', api_id,
                           '--integration-type', 'AWS_PROXY', '--integration-uri', arn,
                           '--payload-format-version', '2.0')
        integrations[module] = response['IntegrationId']
    route_definitions = [('$default', 'commerce', 'NONE')]
    route_definitions += [(route, 'identity-admin', 'NONE')
                          for route in ROUTES_IDENTITY]
    route_definitions += [
        ('ANY /api/auth/{proxy+}', 'identity-admin', 'AWS_IAM'),
        ('POST /api/auth/registrar', 'identity-admin', 'AWS_IAM'),
        ('POST /api/auth/login', 'identity-admin', 'NONE'),
        ('GET /api/auth/test-auth', 'identity-admin', 'NONE'),
    ]
    for route, module, authorization in route_definitions:
        run_aws('apigatewayv2', 'create-route', '--api-id', api_id,
                '--route-key', route, '--target', f'integrations/{integrations[module]}',
                '--authorization-type', authorization)
    for module, name in FUNCTIONS.items():
        run_aws('lambda', 'add-permission', '--function-name', name,
                '--statement-id', f'allow-{API_NAME}-{api_id}',
                '--action', 'lambda:InvokeFunction',
                '--principal', 'apigateway.amazonaws.com',
                '--source-arn',
                f'arn:aws:execute-api:{REGION}:{ACCOUNT}:{api_id}/*/*')
    run_aws('apigatewayv2', 'create-stage', '--api-id', api_id,
            '--stage-name', '$default', '--auto-deploy')
    print(f'HTTP API ready: {api["ApiEndpoint"]}', flush=True)
    return {'api_id': api_id, 'url': api['ApiEndpoint']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true',
                        help='create AWS resources after all local and cloud preflight checks')
    args = parser.parse_args()
    paths = check_artifacts()
    public, private = check_keys()
    role, vpc, variables = describe_existing()
    ensure_new_names()
    print('Preflight OK: native ZIPs, key pair, AWS identity, DB settings, VPC and names',
          flush=True)
    if not args.apply:
        print('Dry run complete; pass --apply to create resources', flush=True)
        return
    with tempfile.TemporaryDirectory(prefix='yourmenu-modular-deploy-') as dirname:
        tmp = Path(dirname)
        tmp.chmod(0o700)
        public_zip, private_zip = tmp/'public-layer.zip', tmp/'private-layer.zip'
        make_layer_zip(public_zip, public, 'publicKey.pem')
        make_layer_zip(private_zip, private, 'privateKey.pem')
        public_layer = publish_layer('yourmenu-modular-jwt-public', public_zip)
        private_layer = publish_layer('yourmenu-modular-jwt-private', private_zip)
        arns = {}
        for module, name in FUNCTIONS.items():
            layers = [public_layer, private_layer] if module == 'identity-admin' else [public_layer]
            arns[module] = create_function(name, paths[module], role, vpc, layers)
            configure_environment(name, variables, module == 'identity-admin', tmp)
        api = create_http_api(arns)
    state = {'region': REGION, 'account': ACCOUNT,
             'functions': arns, 'public_layer': public_layer,
             'private_layer': private_layer, **api}
    output = ROOT / 'verification/2026-11-06/aws-deployment-state.json'
    output.write_text(json.dumps(state, indent=2) + '\n')
    print(f'Deployment state saved: {output}', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (DeploymentError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from None
