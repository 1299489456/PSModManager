"""Synthetic projections of documented local audit excerpts, no game access."""
from copy import deepcopy
import json

import pytest

from backend.diagnostics.rules import DEFINITIONS, evaluate


def entry(name='JCuratedVisitors', *, display='烛印会与失灯区', version='1.0.9', file=None, key=None):
    return dict(entry_id=key or name, relative_path='Mods/' + (file or name + '.dll'), presence='present',
                sha256='a' * 64, prediction={'status':'candidate','reasons':[]}, analysis=dict(
                assembly_name=name, assembly_version='1.0.9.0', melon_info=dict(name=display, version_raw=version),
                roles=['melon_mod'], manifests=[]))


def log(text, n=1, **kwargs):
    return dict(evidence_id=f'evidence-{n}', text=text, seq=n, byte_start=n*100,
                source_id='latest', epoch='epoch-1', session_id='session-1', complete_line=True,
                fragment=False, decode_errors=0, session={'association_confidence':'inferred', 'generation_id':None}, **kwargs)


def run(rows=(), logs=(), **kwargs):
    return evaluate(rows, logs, installation_id='installation', generation=1,
                    fingerprint=kwargs.pop('fingerprint', {'loader_version':'0.7.3'}), **kwargs)


def diags(result, rule):
    return [item for item in result['diagnostics'] if item['rule_id'] == rule]


DISABLED = '[14:16:46.824] [ERROR] [烛印会与失灯区] [第七席] 本次运行已停用本 MOD，未安装游戏补丁。检测到本MOD旧版两个DLL。请退出后移走旧版，再用新存档启动0.5.0；其他作者MOD保持原样。'
DOMAIN = '[ERROR] [MelonLoader.Bootstrap] Failed to initialize a .NET domain'
HARMONY = '[XIAOWO-TradePerks] MOD 兼容性检测：共享方法=46，潜在冲突=21，确认冲突=0，停用模块=0；loadedMods=43。'


def test_definitions_have_input_applicability_counterexamples_and_sources():
    assert len(DEFINITIONS['rules']) == 10
    for rule in DEFINITIONS['rules']:
        assert rule['version'] == 1
        assert all(rule[key] for key in ('inputs','fingerprint_applicability','counterexamples','evidence_template'))


def test_self_disabled_preserves_versions_and_only_real_candidate_entries():
    rows = [entry(), entry('PSItemPack_CuratedVisitors', display='Old Items'),
            entry('PSCharacterPack_CuratedVisitors', display='Old Characters')]
    result = run(rows, [log(DISABLED)])
    disabled = diags(result, 'PS-SELF-DISABLED')[0]
    assert disabled['classification'] == 'confirmed_failure'
    assert disabled['entry_ids'] == ['JCuratedVisitors']
    assert disabled['facts']['candidate_old_entry_ids'] == ['PSCharacterPack_CuratedVisitors', 'PSItemPack_CuratedVisitors']
    assert disabled['facts']['candidate_causality'] == 'unverified'
    conflict = diags(result, 'PS-VERSION-TEXT')[0]
    assert conflict['facts']['metadata_version_raw'] == '1.0.9'
    assert conflict['facts']['log_template_version_raw'] == '0.5.0'
    assert not any(d['block'] for d in result['diagnostics'])
    assert all('delete' not in action for d in result['diagnostics'] for action in d['candidate_actions'])


@pytest.mark.parametrize('text', ['Tutorial: ' + DISABLED, 'The documentation says: Failed to initialize a .NET domain',
                               '教程 DialogUIManager.Instance 为 null', 'INACTIVE', 'error failure 失败'])
def test_tutorials_and_incomplete_claims_do_not_trigger(text):
    assert run([entry()], [log(text)])['diagnostics'] == []


def test_registration_does_not_erase_later_or_earlier_self_disable():
    for lines in [[log('[烛印会与失灯区] Registered 1 item', 1), log(DISABLED, 2)],
                  [log(DISABLED, 1), log('[烛印会与失灯区] Registered 1 item', 2)]]:
        item = diags(run([entry()], lines), 'PS-SELF-DISABLED')[0]
        assert item['facts']['self_disabled'] is True
        assert item['facts']['component_observations'][0]['kind'] == 'log_registration_reported'
        assert item['facts']['deployment_status'] == 'unverified'


