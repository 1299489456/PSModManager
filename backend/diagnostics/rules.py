"""Bounded deterministic explanations of inventory and immutable log evidence.

Log statements remain observations of their recorded session, never evidence of
the current deployment. No rule in this version authorizes a destructive action.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path, PurePosixPath
import re
from uuid import NAMESPACE_URL, uuid5

RULE_VERSION = 'psmm-diagnostics-v1'
DEFINITIONS = json.loads(Path(__file__).with_name('rules').joinpath('definitions.json').read_text(encoding='utf-8'))
MAX_DIAGNOSTICS = 512
_ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
_PREFIX = re.compile(r'^\s*\[([^\]]{1,256})\]\s*')


def identity(entry):
    analysis = entry.get('analysis') or {}
    melon = analysis.get('melon_info') or {}
    return dict(entry_id=entry['entry_id'], relative_path=str(entry.get('relative_path', ''))[:512],
                assembly_name=analysis.get('assembly_name') or entry.get('assembly_name'),
                assembly_version=analysis.get('assembly_version') or entry.get('assembly_version'),
                melon_name=melon.get('name'), melon_version_raw=melon.get('version_raw'),
                sha256=entry.get('sha256'), source='inventory_metadata')


def _line(record):
    body = _ANSI.sub('', record.get('text', '')).strip()
    tags = []
    while match := _PREFIX.match(body):
        tag = match[1]
        if tag not in ('ERROR', 'WARNING', 'INFO', 'DEBUG', 'TRACE') and not re.fullmatch(r'\d{2}:\d{2}:\d{2}(?:\.\d+)?', tag):
            tags.append(tag)
        body = body[match.end():]
    return body, tags


def evaluate(entries, evidence, *, installation_id, generation, fingerprint, coverage='complete'):
    """Pure projection. Inputs must belong to one bounded database snapshot."""
    entries = list(entries); evidence = list(evidence)
    if len(entries) > 512 or len(evidence) > 4096:
        raise ValueError('DIAGNOSTIC_INPUT_LIMIT')
    by_id = {row['entry_id']: row for row in entries}
    aliases = defaultdict(set)
    for row in entries:
        if row.get('presence') == 'missing':
            continue
        ident = identity(row)
        path = PurePosixPath(ident['relative_path'].replace('\\', '/'))
        for name in (path.name, path.stem, ident['assembly_name'], ident['melon_name']):
            if isinstance(name, str) and 0 < len(name) <= 512:
                aliases[name.casefold()].add(row['entry_id'])
    loader = fingerprint.get('loader_version')
    if loader is None and isinstance(fingerprint.get('loader'), dict):
        loader = fingerprint['loader'].get('version')
    # Historical evidence remains explainable without shipping an obsolete loader.
    known_loader = (isinstance(loader,str) and len(loader)<=256 and
                    re.fullmatch(r'v?0\.7\.3(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?',loader) is not None)
    diagnostics = []; truncated = False

    def resolve(names):
        found = set(); ambiguous = []
        for name in names:
            ids = aliases.get(name.casefold(), set())
            if len(ids) == 1: found.update(ids)
            elif len(ids) > 1: ambiguous.append(name[:256])
        return sorted(found), sorted(set(ambiguous))[:16]

    def emit(rule, classification, ids=(), logs=(), facts=None, inferences=(), reproduction=(), actions=()):
        nonlocal truncated
        if len(diagnostics) >= MAX_DIAGNOSTICS:
            truncated = True; return
        ids = sorted(set(ids))[:16]
        log_ids = list(dict.fromkeys(log['evidence_id'] for log in logs))
        if len(log_ids) > 16:
            log_ids = [*log_ids[:2], *log_ids[-14:]]
        seed = json.dumps([RULE_VERSION, installation_id, generation, fingerprint, rule, ids, log_ids, facts],
                          sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        diagnostics.append(dict(diagnostic_id=str(uuid5(NAMESPACE_URL, seed)), rule_id=rule,
            rule_version=RULE_VERSION, classification=classification, entry_ids=ids, evidence_ids=log_ids,
            identities=[identity(by_id[key]) for key in ids], facts=deepcopy(facts or {}),
            inferences=list(inferences), reproduction=list(reproduction), candidate_actions=list(actions),
            block=False, applicability='recorded_session_only' if logs else 'inventory_prediction',
            current_deployment_verified=False))

    current = [row for row in entries if row.get('presence') != 'missing']
    for row in current:
        analysis = row.get('analysis') or {}; prediction = row.get('prediction') or {}
        if 'assembly_version_shadowed' in prediction.get('reasons', []):
            emit('ML073-SHADOW', 'info' if known_loader else 'needs_verification', [row['entry_id']],
                 facts=dict(prediction=deepcopy(prediction), shadowed_by=row.get('shadowed_by', [])[:16], loader_version=loader),
                 inferences=['higher_assembly_version_predicted_to_win' if known_loader else 'unknown_loader_policy_not_applied'],
                 reproduction=['verify actual loader selection in a matching session'], actions=['inspect_component_details'])
        for framework, rule in [('itemforge', 'IF-CONTENT'), ('characterforge', 'CF-CONTENT')]:
            if framework + '_content' not in analysis.get('roles', []): continue
            manifests = [m for m in analysis.get('manifests', []) if m.get('framework') == framework]
            emit(rule, 'info', [row['entry_id']], facts=dict(role=framework + '_content',
                 has_melon_info=bool(analysis.get('melon_info')), registration_status='unknown',
                 manifests=[{key: m.get(key) for key in ('resource', 'bundle_id', 'required_runtime_api', 'status', 'source')}
                            for m in manifests[:4]]), inferences=['content_without_melon_info_is_not_corruption'],
                 reproduction=['runtime registration and gameplay remain unverified'], actions=['inspect_content_manifest'])

    groups = defaultdict(list); skipped = 0
    for log in evidence:
        if (not isinstance(log.get('text'), str) or len(log['text'].encode('utf-8')) > 16384 or
                log.get('fragment') or log.get('abandoned') or log.get('complete_line') is False or log.get('decode_errors')):
            skipped += 1; continue
        session = log.get('session') or {}
        key = (log.get('source_id'), log.get('epoch'), log.get('session_id') or session.get('session_id'))
        if not all(key): skipped += 1; continue
        groups[key].append(log)
    observed_counts = []
    for key in sorted(groups):
        logs = sorted(groups[key], key=lambda row: (row.get('seq', 0), row.get('byte_start', 0), row['evidence_id']))
        loader_declarations = []
        for declared in logs:
            text, prefixes = _line(declared)
            version_match = re.fullmatch(r'MelonLoader v?(\S+)(?: Open-Beta)?', text)
            if not version_match and prefixes == ['MelonLoader']:
                version_match = re.fullmatch(r'(?:v|Version: )([^\s]+)(?: Open-Beta)?', text)
            if version_match and prefixes in ([], ['MelonLoader']):
                loader_declarations.append((version_match[1], declared))
        declared_versions = set(value for value, _ in loader_declarations)
        recorded_loader = next(iter(declared_versions)) if len(declared_versions) == 1 else None
        applicable_session_loader = known_loader and recorded_loader == '0.7.3'
        domain_failures = []; init_failures = defaultdict(list)
        component_observations = defaultdict(list)
        successful_init = defaultdict(list)
        for position, observed in enumerate(logs):
            text, prefixes = _line(observed)
            linked, uncertain = resolve(prefixes)
            if len(linked) != 1 or uncertain: continue
            observation = None
            if re.match(r'^(?:Registered\s|已注册|注册成功)', text): observation = 'log_registration_reported'
            elif re.match(r'^(?:Initializing(?:\s|$)|开始初始化)', text): observation = 'log_initialization_started'
            elif text in ('Initialization Complete!', '初始化完成'):
                observation = 'log_initialization_completed'
                successful_init[tuple(linked)].append((position, observed))
            if observation and len(component_observations[linked[0]]) < 8:
                component_observations[linked[0]].append(dict(kind=observation, evidence_id=observed['evidence_id']))
        for position, log in enumerate(logs):
            body, tags = _line(log)
            ids, ambiguous = resolve(tags)
            scope = dict(source_id=key[0], epoch=key[1], session_id=key[2], association='log_declared_unverified')
            # Anchored statements, not incidental keywords in tutorials or quotes.
            if body == 'Failed to initialize a .NET domain' and 'MelonLoader.Bootstrap' in tags:
                domain_failures.append(log)
            elif ((re.fullmatch(r'\d+ Mods loaded\.', body) and tags in ([], ['MelonLoader'])) or
                  (body == 'Initialization Complete!' and tags == ['MelonLoader'])) and domain_failures:
                trusted_session = (log.get('session') or {}).get('association_confidence') == 'inferred'
                emit('ML-RECOVERED', 'recovered' if applicable_session_loader and trusted_session else 'needs_verification',
                     logs=[*[row for _, row in loader_declarations[:1]], *domain_failures, log], facts=dict(scope, continued_startup=True,
                     actual_runtime_version='unknown', loader_version=loader, recorded_loader_version=recorded_loader,
                     recorded_loader_version_sources=[row['evidence_id'] for _, row in loader_declarations[:2]]),
                     inferences=['continuation_observed_not_complete_gameplay_verification'],
                     reproduction=['verify a fresh authorized launch; never replace system runtime from HostFXR path alone'])
                domain_failures.clear()
            self_disabled = body.startswith('本次运行已停用本 MOD，未安装游戏补丁。')
            if self_disabled:
                candidates, _ = resolve(['PSItemPack_CuratedVisitors.dll', 'PSCharacterPack_CuratedVisitors.dll'])
                # The historical candidate association belongs only to this exact component.
                if not any((identity(by_id[i])['assembly_name'] == 'JCuratedVisitors' or
                            PurePosixPath(by_id[i]['relative_path']).name == 'JCuratedVisitors.dll') for i in ids):
                    candidates = []
                observations = [item for entry_id in ids for item in component_observations[entry_id]][:8]
                observation_ids = {item['evidence_id'] for item in observations}
                related_logs = [item for item in logs if item['evidence_id'] in observation_ids]
                emit('PS-SELF-DISABLED', 'confirmed_failure' if len(ids) == 1 and not ambiguous else 'needs_verification',
                     ids, [*related_logs, log], facts=dict(scope, self_disabled=True, patches_installed=False,
                     component_observations=observations, deployment_status='unverified',
                     candidate_old_entry_ids=candidates, candidate_causality='unverified', ambiguous_aliases=ambiguous),
                     inferences=['deployed_and_initialized_may_coexist_with_self_disabled'],
                     reproduction=['verify author upgrade guidance and reproduce using isolated save data'],
                     actions=['review_candidate_components', 'prepare_user_reviewed_test_profile'])
                version = re.search(r'启动\s*([0-9]+(?:\.[0-9]+)+(?:[-+\w.]*)?)', body)
                if version:
                    for entry_id in ids:
                        value = identity(by_id[entry_id])['melon_version_raw']
                        if value and value != version[1]:
                            emit('PS-VERSION-TEXT', 'info', [entry_id], [log], facts=dict(scope,
                                metadata_version_raw=value, log_template_version_raw=version[1],
                                version_authority='embedded_melon_metadata', metadata_source='MelonInfoAttribute'),
                                inferences=['different_sources_conflict_not_a_downgrade_request'], actions=['inspect_version_sources'])
            delegated = re.fullmatch(r'INACTIVE: ([\w.-]+) detected; expansion remains entirely controlled by the main mod\.', body)
            if delegated:
                targets, target_ambiguity = resolve([delegated[1]])
                emit('PS-DELEGATED', 'delegated' if len(ids) == 1 and not ambiguous else 'needs_verification',
                     ids, [log], facts=dict(scope, control_target_raw=delegated[1], control_target_entry_ids=targets,
                     ambiguous_aliases=ambiguous + target_ambiguity), inferences=['reported_delegation_not_loading_failure'],
                     actions=['inspect_component_details'])
            harmony = re.match(r'^MOD 兼容性检测：共享方法=(\d{1,8})，潜在冲突=(\d{1,8})，确认冲突=(\d{1,8})，停用模块=(\d{1,8})(?:；|$)', body)
            if harmony:
                counts = dict(zip(('shared_methods', 'potential_conflicts', 'confirmed_conflicts', 'disabled_modules'), map(int, harmony.groups())))
                loaded = re.search(r'\bloadedMods=(\d{1,6})(?:[。,;]|$)', body)
                if loaded: observed_counts.append(dict(evidence_id=log['evidence_id'], count=int(loaded[1]), kind='historical_log_report'))
                emit('HARMONY-POTENTIAL', 'potential_risk' if counts['potential_conflicts'] else 'info', ids, [log],
                     facts=dict(scope, reported_counts=counts, independently_verified=False, ambiguous_aliases=ambiguous),
                     inferences=['source_report_not_global_compatibility_proof'], actions=['inspect_reported_interactions'])
            elif body.startswith('可能存在 MOD 冲突：目标方法 '):
                mentioned = re.search(r'(?:可能被 MOD |也会被 MOD )(.+?) 的', body)
                related, uncertain = resolve([name.strip() for name in mentioned[1].split(',')]) if mentioned else ([], [])
                emit('HARMONY-POTENTIAL', 'potential_risk', sorted(set(ids + related)), [log],
                     facts=dict(scope, report='potential_interaction', ambiguous_aliases=ambiguous + uncertain),
                     inferences=['potential_not_confirmed_conflict'], actions=['inspect_reported_interactions'])
            if body == 'DialogUIManager.Instance 为 null':
                emit('PS-NULL-WARNING', 'info', ids, [log], facts=dict(scope, observed_null_warning=True),
                     inferences=['single_warning_does_not_prove_persistent_failure'], reproduction=['observe whether the issue recurs'])
            if re.fullmatch(r'(?:Initialization failed(?:[.:].*)?|初始化失败(?:[：:].*)?)', body):
                init_failures[(tuple(ids), tuple(ambiguous))].append((position, log))
        for failure in domain_failures:
            emit('ML-RECOVERED', 'needs_verification', logs=[failure], facts=dict(source_id=key[0], epoch=key[1],
                 session_id=key[2], continued_startup=False, actual_runtime_version='unknown',
                 recorded_loader_version=recorded_loader),
                 reproduction=['collect later evidence in the same session; do not replace system runtime automatically'])
        for (ids, ambiguous), failures in init_failures.items():
            later_success = [row for pos, row in successful_init[ids] if pos > failures[-1][0]]
            classification = ('recovered' if later_success and len(ids) == 1 and not ambiguous else
                              'confirmed_failure' if len(failures) >= 2 and len(ids) == 1 and not ambiguous else 'needs_verification')
            emit('PS-INIT-FAILURE', classification,
                 ids, [*[row for _, row in failures], *later_success[-1:]], facts=dict(source_id=key[0], epoch=key[1], session_id=key[2],
                 initialization_failure_count=len(failures), later_initialization_completed=bool(later_success), scope='recorded_session'),
                 reproduction=['reproduce persistent initialization failure in an authorized isolated session'], actions=['inspect_initialization_evidence'])
    result_coverage = 'partial' if truncated or skipped or coverage != 'complete' else 'complete'
    return dict(diagnostics=diagnostics, rule_version=RULE_VERSION, coverage=result_coverage,
                summary=dict(entry_count=len(current), melon_info_count=sum(bool((r.get('analysis') or {}).get('melon_info')) for r in current),
                direct_load_prediction_count=sum((r.get('prediction') or {}).get('status') == 'candidate' for r in current) if known_loader else None,
                prediction_loader_version=loader, historical_observed_loaded_counts=observed_counts[:8],
                classifications=dict(Counter(d['classification'] for d in diagnostics)), diagnostic_count=len(diagnostics),
                skipped_evidence_count=skipped, truncated=truncated, current_deployment_verified=False, hard_block_count=0))
