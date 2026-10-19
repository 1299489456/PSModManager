"""PS-01-06: real bridge, isolated metadata workers, stored log byte windows."""
import json
import shutil
import socket
import time

import pytest

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from tests.integration.test_content_catalog import corpus, call, wait_scan
from tests.integration.test_inventory_scan import loader_dll
from tests.integration.test_metadata_worker import BUILD, dll
from tests.unit.test_installation_discovery import discover, hashes, wait
from tests.unit.test_installation_layout import make_installation


def explain(api, installation_id, **payload):
    return call(api, 'diagnostics.explain', installation_id=installation_id, **payload)


def all_diagnostics(api, installation_id, **payload):
    first = explain(api, installation_id, **payload)
    rows = list(first['diagnostics'])
    page = first
    while page['next_offset'] is not None:
        page = explain(api, installation_id, **(payload | dict(generation=first['generation'],
                       snapshot_id=first['snapshot_id'], offset=page['next_offset'])))
        assert page['snapshot_id'] == first['snapshot_id']
        rows.extend(page['diagnostics'])
    assert len(rows) == first['total']
    return first, rows


@pytest.fixture(scope='module')
def visitor_dll(tmp_path_factory):
    root = tmp_path_factory.mktemp('diagnostic-version-source')
    source = root / 'Visitor.cs'
    source.write_text('''using System; using System.Reflection;
[assembly:AssemblyVersion("1.0.9.0")]
[assembly:MelonLoader.MelonInfo(typeof(Payload),"JCuratedVisitors","1.0.9","Synthetic")]
public class Payload { static Payload(){throw new Exception("must never execute");} }
namespace MelonLoader { [AttributeUsage(AttributeTargets.Assembly)] public class MelonInfoAttribute:Attribute {
 public MelonInfoAttribute(Type t,string n,string v,string a){throw new Exception("must never instantiate");} } }''', encoding='utf-8')
    return BUILD.compile_source(source, root / 'JCuratedVisitors.dll', library=True)


@pytest.fixture
def runtime(tmp_path, loader_dll, dll, visitor_dll):
    game = make_installation(tmp_path / 'diagnostic 合成 %')
    shutil.copyfile(loader_dll, game / 'MelonLoader/net6/MelonLoader.dll')
    (game / 'Mods').mkdir()
    shutil.copyfile(dll, game / 'Mods/WagesPerks.dll')
    shutil.copyfile(visitor_dll, game / 'Mods/JCuratedVisitors.dll')
    context = DesktopContext(resolve_paths(str(tmp_path / 'isolated-data')))
    context.initialize()
    context.choose_installation_folder = lambda kind: str(game)
    api = DesktopApi(context)
    context.api = api
    try:
        selected = wait(api, call(api, 'installation.select', candidate_id=discover(api)['candidate_id']))
        assert selected['state'] == 'succeeded', selected
        installation_id = selected['data']['installation']['installation_id']
        scan = wait_scan(api, call(api, 'inventory.scan', installation_id=installation_id))
        assert scan['status'] == 'complete', scan
        yield dict(api=api, context=context, game=game, identity=installation_id,
                   generation=scan['generation'], root=tmp_path)
    finally:
        context.close()


def index_log(runtime, text):
    prior = call(runtime['api'], 'evidence.list', installation_id=runtime['identity'])['total']
    path = runtime['game'] / 'MelonLoader/Latest.log'
    path.write_bytes(text.encode('utf-8'))
    before = hashes(runtime['game'])
    task = call(runtime['api'], 'logs.watch', installation_id=runtime['identity'])
    deadline = time.monotonic() + 12
    expected = prior + text.count('\n')
    while time.monotonic() < deadline:
        page = call(runtime['api'], 'evidence.list', installation_id=runtime['identity'])
        if page['total'] >= expected:
            break
        time.sleep(.04)
    else:
        raise AssertionError('Synthetic log evidence was not indexed')
    call(runtime['api'], 'task.cancel', task_id=task['task_id'])
    assert wait(runtime['api'], task)['state'] == 'cancelled'
    assert hashes(runtime['game']) == before
    return page


