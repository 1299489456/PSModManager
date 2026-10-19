import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import zipfile

from backend.app.native_drop import _BIND_SCRIPT, _grant_objects, install_native_drop


class File:
    def __init__(self, path):
        self.Path = path


class Grants:
    def __init__(self):
        self.calls = []

    def grant(self, *args):
        self.calls.append(args)
        return dict(source_handle='opaque-handle', display_name=Path(args[-1]).name)


def context(tmp_path):
    return SimpleNamespace(paths=SimpleNamespace(root=tmp_path/'private-data'), imports=Grants(), profile_sharing=Grants())


def test_native_types_are_required_and_javascript_created_file_is_refused(tmp_path):
    ctx = context(tmp_path)
    result = _grant_objects(ctx, [dict(Path=str(tmp_path/'fake.dll')), File('')], File)
    assert result['sources'] == []
    assert [error['code'] for error in result['errors']] == ['IMPORT_DROP_INVALID'] * 2
    assert ctx.imports.calls == []
    assert ctx.profile_sharing.calls == []


def test_relative_and_network_paths_are_refused_before_source_inspection(tmp_path):
    ctx = context(tmp_path)
    result = _grant_objects(ctx, [File('relative.dll'), File(r'\\server\share\mod.dll')], File)
    assert result['sources'] == []
    assert [error['code'] for error in result['errors']] == ['IMPORT_PATH_INVALID'] * 2
    assert ctx.imports.calls == ctx.profile_sharing.calls == []


def test_drop_grants_only_bounded_unique_paths_and_never_deploys(tmp_path):
    ctx = context(tmp_path)
    dll = tmp_path/'mod.dll'
    dll.write_bytes(b'synthetic-only')
    directory = tmp_path/'resource-mod'
    directory.mkdir()
    result = _grant_objects(ctx, [File(str(dll)), File(str(dll)), File(str(directory))], File)
    assert [source['kind'] for source in result['sources']] == ['dll', 'directory']
    assert ctx.imports.calls == [('dll', str(dll)), ('directory', str(directory))]
    assert all(set(source) == {'source_handle', 'display_name', 'kind'} for source in result['sources'])
    assert result['errors'] == []
    assert _grant_objects(ctx, (File(str(dll)) for _ in range(10000)), File) == dict(
        sources=[], errors=[dict(code='IMPORT_CAPACITY', display_name='')])
    assert len(ctx.imports.calls) == 2


def test_zip_manifest_routes_to_sharing_and_plain_zip_keeps_import_flow(tmp_path):
    ctx = context(tmp_path)
    bundle, plain = tmp_path/'bundle.zip', tmp_path/'mod.zip'
    for path, name in ((bundle, 'modpack.json'), (plain, 'Mods/example.dll')):
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(name, '{}')
    result = _grant_objects(ctx, [File(str(bundle)), File(str(plain))], File)
    assert result['errors'] == []
    assert [source['kind'] for source in result['sources']] == ['modpack', 'zip']
    assert ctx.profile_sharing.calls == [(str(bundle),)]
    assert ctx.imports.calls == [('zip', str(plain))]


def test_broken_or_protected_zip_is_not_granted_and_does_not_expose_paths(tmp_path):
    ctx = context(tmp_path)
    protected = ctx.paths.root/'private.zip'
    protected.parent.mkdir()
    protected.write_bytes(b'never-inspect')
    broken = tmp_path/'broken.zip'
    broken.write_bytes(b'not-a-zip')
    result = _grant_objects(ctx, [File(str(protected)), File(str(broken)), File(str(tmp_path/'bad.exe'))], File)
    assert result['sources'] == []
    assert [error['code'] for error in result['errors']] == [
        'IMPORT_SOURCE_PROTECTED_LIBRARY', 'IMPORT_ARCHIVE_INVALID', 'IMPORT_FORMAT_UNSUPPORTED']
    assert str(tmp_path) not in json.dumps(result)
    assert ctx.imports.calls == ctx.profile_sharing.calls == []


def test_javascript_only_posts_real_drop_files_and_preserves_normal_drags():
    script = r"""
const assert = require('node:assert/strict');
const listeners = {}, hostListeners = {}, messages = [], dispatched = [];
global.window = {chrome: {webview: {
  addEventListener: (name, callback) => hostListeners[name] = callback,
  postMessageWithAdditionalObjects: (...args) => messages.push(args)
}}, dispatchEvent: event => dispatched.push(event)};
global.document = {addEventListener: (name, callback) => listeners[name] = callback};
global.CustomEvent = function(name, options) {this.type=name;this.detail=options.detail};
""" + _BIND_SCRIPT + r""";
let prevented=0;
listeners.drop({isTrusted:false, dataTransfer:{types:['Files'], files:['forged']}, preventDefault(){prevented++}});
assert.equal(messages.length,0);
listeners.drop({isTrusted:true, dataTransfer:{types:['text/plain']}, preventDefault(){prevented++}});
assert.equal(prevented,1);
const files = [{osFile:true}];
listeners.drop({isTrusted:true, dataTransfer:{types:['Files'],files}, preventDefault(){prevented++}});
assert.deepEqual(messages,[['FilesDropped',files]]);
hostListeners.message({data:{type:'other',detail:{}}});
assert.equal(dispatched.length,0);
hostListeners.message({data:{type:'psmm-native-import',detail:{sources:['opaque']}}});
assert.deepEqual(dispatched[0].detail,{sources:['opaque']});
"""
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_install_is_idempotent_without_touching_picker_or_rpc(tmp_path):
    class Event:
        def __init__(self): self.callbacks = []
        def __iadd__(self, callback):
            self.callbacks.append(callback)
            return self
    window = SimpleNamespace(events=SimpleNamespace(before_show=Event(), loaded=Event()))
    ctx = context(tmp_path)
    install_native_drop(window, ctx)
    install_native_drop(window, ctx)
    assert len(window.events.before_show.callbacks) == len(window.events.loaded.callbacks) == 1
    assert not hasattr(window, 'expose')