@pytest.mark.parametrize('version', ['0.5.0', None])
def test_no_version_conflict_without_different_known_metadata(version):
    assert not diags(run([entry(version=version)], [log(DISABLED)]), 'PS-VERSION-TEXT')


def test_ambiguous_display_name_never_guesses_and_nonstandard_version_stays_raw():
    rows = [entry(version='build-preview+custom'), entry(name='Other', display='烛印会与失灯区')]
    result = run(rows, [log(DISABLED)])
    assert diags(result, 'PS-SELF-DISABLED')[0]['classification'] == 'needs_verification'
    assert diags(result, 'PS-SELF-DISABLED')[0]['entry_ids'] == []
    single = diags(run(rows[:1], [log(DISABLED)]), 'PS-VERSION-TEXT')[0]
    assert single['facts']['metadata_version_raw'] == 'build-preview+custom'


def test_exact_alias_links_jackson_to_existing_wages_file_and_preserves_identities():
    wages = entry('JacksonPerks', file='WagesPerks.dll', display='Different Display', version='beta-test')
    reporter = entry('XIAOWOTradePerks', display='XIAOWO-TradePerks')
    text = '[WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 DirectoryMaster.Item 可能被 MOD JacksonPerks, MissingMod 的前置补丁跳过原方法'
    item = diags(run([wages,reporter], [log(text)]), 'HARMONY-POTENTIAL')[0]
    assert item['entry_ids'] == ['JacksonPerks', 'XIAOWOTradePerks']
    identity = next(i for i in item['identities'] if i['entry_id'] == 'JacksonPerks')
    assert identity['relative_path'] == 'Mods/WagesPerks.dll'
    assert identity['melon_name'] == 'Different Display'
    assert identity['melon_version_raw'] == 'beta-test'
    assert identity['assembly_version'] == '1.0.9.0'
    bad = text.replace('JacksonPerks,', 'JacksonPerksExtra,')
    assert 'JacksonPerks' not in diags(run([wages,reporter], [log(bad)]), 'HARMONY-POTENTIAL')[0]['entry_ids']


def test_45_44_43_prediction_distinct_from_historical_observed_count():
    rows = [entry(f'Synthetic{i}', display=f'Synthetic{i}') for i in range(45)]
    rows[-1]['analysis'].update(melon_info=None, roles=['itemforge_content'], manifests=[dict(
        framework='itemforge',resource='ItemForge.items.json',required_runtime_api=3, status='valid')])
    rows[-1]['prediction']['status'] = 'excluded'
    rows[-2]['prediction'] = dict(status='excluded',reasons=['assembly_version_shadowed'])
    rows[-2]['shadowed_by'] = ['Mods/Synthetic0.dll']
    result = run(rows,[log(HARMONY)])
    assert result['summary']['entry_count'] == 45
    assert result['summary']['melon_info_count'] == 44
    assert result['summary']['direct_load_prediction_count'] == 43
    assert result['summary']['historical_observed_loaded_counts'][0]['kind'] == 'historical_log_report'
    assert diags(result,'IF-CONTENT')[0]['facts']['registration_status'] == 'unknown'
    assert diags(result,'ML073-SHADOW')[0]['candidate_actions'] == ['inspect_component_details']


@pytest.mark.parametrize('framework,rule', [('itemforge','IF-CONTENT'),('characterforge','CF-CONTENT')])
def test_content_requires_analyzed_role_and_never_claims_registration(framework, rule):
    row=entry();row['analysis']['melon_info']=None
    assert not diags(run([row]),rule)
    row['analysis']['roles']=[framework+'_content']
    row['analysis']['manifests']=[dict(framework=framework,bundle_id={'type':'bundle','value':'same'},required_runtime_api=2)]
    diag=diags(run([row]),rule)[0]
    assert diag['facts']['registration_status']=='unknown'
    assert diag['classification']=='info'


def test_unknown_loader_does_not_apply_stock_shadow_or_recovery_policy():
    row=entry();row['prediction']=dict(status='excluded',reasons=['assembly_version_shadowed'])
    result=run([row],[log(DOMAIN),log('43 Mods loaded.',2)],fingerprint={'loader_version':'9.0'})
    assert result['summary']['direct_load_prediction_count'] is None
    assert all(d['classification']=='needs_verification' for d in result['diagnostics'])


