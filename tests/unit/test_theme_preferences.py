"""Real private config persistence through the closed desktop command table."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths


def request(api, command, payload=None):
    return api.dispatch({'schema_version': 1, 'request_id': 'theme-test',
                         'command': command, 'payload': payload or {}})


@pytest.fixture
def desktop(tmp_path):
    context = DesktopContext(resolve_paths(str(tmp_path / 'private-data')))
    context.initialize()
    try:
        yield context, DesktopApi(context)
    finally:
        context.close()


def test_theme_is_persisted_and_read_by_a_fresh_desktop_process(desktop):
    context, api = desktop
    assert request(api, 'app.theme.get')['data'] == {'theme_id': 'obsidian-cyan'}
    assert request(api, 'app.theme.set', {'theme_id': 'win11-mica'})['data'] == {'theme_id': 'win11-mica'}
    assert json.loads(context.paths.config.read_text(encoding='utf-8'))['theme_id'] == 'win11-mica'
    code = '''
import json, sys
sys.path.insert(0, sys.argv[1])
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from backend.app.api import DesktopApi
c = DesktopContext(resolve_paths(sys.argv[2]))
c.initialize()
try:
    print(json.dumps(DesktopApi(c).dispatch(dict(schema_version=1, request_id='reopen', command='app.theme.get', payload={}))))
finally:
    c.close()
'''
    process = subprocess.run([sys.executable, '-I', '-B', '-c', code,
        str(Path(__file__).resolve().parents[2]), str(context.paths.root)], capture_output=True, text=True, timeout=20)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)['data']['theme_id'] == 'win11-mica'


def test_theme_update_preserves_identity_and_unknown_settings(desktop):
    context, api = desktop
    old = json.loads(context.paths.config.read_text(encoding='utf-8'))
    old['future_preference'] = {'layout': '留存', 'size': 17}
    context.paths.config.write_text(json.dumps(old, ensure_ascii=False), encoding='utf-8')
    assert request(api, 'app.theme.set', {'theme_id': 'graphite-ash'})['ok']
    updated = json.loads(context.paths.config.read_text(encoding='utf-8'))
    assert updated == old | {'theme_id': 'graphite-ash'}
    assert not list(context.paths.root.glob('.config-*.tmp'))


@pytest.mark.parametrize('payload', [{'theme_id': 'external'}, {'theme_id': '../theme'},
    {'theme_id': 1}, {'theme_id': 'win11-mica', 'path': 'outside'}, {}])
def test_theme_command_rejects_unknown_values_and_extra_fields(desktop, payload):
    context, api = desktop
    before = context.paths.config.read_bytes()
    response = request(api, 'app.theme.set', payload)
    assert not response['ok'] and response['error']['code'] == 'INVALID_REQUEST'
    assert context.paths.config.read_bytes() == before


def test_theme_get_does_not_accept_a_path(desktop):
    _, api = desktop
    assert request(api, 'app.theme.get', {'path': 'outside'})['error']['code'] == 'INVALID_REQUEST'


def test_theme_rejects_missing_identity_field_without_repairing_config(desktop):
    context, api = desktop
    config = json.loads(context.paths.config.read_text(encoding='utf-8'))
    del config['update_source']
    context.paths.config.write_text(json.dumps(config), encoding='utf-8')
    before = context.paths.config.read_bytes()
    assert not request(api, 'app.theme.get')['ok']
    assert not request(api, 'app.theme.set', {'theme_id': 'win11-mica'})['ok']
    assert context.paths.config.read_bytes() == before


def test_failed_atomic_replace_keeps_previous_theme(desktop, monkeypatch):
    context, api = desktop
    assert request(api, 'app.theme.set', {'theme_id': 'win11-mica'})['ok']
    before = context.paths.config.read_bytes()
    def fail(source, destination):
        assert destination == context.paths.config
        raise OSError('owned injected write failure')
    monkeypatch.setattr('backend.app.preferences.os.replace', fail)
    assert request(api, 'app.theme.set', {'theme_id': 'graphite-ash'})['error']['code'] == 'INTERNAL_ERROR'
    assert context.paths.config.read_bytes() == before
    assert not list(context.paths.root.glob('.config-*.tmp'))


def test_theme_write_rejects_config_hardlink(desktop, tmp_path):
    context, api = desktop
    original = context.paths.config.read_bytes()
    sentinel = tmp_path / 'keep-config.json'
    sentinel.write_bytes(original)
    context.paths.config.unlink()
    os.link(sentinel, context.paths.config)
    assert not request(api, 'app.theme.set', {'theme_id': 'win11-mica'})['ok']
    assert sentinel.read_bytes() == original


def test_theme_write_rejects_changed_product_identity(desktop):
    context, api = desktop
    config = json.loads(context.paths.config.read_text(encoding='utf-8'))
    config['product'] = 'Other product'
    context.paths.config.write_text(json.dumps(config), encoding='utf-8')
    before = context.paths.config.read_bytes()
    assert not request(api, 'app.theme.set', {'theme_id': 'win11-mica'})['ok']
    assert context.paths.config.read_bytes() == before
