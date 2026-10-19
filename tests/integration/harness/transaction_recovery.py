"""Owned CASE-029..033 fixture and killable real product RPC consumer.

No historical generation builder is used: every generation comes from apply.
The only substituted user input is the native picker/confirmation receipt.
Faults are constructor hooks; no request or environment flag enables them.
"""
from contextlib import contextmanager
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from tests.unit.test_installation_layout import make_installation

ROOTS = {'mods': 'Mods', 'plugins': 'Plugins', 'user_libs': 'UserLibs'}
FAULT_POINTS = (
    'transaction.created', 'journal.header', 'backup.write', 'backup.flush', 'backup.verified',
    'stage.write', 'stage.flush', 'stage.verified', 'transaction.ready', 'transaction.applying',
    'operation.intent', 'operation.evacuated', 'operation.published', 'operation.verified',
    'operation.done', 'targets.verified', 'manifest.prepared', 'commit.intent',
    'manifest.published', 'db.before_commit', 'db.committed', 'journal.committed',
    'cleanup.before', 'cleanup.after',
)


def request(command, payload=None):
    return {'schema_version': 1, 'request_id': str(uuid4()), 'command': command, 'payload': payload or {}}


def finish(api, snapshot, timeout=45):
    deadline = time.monotonic() + timeout
    while snapshot['state'] not in {'succeeded', 'failed', 'cancelled'}:
        if time.monotonic() >= deadline:
            raise AssertionError('Product task exceeded bounded fixture wait')
        time.sleep(.01)
        response = api.dispatch(request('task.snapshot', {'task_id': snapshot['task_id']}))
        assert response['ok'], response
        snapshot = response['data']
    return snapshot


def rpc(api, command, payload=None):
    response = api.dispatch(request(command, payload))
    assert response['ok'], response
    data = response['data']
    if isinstance(data, dict) and 'task_id' in data:
        terminal = finish(api, data)
        assert terminal['state'] == 'succeeded', (command, terminal)
        return terminal['data']
    return data


def tree(root):
    return {p.relative_to(root).as_posix() + ('/' if p.is_dir() else ''):
            '<directory>' if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob('*')) if p.is_dir() or p.is_file()}


def candidates(game):
    """Enumerate every known loader/framework scan-root path without execution."""
    suffixes = {'.dll', '.png', '.jpg', '.ogg', '.wav', '.bundle', '.json'}
    return {p.relative_to(game).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for name in ROOTS.values() for p in (game / name).rglob('*')
            if p.is_file() and p.suffix.lower() in suffixes}


def durable_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def database_snapshot(path):
    with sqlite3.connect(Path(path).as_uri()+'?mode=ro', uri=True, timeout=2) as db:
        return {'quick_check': db.execute('PRAGMA quick_check').fetchall(),
                'foreign_key_check': db.execute('PRAGMA foreign_key_check').fetchall(),
                'entities': {name: [json.loads(row[0]) for row in db.execute('SELECT payload FROM '+name+' ORDER BY rowid')]
                             for name in ('transactions', 'deployment_generations', 'action_plans', 'approvals')}}


def process_identity(process):
    """Read identity through this Popen's live OS handle, never a reused PID."""
    import win32process
    buffer = ctypes.create_unicode_buffer(32768)
    size = ctypes.c_ulong(len(buffer))
    query = ctypes.windll.kernel32.QueryFullProcessImageNameW
    query.argtypes = [ctypes.c_void_p,ctypes.c_ulong,ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_ulong)]
    assert query(int(process._handle),0,buffer,ctypes.byref(size))
    return {'executable':buffer.value,
            'creation_utc':win32process.GetProcessTimes(int(process._handle))['CreationTime'].isoformat()}


