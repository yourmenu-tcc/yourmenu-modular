#!/usr/bin/env python3
"""Verify source equivalence and unique ownership against the preserved Lambda baseline."""
from collections import Counter
import json
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1]
baseline = root.parent / 'yourmenu-serverless/src/main/java/tech/miqueias/bento/io'
manifest = json.loads((root/'class-distribution.json').read_text())
assert set(manifest) == {str(p.relative_to(baseline)) for p in baseline.rglob('*.java')}
found = Counter()
for module in ['common', 'identity-admin', 'commerce']:
    source = root/module/'src/main/java/tech/miqueias/bento/io'
    for file in source.rglob('*.java'):
        relative = str(file.relative_to(source))
        found[relative] += 1
        assert manifest[relative] == module, relative
        assert file.read_bytes() == (baseline/relative).read_bytes(), relative
    assert not list((root/module/'src').rglob('*.pem')), 'Key material must be supplied externally'
    config = root/module/'src/main/resources/application.properties'
    if config.exists():
        text = config.read_text()
        assert '%prod.quarkus.hibernate-orm.schema-management.strategy=none' in text
        assert '${DB_PASSWORD}' in text
    artifact = root/module/'target/function.zip'
    if artifact.exists():
        with zipfile.ZipFile(artifact) as z:
            assert not any(name.endswith('.pem') for name in z.namelist())
assert found == Counter({name:1 for name in manifest})
print(f'PASS: {len(found)} original Java files, byte-identical, each in exactly one module.')
print('Distribution:', dict(Counter(manifest.values())))
print('PASS: external keys/credentials; automatic production schema changes disabled.')
