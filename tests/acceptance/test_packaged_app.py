"""CASE-047/048: delivered EXE, real standard-permission UI and owned files."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(path):
    return {p.relative_to(path).as_posix():dict(size=p.stat().st_size,sha256=sha(p))
            for p in path.rglob('*') if p.is_file()}


@pytest.fixture(scope='module')
def product():
    if os.name!='nt' or not os.environ.get('PSMM_PACKAGED_ROOT'):
        pytest.skip('Select the actual extracted Windows artifact with PSMM_PACKAGED_ROOT')
    bundle=Path(os.environ['PSMM_PACKAGED_ROOT']).resolve(strict=True)
    run=Path(os.environ['PSMM_PACKAGED_EVIDENCE_ROOT']).resolve(strict=True)
    assert run.is_relative_to(ROOT/'docs/development/evidence/PS-05-03')
    assert bundle.is_relative_to(ROOT/'.preview') and (bundle/'PSModManager.exe').is_file()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out=run/('acceptance-'+stamp);out.mkdir()
    from tests.unit.test_installation_layout import make_installation
    from tests.integration.test_content_catalog import compile_fixture
    from backend.storage.migrations import open_database
    from backend.app.preferences import IDENTITY
    game=make_installation(out/'合成 游戏')
    for folder in ('Mods','Plugins','UserLibs','UserData'):
        (game/folder).mkdir()
    (game/'UserData/owned-save-marker.txt').write_text('synthetic save; preserve',encoding='utf-8')
    dll=compile_fixture(out/'compiler','PackagedAcceptanceMod')
    first=out/'first';first.mkdir()
    data=first/'local/PSModManager';data.mkdir(parents=True)
    # Existing contract fixture + existing schema6 creator, not a fake version
    # number on a current database. The actual frozen startup performs migration.
    legacy=read(ROOT/'tests/fixtures/contracts/valid.json')['RuntimeFingerprint']
    legacy_payload=json.dumps(legacy)
    db=open_database(data/'psmodmanager.sqlite3',target_version=6)
    db.execute('INSERT INTO runtime_fingerprints (fingerprint_id,payload) VALUES (?,?)',
               (legacy['fingerprint_id'],legacy_payload));db.close()
    config=dict(IDENTITY,theme_id='graphite-ash',retained={'marker':'PS-05-03-existing-setting'})
    write(data/'config.json',config)
    fixture=dict(game=str(game),dll=str(dll),target=str(game/'Mods'/dll.name),dll_sha256=sha(dll),
                 game_before=tree(game),legacy_id=legacy['fingerprint_id'],legacy_payload=legacy_payload,
                 legacy_database_sha256=sha(data/'psmodmanager.sqlite3'),config=config,
                 config_sha256=sha(data/'config.json'))
    write(out/'fixture.json',fixture)
    value=dict(bundle=bundle,run=run,out=out,first=first,data=data,fixture=fixture,package_before=tree(bundle))
    yield value
    write(out/'test-inputs.json',[dict(path=name,sha256=sha(ROOT/name)) for name in [
        'tests/acceptance/test_packaged_app.py','tests/acceptance/harness/packaged_app.py',
        'tests/acceptance/harness/standard_user.py','tests/desktop/harness/portable_launcher.py']])


def session(product,mode,phase,out):
    command=[sys.executable,'-I','-B',str(ROOT/'tests/acceptance/harness/packaged_app.py'),
             '--exe',str(product['bundle']/'PSModManager.exe'),'--out',str(out),'--mode',mode,
             '--phase',phase,'--fixture',str(product['out']/'fixture.json')]
    with (product['out']/(phase+'.stdout.log')).open('xb') as stdout, (product['out']/(phase+'.stderr.log')).open('xb') as stderr:
        result=subprocess.run(command,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=360,check=False)
    record=read(out/'result.json')
    assert result.returncode==0,f'Inspect {out}/result.json'
    assert record['status']=='PASS' and not record['forced_owned_cleanup']
    assert record['actual_token']['elevated'] is False
    assert record['actual_token']['integrity_rid']==8192
    assert record['actual_token']['enabled_admin_privileges']==[]
    assert record['exit_code']==(2 if mode=='missing-runtime' else 0)
    assert record['remaining_processes']==record['cleanup_remaining_processes']==0
    assert tree(product['bundle'])==product['package_before']
    return record


def test_case047_packaged_standard_permission_install_enable_disable_uninstall_and_reopen(product):
    session(product,'manage','first',product['first'])
    fixture=product['fixture'];fixture['game_after_first']=tree(Path(fixture['game']))
    write(product['out']/'fixture.json',fixture)
    # Reuse the exact default data directory across actual process restarts.
    first=product['first'];(first/'profile').rename(first/'previous-profile')
    (first/'中文 空格工作目录').rename(first/'previous-cwd')
    shutil.copyfile(first/'result.json',product['out']/'first-result.json')
    session(product,'manage','reopen',first)
    shutil.copyfile(first/'result.json',product['out']/'reopen-result.json')
    assert sha(Path(fixture['target']))==fixture['dll_sha256']
    assert sha(Path(fixture['dll']))==fixture['dll_sha256']
    assert fixture['dll_sha256'] in [sha(p) for p in (product['data']/'library').rglob('*') if p.is_file()]
    assert read(product['data']/'config.json')==fixture['config']


def test_case047_missing_webview_runtime_has_visible_error_and_no_install(product):
    out=product['out']/'missing';out.mkdir()
    record=session(product,'missing-runtime','missing',out)
    assert any('不会自动安装' in text for text in record['runtime_message'])
    assert not (out/'local/PSModManager').exists()
    assert not list((out/'empty-webview-runtime').iterdir())


def test_case048_existing_schema6_settings_and_single_backup_restore(product):
    data=product['data'];fixture=product['fixture']
    with sqlite3.connect(data/'psmodmanager.sqlite3') as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==7
        assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        assert db.execute('SELECT payload FROM runtime_fingerprints WHERE fingerprint_id=?',
                          (fixture['legacy_id'],)).fetchone()[0]==fixture['legacy_payload']
    backups=list(data.glob('*.before-v6.*.bak'));assert len(backups)==1
    backup=backups[0];metadata=read(backup.with_suffix('.bak.json'))
    assert sha(backup)==metadata['sha256']
    assert metadata['source_file_sha256']==fixture['legacy_database_sha256']
    out=product['out']/'restored';out.mkdir()
    restored=out/'local/PSModManager';restored.mkdir(parents=True)
    # Portable data recovery is an explicit offline copy into another owned
    # root. There is no invented DB-restore button or hidden product API.
    shutil.copyfile(backup,restored/'psmodmanager.sqlite3')
    shutil.copyfile(data/'config.json',restored/'config.json')
    session(product,'restore','restore',out)
    with sqlite3.connect(restored/'psmodmanager.sqlite3') as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==7
        assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        assert db.execute('SELECT payload FROM runtime_fingerprints WHERE fingerprint_id=?',
                          (fixture['legacy_id'],)).fetchone()[0]==fixture['legacy_payload']
    assert read(restored/'config.json')==fixture['config']
    assert sha(backup)==metadata['sha256']
    write(product['out']/'migration-result.json',dict(status='PASS',source_schema=6,target_schema=7,
        old_payload_preserved=True,unknown_settings_preserved=True,backup_hash_verified=True,
        restored_copy_actual_gui_started=True,real_user_upgrade='NOT_RUN'))


def test_case047_removing_only_owned_portable_copy_preserves_game_data_and_library(product):
    bundle=product['bundle'];owned=bundle.parent.resolve()
    assert owned.is_relative_to(ROOT/'.preview') and owned.name.startswith('p503-')
    assert bundle.resolve()==owned/'PSModManager' and bundle.is_dir()
    assert tree(bundle)==product['package_before']
    for path in [bundle,*bundle.rglob('*')]:
        assert not path.is_symlink() and not (path.lstat().st_file_attributes & 0x400)
    game=Path(product['fixture']['game']);before_game=tree(game);before_data=tree(product['data'])
    shutil.rmtree(bundle)
    assert not bundle.exists()
    assert tree(game)==before_game and tree(product['data'])==before_data
    write(product['out']/'portable-removal.json',dict(status='PASS',removed=str(bundle),
        game_unchanged=True,private_data_library_config_unchanged=True,
        original_ps0502_archive_and_bundle_removed=False))