class TransactionFixture:
    def __init__(self, root, *, game_root=None):
        self.root = Path(root)
        self.game = make_installation(Path(game_root) if game_root is not None else self.root / 'synthetic-game')
        for name in (*ROOTS.values(), 'UserData'):
            (self.game / name).mkdir()
        (self.game / 'UserData/save-sentinel.bin').write_bytes(b'isolated-save-never-deployed')
        (self.game / 'Mods/foreign.json').write_bytes(b'{"unknown":"keep"}')
        self.data = self.root / 'private-data'
        durable_json(self.root / '.case-owner.json', {'owner': 'PSModManager.CASE029-033.owned.v1',
            'root': str(self.root.resolve()), 'synthetic': True})
        self.context = self.open()
        self.context.choose_installation_folder = lambda _: str(self.game)
        grant = self.call('installation.choose', {'kind':'manual'})
        found = self.call('installation.discover', {'source_handles':[grant['source_handle']]})
        candidate = found['candidates'][0]
        assert candidate['valid'] and candidate['exe_relative_path'], candidate
        registered = self.call('installation.select', {'candidate_id':candidate['candidate_id']})
        self.installation = registered['installation']['installation_id']
        self.counter = 0
        self.sentinels = {key:value for key,value in tree(self.game).items()
                          if not key.startswith(('Mods/', 'Plugins/', 'UserLibs/'))}

    def open(self, fault=None):
        context = DesktopContext(resolve_paths(str(self.data)))
        if fault is not None:
            context.deployment_fault = fault
        context.initialize()
        context.api = DesktopApi(context)
        return context

    def close(self):
        if self.context is not None:
            self.context.close()
            self.context = None

    def reopen(self, fault=None):
        self.close()
        self.context = self.open(fault)
        return self.context

    def call(self, command, payload=None):
        return rpc(self.context.api, command, payload)

    def publish(self, files, name=None):
        self.counter += 1
        source = self.root / ('input-'+str(self.counter))
        source.mkdir()
        for relative, content in files.items():
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self.context.choose_import_source = lambda _: str(source)
        grant = self.call('package.choose', {'kind':'directory'})
        preview = self.call('package.import_preview', {'source_handle':grant['source_handle']})
        assert preview.get('can_commit'), preview
        release = self.call('package.import_commit', {'preview_id':preview['preview_id'],
            'preview_hash':preview['preview_hash'], 'display_name':name or 'Package '+str(self.counter), 'version_raw':'1.0'})
        assert 'release_id' in release and not release['deployed'], release
        return release

    def profile(self, releases=()):
        state = self.call('profile.create', {'installation_id':self.installation,'name':'Real transaction fixture'})
        for release in releases:
            state = self.select(state, release)
        return self.save(state)

    def select(self, state, release, selected=True):
        return self.call('profile.edit', {'profile_id':state['profile_id'], 'expected_revision':state['revision'],
            'action':{'op':'set_selection','selection':{'release_id':release['release_id'],
                'manifest_hash':release['manifest_hash'],'selected':selected}}})

    def save(self, state):
        saved = self.call('profile.commit', {'profile_id':state['profile_id'],'expected_revision':state['revision']})
        assert not saved.get('error_code'), saved
        return self.call('profile.get', {'profile_id':state['profile_id']})

    def approved(self, state):
        preview = self.call('plan.create', {'profile_id':state['profile_id'],
            'expected_revision':state['profile_revision'],'idempotency_key':str(uuid4())})
        assert 'plan' in preview, preview
        identity = {key:preview['plan'][key] for key in ('plan_id','plan_hash')}
        self.context.confirm_plan = lambda _: True
        approval = self.call('plan.approve', identity)
        assert not approval.get('error_code'), approval
        self.context.confirm_plan = lambda _: False
        return preview, dict(identity, approval_id=(approval.get('approval') or {}).get('approval_id'))

    def status(self, plan_id=None):
        payload = {'installation_id':self.installation}
        if plan_id is not None:
            payload['plan_id'] = plan_id
        return self.call('deployment.status', payload)

    def mixed(self):
        old = {'Mods/replace.png':b'old-replace','Mods/remove.png':b'old-remove','Mods/shared.png':b'shared'}
        for name,content in old.items():
            (self.game / name).write_bytes(content)
        self.call('inventory.scan', {'installation_id':self.installation,'force':True})
        adoption = self.call('package.adopt_preview', {'installation_id':self.installation})
        adopted = self.call('package.adopt', {'snapshot_id':adoption['snapshot_id'],
            'manifest_hash':adoption['manifest_hash'],'paths':list(old),'display_name':'Explicit owned originals'})
        assert not adopted.get('error_code'), adopted
        newer = {'Mods/replace.png':b'new-replace','Mods/shared.png':b'shared','UserLibs/add.png':b'new-add'}
        state = self.profile([self.publish(newer)])
        preview,payload = self.approved(state)
        before,after = tree(self.game),tree(self.game)
        after.pop('Mods/remove.png')
        for name,content in newer.items():
            after[name] = hashlib.sha256(content).hexdigest()
        return state,preview,payload,before,after

    def assert_sentinels(self):
        current = tree(self.game)
        assert {key:value for key,value in current.items() if key in self.sentinels}==self.sentinels
        assert (self.game / 'Mods/foreign.json').read_bytes()==b'{"unknown":"keep"}'

    def evidence(self, label):
        value = {'game':tree(self.game), 'candidates':candidates(self.game),
                 'database':database_snapshot(self.data / 'psmodmanager.sqlite3')}
        if self.context is not None:
            value['status'] = self.status()
        durable_json(self.root / (label+'.json'), value)
        return value