def test_real_45_44_43_explanations_keep_content_and_shadow_distinct(corpus):
    first, rows = all_diagnostics(corpus['api'], corpus['identity'])
    by_rule = {}
    for row in rows:
        by_rule.setdefault(row['rule_id'], []).append(row)
    assert 'ML073-SHADOW' in by_rule
    assert 'IF-CONTENT' in by_rule and 'CF-CONTENT' in by_rule
    assert first['deployment_verification'] == 'not_run'
    assert len(corpus['rows']) == 45
    assert first['summary']['melon_info_count'] == 44
    assert first['summary']['entry_count'] == 45
    assert first['summary']['direct_load_prediction_count'] == 43
    assert sum(r['prediction']['status'] == 'candidate' for r in corpus['rows']) == 43
    assert hashes(corpus['game']) == corpus['before']
    report = dict(physical_count=45, melon_count=44, predicted_candidate_count=43,
                  fixture='self-authored offline C#; actual LPAC metadata scanner',
                  result=first, diagnostics=rows, before=corpus['before'], after=hashes(corpus['game']))
    (corpus['root'] / 'diagnostic-45-evidence.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


def test_offline_explain_is_readonly_and_no_registration_invented(runtime, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Diagnostics must not open network connections')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    before_tree = hashes(runtime['game'])
    before_db = runtime['context'].paths.database.read_bytes()
    first, rows = all_diagnostics(runtime['api'], runtime['identity'])
    assert first['deployment_verification'] == 'not_run'
    assert not [r for r in rows if r.get('classification') == 'confirmed_failure']
    assert runtime['context'].paths.database.read_bytes() == before_db
    assert hashes(runtime['game']) == before_tree


def test_case010_019_real_identity_version_and_selfdisabled_windows(runtime):
    text = ('MelonLoader v0.7.3\n'
            '[JCuratedVisitors] Registered 21 items\n'
            '[JCuratedVisitors] Initialization Complete!\n'
            '[JCuratedVisitors] 本次运行已停用本 MOD，未安装游戏补丁。请检查旧组件后启动0.5.0\n'
            '[JCuratedVisitors] 可能存在 MOD 冲突：目标方法 Trade 可能被 MOD JacksonPerks 的前置补丁跳过原方法\n')
    index_log(runtime, text)
    page, rows = all_diagnostics(runtime['api'], runtime['identity'])
    disabled = next(r for r in rows if r['rule_id'] == 'PS-SELF-DISABLED')
    version = next(r for r in rows if r['rule_id'] == 'PS-VERSION-TEXT')
    assert disabled['classification'] == 'confirmed_failure'
    assert disabled['facts']['candidate_causality'] == 'unverified'
    assert version['facts']['metadata_version_raw'] == '1.0.9'
    assert version['facts']['log_template_version_raw'] == '0.5.0'
    assert version['facts']['version_authority'] == 'embedded_melon_metadata'
    potential = next(r for r in rows if r['rule_id'] == 'HARMONY-POTENTIAL')
    perks = next(i for i in potential['identities'] if i['assembly_name'] == 'JacksonPerks')
    assert perks['relative_path'] == 'Mods/WagesPerks.dll'
    assert perks['melon_name'] == 'Perks display' and perks['melon_version_raw'] == '0.46D'
    assert perks['assembly_version'] == '2.3.4.5'
    assert not (runtime['game'] / 'Mods/JacksonPerks.dll').exists()
    windows = []
    for row in (disabled, version, potential):
        assert row['evidence_ids'] and not row['current_deployment_verified'] and not row['block']
        for evidence_id in row['evidence_ids']:
            window = call(runtime['api'], 'evidence.read', installation_id=runtime['identity'], evidence_id=evidence_id)
            assert window['text'] in text and window['excerpt_hash']
            windows.append(window)
    sessions = call(runtime['api'], 'session.list', installation_id=runtime['identity'])['entries']
    assert all(s['pid'] is None and s['generation_id'] is None for s in sessions)
    assert page['deployment_verification'] == 'not_run'
    (runtime['root'] / 'identity-version-selfdisabled.json').write_text(json.dumps(dict(result=page, diagnostics=rows, windows=windows), ensure_ascii=False, indent=2), encoding='utf-8')


@pytest.mark.parametrize('separate', [False, True])
def test_case018_recovery_is_bound_to_recorded_session(runtime, separate):
    text = 'MelonLoader v0.7.3\n[MelonLoader.Bootstrap] Failed to initialize a .NET domain\n'
    if separate:
        text += 'MelonLoader v0.7.3\n'
    text += '43 Mods loaded.\n'
    index_log(runtime, text)
    _, rows = all_diagnostics(runtime['api'], runtime['identity'])
    recovery = [r for r in rows if r['rule_id'] == 'ML-RECOVERED']
    assert len(recovery) == 1
    assert recovery[0]['classification'] == ('needs_verification' if separate else 'recovered')
    assert recovery[0]['facts']['actual_runtime_version'] == 'unknown'
    windows = [call(runtime['api'], 'evidence.read', installation_id=runtime['identity'], evidence_id=value)['text']
               for value in recovery[0]['evidence_ids']]
    expected = {'[MelonLoader.Bootstrap] Failed to initialize a .NET domain\n'}
    if not separate:
        expected |= {'MelonLoader v0.7.3\n', '43 Mods loaded.\n'}
    assert set(windows) == expected and len(windows) == len(expected)


def test_case020_delegation_potential_null_and_persistent_failure(runtime):
    text = ('MelonLoader v0.7.3\n'
            '[JCuratedVisitors] INACTIVE: XIAOWO-TradePerks detected; expansion remains entirely controlled by the main mod.\n'
            '[JCuratedVisitors] MOD 兼容性检测：共享方法=46，潜在冲突=21，确认冲突=0，停用模块=0；loadedMods=43。\n'
            '[JacksonPerks] DialogUIManager.Instance 为 null\n'
            '[JacksonPerks] Initialization failed: missing fixture state\n'
            '[JacksonPerks] Initialization failed: missing fixture state\n')
    index_log(runtime, text)
    page, rows = all_diagnostics(runtime['api'], runtime['identity'])
    by_rule = {row['rule_id']: row for row in rows}
    for rule, classification in [('PS-DELEGATED','delegated'), ('HARMONY-POTENTIAL','potential_risk'),
                                  ('PS-NULL-WARNING','info'), ('PS-INIT-FAILURE','confirmed_failure')]:
        assert by_rule[rule]['classification'] == classification
    counts = by_rule['HARMONY-POTENTIAL']['facts']['reported_counts']
    assert counts['potential_conflicts'] == 21 and counts['confirmed_conflicts'] == 0
    assert counts['disabled_modules'] == 0
    assert page['summary']['historical_observed_loaded_counts'][0]['kind'] == 'historical_log_report'
    assert all(not row['block'] and not row['current_deployment_verified'] for row in rows)
    assert not any(action in ('delete','disable','downgrade') for row in rows for action in row['candidate_actions'])


def test_unknown_loader_does_not_confirm_recovery(runtime):
    (runtime['game'] / 'MelonLoader/net6/MelonLoader.dll').write_bytes(b'unknown synthetic loader version')
    scan = wait_scan(runtime['api'], call(runtime['api'], 'inventory.scan', installation_id=runtime['identity'], force=True))
    assert scan['status'] == 'complete'
    index_log(runtime, 'MelonLoader v0.7.3\n[MelonLoader.Bootstrap] Failed to initialize a .NET domain\n43 Mods loaded.\n')
    page, rows = all_diagnostics(runtime['api'], runtime['identity'])
    assert page['summary']['prediction_loader_version'] is None
    assert page['summary']['direct_load_prediction_count'] is None
    assert next(r for r in rows if r['rule_id'] == 'ML-RECOVERED')['classification'] == 'needs_verification'