def test_release_build_metadata_uses_same_stock_loader_diagnosis():
    row=entry();row['prediction']=dict(status='excluded',reasons=['assembly_version_shadowed'])
    plain=run([row],fingerprint={'loader_version':'0.7.3'})
    built=run([row],fingerprint={'loader_version':'0.7.3+982ed9981ac5b0619648a2e79bcc02fd8861db33'})
    assert [(d['rule_id'],d['classification']) for d in built['diagnostics']]==[(d['rule_id'],d['classification']) for d in plain['diagnostics']]
    assert built['diagnostics'][0]['classification']=='info'
    assert built['diagnostics'][0]['facts']['loader_version'].endswith('+982ed9981ac5b0619648a2e79bcc02fd8861db33')
    assert built['summary']['prediction_loader_version'].endswith('+982ed9981ac5b0619648a2e79bcc02fd8861db33')
    assert built['summary']['direct_load_prediction_count']==plain['summary']['direct_load_prediction_count']


def test_same_session_domain_continuation_recovers_without_runtime_or_gameplay_claim():
    item=diags(run(logs=[log('MelonLoader v0.7.3 Open-Beta',0),log(DOMAIN),log('43 Mods loaded.',2)]),'ML-RECOVERED')[0]
    assert item['classification']=='recovered'
    assert item['evidence_ids']==['evidence-0','evidence-1','evidence-2']
    assert item['facts']['actual_runtime_version']=='unknown'
    assert item['current_deployment_verified'] is False


@pytest.mark.parametrize('field,value',[('source_id','other'),('epoch','other'),('session_id','other')])
def test_domain_failure_cannot_recover_across_source_epoch_or_session(field,value):
    recovery=log('43 Mods loaded.',2);recovery[field]=value
    assert diags(run(logs=[log(DOMAIN),recovery]),'ML-RECOVERED')[0]['classification']=='needs_verification'


@pytest.mark.parametrize('later',['Using HostFXR Path: C:/dotnet/10/hostfxr.dll',
                                  'Running the .NET Runtime Installer...', 'Tutorial: Initialization Complete!'])
def test_hostfxr_and_installer_are_not_recovery(later):
    assert diags(run(logs=[log(DOMAIN),log(later,2)]),'ML-RECOVERED')[0]['classification']=='needs_verification'


def test_earlier_continuation_does_not_recover_later_failure():
    assert diags(run(logs=[log('43 Mods loaded.'),log(DOMAIN,2)]),'ML-RECOVERED')[0]['classification']=='needs_verification'


def test_delegation_potential_null_and_persistent_failure_are_distinct():
    rows=[entry('Expansion',display='XIAOWO-PrecisionBayExpansion'),entry('Trade',display='XIAOWO-TradePerks'),entry('Broken',display='Broken')]
    logs=[log('[XIAOWO-PrecisionBayExpansion] INACTIVE: XIAOWO-TradePerks detected; expansion remains entirely controlled by the main mod.'),
          log(HARMONY,2),log('[WARNING] [Broken] DialogUIManager.Instance 为 null',3),
          log('[ERROR] [Broken] Initialization failed: missing input',4),log('[ERROR] [Broken] Initialization failed: missing input',5)]
    result=run(rows,logs)
    assert diags(result,'PS-DELEGATED')[0]['classification']=='delegated'
    risk=diags(result,'HARMONY-POTENTIAL')[0]
    assert risk['classification']=='potential_risk'
    assert risk['facts']['reported_counts']['potential_conflicts']==21
    assert risk['facts']['reported_counts']['confirmed_conflicts']==0
    assert diags(result,'PS-NULL-WARNING')[0]['classification']=='info'
    assert diags(result,'PS-INIT-FAILURE')[0]['classification']=='confirmed_failure'
    assert result['summary']['hard_block_count']==0


def test_one_or_cross_session_initialization_failure_not_persistent():
    logs=[log('[Broken] Initialization failed: x'),log('[Broken] Initialization failed: x',2)]
    logs[1]['session_id']='other'
    result=run([entry('Broken',display='Broken')],logs)
    assert len(diags(result,'PS-INIT-FAILURE'))==2
    assert all(d['classification']=='needs_verification' for d in result['diagnostics'])


