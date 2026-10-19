"""CASE-001: recheck the fixed import without rerunning the destructive importer.

The six migrated entry files are intentionally owned by PS-00-02/04. Their
behavior is covered by those task routes; the other 389 imported blobs must
still match the original Git objects exactly. No original RimCrow path is read.
"""
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / 'docs/development/evidence/PS-00-01/20260928T032245703563Z'
COMMIT = 'a7e875112a175a914bb4706a82589d96d62adc70'
MIGRATED_ENTRIES = {
    'main.py', 'frontend/index.html', 'frontend/package.json',
    'frontend/vite.config.js', 'frontend/src/app/App.vue',
    'frontend/src/app/main.js',
}


# The project's own copyright line is the only addition to the upstream MIT
# text; the upstream notice and license wording stay byte-for-byte.
PROJECT_COPYRIGHT = b'Copyright (c) 2026 PSMM contributors\n'


def upstream_license(content):
    assert content.count(PROJECT_COPYRIGHT) == 1
    return content.replace(PROJECT_COPYRIGHT, b'', 1)


def load(name):
    return json.loads((ARCHIVE / name).read_text(encoding='utf-8'))


def test_sealed_baseline_evidence_has_valid_bytes_and_actual_counts():
    records = (ARCHIVE / 'artifacts.sha256').read_text(encoding='utf-8').splitlines()
    assert len(records) >= 20
    for line in records:
        digest, relative = line.split('  ', 1)
        path = (ARCHIVE / relative).resolve()
        assert path.is_relative_to(ARCHIVE)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, relative
    report = load('summary.json')
    assert report['reference_commit'] == COMMIT
    assert report['exit_code'] == 0 and report['verification_status'] == 'PASS'
    assert report['executed'] == report['passed'] == 4
    assert report['failed'] == report['skipped'] == 0
    before, after = load('source-before.json'), load('source-after.json')
    assert before == after
    assert after['head'] == COMMIT and len(after['files']) == 410


def test_imported_local_blobs_remain_traceable_to_fixed_git_objects():
    manifest = load('import-manifest.json')
    assert len(manifest) == len({item['path'] for item in manifest}) == 395
    migrated = set()
    retained = 0
    for item in manifest:
        assert item['source_sha256'] == item['destination_sha256']
        assert item['disposition'] and item['owner'] and item['reason']
        path = (ROOT / item['path']).resolve()
        assert path.is_relative_to(ROOT)
        content = path.read_bytes()
        if item['path'] == 'LICENSE':
            content = upstream_license(content)
        if item['path'] in MIGRATED_ENTRIES:
            migrated.add(item['path'])
            assert content, item['path']
            continue
        assert len(content) == item['bytes'], item['path']
        assert hashlib.sha256(content).hexdigest() == item['source_sha256'], item['path']
        git_object = f'blob {len(content)}\0'.encode('ascii') + content
        assert hashlib.sha1(git_object).hexdigest() == item['git_blob'], item['path']
        retained += 1
    assert migrated == MIGRATED_ENTRIES and retained == 389


def test_license_and_dependency_provenance_are_explicit_not_release_approval():
    sources = load('third-party-sources.json')
    upstream = sources['upstream']
    assert upstream['license'] == 'MIT'
    assert hashlib.sha256(upstream_license((ROOT / upstream['path']).read_bytes())).hexdigest() == upstream['sha256']
    assert upstream['copyright'].encode() in (ROOT / upstream['path']).read_bytes()
    assert sources['python_packages']
    assert all(item['name'] and item['version'] and item['source']
               and item['license_status'].startswith('NOT_AUDITED')
               for item in sources['python_packages'])