@contextmanager
def child_at(app, payload, point, *, mode='apply', restart_fault=None):
    app.close()
    token = uuid4().hex
    instruction,marker,release = (app.root / (token+suffix) for suffix in ('.instruction.json','.boundary.json','.release'))
    durable_json(instruction, {'payload':payload,'point':point,'marker':str(marker),'release':str(release)})
    # Windows venv python.exe is a redirector whose Popen handle may identify
    # only a launcher. Use the same installed base interpreter directly and
    # activate existing venv .pth files so the exact owned handle is killable.
    base_python = Path(sys._base_executable).resolve(strict=True)
    site_packages = Path(sys.prefix) / 'Lib/site-packages'
    assert site_packages.is_dir()
    bootstrap = 'import runpy,site,sys;site.addsitedir(sys.argv[1]);sys.argv=sys.argv[2:];runpy.run_path(sys.argv[0],run_name="__main__")'
    command = [str(base_python),'-X','utf8','-c',bootstrap,str(site_packages),str(Path(__file__).resolve()),
        '--case-root',str(app.root),'--instruction',str(instruction),'--mode',mode]
    process = subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW)
    identity = process_identity(process)
    assert Path(identity['executable']).resolve()==base_python
    try:
        deadline = time.monotonic()+45
        while not marker.exists() or not marker.stat().st_size:
            if process.poll() is not None:
                stdout,stderr = process.communicate()
                durable_json(app.root / (token+'.early-exit.json'), {'command':command,'exit_code':process.returncode,
                    'stdout':stdout,'stderr':stderr})
                raise AssertionError('Owned product child exited before '+point+': '+stderr[-2000:]+stdout[-1000:])
            if time.monotonic()>=deadline:
                raise AssertionError('Owned product child did not reach '+point)
            time.sleep(.02)
        observed = json.loads(marker.read_text(encoding='utf-8'))
        assert observed['point']==point and observed['pid']==process.pid,{'marker':observed['pid'],'popen':process.pid}
        assert process_identity(process)==identity
        yield process,observed
    finally:
        if process.poll() is None:
            process.kill()  # Only this exact owned Popen handle, never PID/name lookup.
        stdout,stderr = process.communicate(timeout=10)
        assert process.poll() is not None
        durable_json(app.root / (token+'.process.json'), {'command':command,'point':point,'pid':process.pid,
            'process_identity':identity,'boundary_pid':observed['pid'] if 'observed' in locals() else None,
            'exit_code':process.returncode,'stdout':stdout,'stderr':stderr,
            'remaining_processes':0,'remaining_scope':'exact verified direct-base Popen handle after wait/communicate'})
        app.context = app.open(restart_fault)


def child_main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case-root',required=True)
    parser.add_argument('--instruction',required=True)
    parser.add_argument('--mode',choices=('apply','recover'),default='apply')
    args = parser.parse_args()
    root = Path(args.case_root).resolve(strict=True)
    owner = json.loads((root / '.case-owner.json').read_text(encoding='utf-8'))
    assert owner=={'owner':'PSModManager.CASE029-033.owned.v1','root':str(root),'synthetic':True}
    instruction = Path(args.instruction).resolve(strict=True)
    assert instruction.parent==root
    inputs = json.loads(instruction.read_text(encoding='utf-8'))
    marker,release = Path(inputs['marker']),Path(inputs['release'])
    assert marker.parent.resolve()==root and release.parent.resolve()==root
    fired = False
    def fault(point,details):
        nonlocal fired
        if fired or point!=inputs['point']:
            return
        fired = True
        durable_json(marker, {'point':point,'details':details,'pid':os.getpid(),
            'game':tree(root / 'synthetic-game'),'candidates':candidates(root / 'synthetic-game'),
            'database':database_snapshot(root / 'private-data/psmodmanager.sqlite3')})
        deadline = time.monotonic()+120
        while not release.exists() and time.monotonic()<deadline:
            time.sleep(.02)
        if not release.exists():
            raise TimeoutError('Owned fault pause expired')
    context = DesktopContext(resolve_paths(str(root / 'private-data')))
    context.deployment_fault = fault
    try:
        context.initialize()
        context.api = DesktopApi(context)
        command = 'plan.apply' if args.mode=='apply' else 'deployment.recover'
        result = rpc(context.api,command,inputs['payload'])
        print(json.dumps({'result':result,'reached_boundary':fired}))
        return 0 if fired else 2
    finally:
        context.close()


if __name__=='__main__':
    raise SystemExit(child_main())