@pytest.mark.parametrize('update',[{'fragment':True},{'abandoned':True},{'decode_errors':1},{'complete_line':False}])
def test_incomplete_evidence_does_not_trigger(update):
    row=log(DISABLED);row.update(update)
    result=run([entry()],[row])
    assert result['diagnostics']==[] and result['coverage']=='partial'


def test_bounds_determinism_and_inputs_not_mutated():
    rows=[entry()];logs=[log(DISABLED)];before=deepcopy((rows,logs))
    assert run(rows,logs)==run(rows,logs)
    assert (rows,logs)==before
    result=run(rows,[log(DISABLED,n) for n in range(400)])
    assert len(result['diagnostics'])==512
    assert result['coverage']=='partial' and result['summary']['truncated']
    assert len(json.dumps(result['summary']).encode())<2048
    assert max(len(json.dumps(d).encode()) for d in result['diagnostics'])<30*1024
    with pytest.raises(ValueError,match='INPUT_LIMIT'):
        run([entry()]*513)


def test_many_domain_failures_preserve_first_failure_and_final_recovery_evidence():
    rows=[log('MelonLoader v0.7.3 Open-Beta',0)]+[log(DOMAIN,n) for n in range(1,21)]+[log('[MelonLoader] 43 Mods loaded.',21)]
    item=diags(run(logs=rows),'ML-RECOVERED')[0]
    assert item['classification']=='recovered'
    assert len(item['evidence_ids'])==16
    assert item['evidence_ids'][:2]==['evidence-0','evidence-1']
    assert item['evidence_ids'][-1]=='evidence-21'


@pytest.mark.parametrize('text',['[OtherMod] Initialization Complete!','Initialization Complete!', '[OtherMod] 43 Mods loaded.'])
def test_other_component_completion_cannot_recover_loader_domain(text):
    assert diags(run(logs=[log(DOMAIN),log(text,2)]),'ML-RECOVERED')[0]['classification']=='needs_verification'


def test_explicit_same_component_success_after_failures_is_not_persistent_failure():
    rows=[log('[Broken] Initialization failed: x',n) for n in range(1,33)]
    rows.append(log('[Broken] Initialization Complete!',33))
    item=diags(run([entry('Broken',display='Broken')],rows),'PS-INIT-FAILURE')[0]
    assert item['classification']=='recovered'
    assert item['facts']['later_initialization_completed'] is True
    assert 'evidence-33' in item['evidence_ids']
    rows[-1]['session_id']='other'
    assert diags(run([entry('Broken',display='Broken')],rows),'PS-INIT-FAILURE')[0]['classification']=='confirmed_failure'


def test_initialization_and_self_disable_keep_both_observations_without_deployment_claim():
    result=run([entry()],[log('[烛印会与失灯区] Initializing mod'),log(DISABLED,2)],coverage='partial')
    item=diags(result,'PS-SELF-DISABLED')[0]
    assert item['facts']['component_observations']==[{'kind':'log_initialization_started','evidence_id':'evidence-1'}]
    assert item['evidence_ids']==['evidence-1','evidence-2']
    assert item['current_deployment_verified'] is False and item['block'] is False
    assert result['coverage']=='partial'


@pytest.mark.parametrize('banner',[None,'MelonLoader v0.8.0','MelonLoader v0.7.3-preview'])
def test_missing_or_mismatched_recorded_loader_does_not_reuse_scan_fingerprint(banner):
    rows=([log(banner,0)] if banner else [])+[log(DOMAIN),log('43 Mods loaded.',2)]
    item=diags(run(logs=rows),'ML-RECOVERED')[0]
    assert item['classification']=='needs_verification'
    assert item['facts']['recorded_loader_version']==(banner.split(' v')[1] if banner else None)


@pytest.mark.parametrize('continuation,classification',[
    ('[14:16:01.123] [INFO] 43 Mods loaded.','recovered'),
    ('[14:16:01.123] [INFO] [MelonLoader] Initialization Complete!','recovered'),
    ('[14:16:01.123] [INFO] [OtherMod] Initialization Complete!','needs_verification')])
def test_timestamp_and_level_prefixes_do_not_hide_actual_logger(continuation,classification):
    rows=[log('[14:15:00.123] MelonLoader v0.7.3 Open-Beta',0),log(DOMAIN),log(continuation,2)]
    assert diags(run(logs=rows),'ML-RECOVERED')[0]['classification']==classification
