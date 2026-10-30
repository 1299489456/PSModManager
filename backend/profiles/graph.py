"""Bounded, deterministic compatibility analysis; never applies graph edits.

Release versions, assembly versions and integer capability APIs are separate
domains. Display order is deliberately absent from this interface.
"""
from collections import defaultdict, deque
import hashlib
import json
import re
import unicodedata
from uuid import UUID

from backend.contracts import Component, FileEntry, Relation, RuntimeFingerprint
from backend.contracts.models import Source
from backend.scanning import legacy_loader_rules as v073

RULE_VERSION = 'psmm-compatibility-1'
MAX_COMPONENTS = 1000
MAX_RELATIONS = 2000
MAX_FILES = 5000
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_FINDINGS = 5000


class GraphError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _require(value, code='GRAPH_INPUT_INVALID'):
    if not value:
        raise GraphError(code)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _id(value):
    _require(isinstance(value, str) and str(UUID(value)) == value)
    return value


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,6}(?:\.\d{1,6}){0,3}', value):
        return None
    parts = tuple(map(int, value.split('.')))
    return parts + (0,) * (4 - len(parts))


def version_satisfies(actual, requirement):
    """Only exact numeric versions or comma-separated numeric comparators.

    No SemVer/prerelease, assembly/API coercion, caret, wildcard or OR guesses.
    None means the metadata does not support a determinate comparison.
    """
    if requirement is None:
        return True
    if not isinstance(requirement, str) or len(requirement) > 200:
        return None
    actual = _version(actual)
    if actual is None:
        return None
    clauses = requirement.split(',')
    if len(clauses) > 8:
        return None
    outcomes = []
    for clause in clauses:
        match = re.fullmatch(r'\s*(==|>=|<=|>|<|=)?\s*(\d{1,6}(?:\.\d{1,6}){0,3})\s*', clause)
        if not match:
            return None
        op, wanted = match[1] or '==', _version(match[2])
        outcomes.append({'==': actual == wanted, '=': actual == wanted, '>=': actual >= wanted,
                         '<=': actual <= wanted, '>': actual > wanted, '<': actual < wanted}[op])
    return all(outcomes)


def _source_trusted(source):
    ref = re.sub('[^a-z]', '', source['reference'].lower())
    return source['kind'] in ('author_manifest', 'embedded_metadata', 'loader_rule', 'user') and 'assemblyref' not in ref


def _applicability(relation, fingerprint):
    if relation['fingerprint_scope'] is not None and relation['fingerprint_scope'] != fingerprint['fingerprint_id']:
        return 'out_of_scope'
    if relation['confidence'] != 'confirmed' or not _source_trusted(relation['source']):
        return 'untrusted'
    if relation['fingerprint_scope'] is None or fingerprint['coverage'] != 'complete':
        return 'unknown'
    return 'trusted'


def _scc(nodes, edges, check):
    """Iterative Kosaraju: long chains never depend on Python recursion depth."""
    adjacency = {node: sorted({target for target, _ in edges.get(node, [])}) for node in nodes}
    reverse = defaultdict(list)
    for source, targets in adjacency.items():
        for target in targets:
            reverse[target].append(source)
    seen, order = set(), []
    for node in sorted(nodes):
        if node in seen:
            continue
        seen.add(node)
        stack = [(node, iter(adjacency[node]))]
        while stack:
            check()
            current, children = stack[-1]
            target = next(children, None)
            if target is None:
                order.append(current)
                stack.pop()
            elif target not in seen:
                seen.add(target)
                stack.append((target, iter(adjacency[target])))
    seen, groups = set(), []
    for node in reversed(order):
        if node in seen:
            continue
        group, queue = [], [node]
        seen.add(node)
        while queue:
            check()
            current = queue.pop()
            group.append(current)
            for target in sorted(reverse[current]):
                if target not in seen:
                    seen.add(target)
                    queue.append(target)
        group.sort()
        if len(group) > 1 or node in adjacency[node]:
            groups.append(group)
    return sorted(groups)


def evaluate_graph(*, components, relations, files, fingerprint, selected_release_ids,
                   requested_enable=None, requested_disable=None, loader_options=None, check=lambda: None):
    """Evaluate immutable input snapshots, returning candidate sets and evidence.

    Component extensions: release version_raw, optional enabled, explicit
    capabilities[{name,value,source,confidence,fingerprint_scope}]. Provider API
    values are integers and need their own confirmed, scoped source.
    """
    callback_errors = []
    def checked():
        try:
            check()
        except BaseException as error:
            callback_errors.append(error)
            raise
    try:
        return _evaluate(components, relations, files, fingerprint, selected_release_ids,
                         [] if requested_enable is None else requested_enable,
                         [] if requested_disable is None else requested_disable, loader_options, checked)
    except GraphError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        if callback_errors and callback_errors[-1] is error:
            raise
        raise GraphError('GRAPH_INPUT_INVALID') from None


def _evaluate(components, relations, files, fingerprint, selected, enable, disable, loader_options, check, build_previews=True):
    original_components = components
    for items, maximum in ((components, MAX_COMPONENTS), (relations, MAX_RELATIONS), (files, MAX_FILES),
                           (selected, MAX_COMPONENTS), (enable, MAX_COMPONENTS), (disable, MAX_COMPONENTS)):
        _require(type(items) is list)
        _require(len(items) <= maximum, 'GRAPH_LIMIT_EXCEEDED')
    check()
    _require(len(_json([components, relations, files, fingerprint, selected, enable, disable]).encode('utf-8')) <= MAX_INPUT_BYTES,
             'GRAPH_LIMIT_EXCEEDED')
    fingerprint = RuntimeFingerprint.model_validate(fingerprint).model_dump(mode='json')
    component_map, providers = {}, defaultdict(list)
    for raw in components:
        check()
        _require(type(raw) is dict)
        extra = {key: raw[key] for key in ('version_raw', 'enabled', 'capabilities') if key in raw}
        value = Component.model_validate({key: item for key, item in raw.items() if key not in extra}).model_dump(mode='json')
        _require(value['component_id'] not in component_map)
        if 'enabled' in extra:
            _require(type(extra['enabled']) is bool)
        _require(extra.get('version_raw') is None or (isinstance(extra['version_raw'], str) and len(extra['version_raw']) <= 200))
        value['version_raw'] = extra.get('version_raw')
        component_map[value['component_id']] = value
        capability_values = extra.get('capabilities', [])
        _require(type(capability_values) is list and len(capability_values) <= 32, 'GRAPH_LIMIT_EXCEEDED')
        for provider in capability_values:
            _require(type(provider) is dict and set(provider) == {'name','value','source','confidence','fingerprint_scope'})
            _require(isinstance(provider['name'], str) and 0 < len(provider['name']) <= 200)
            _require(type(provider['value']) is int and 0 <= provider['value'] <= 2147483647)
            _require(provider['confidence'] in ('confirmed', 'inferred', 'unknown'))
            if provider['fingerprint_scope'] is not None:
                _id(provider['fingerprint_scope'])
            source = Source.model_validate(provider['source']).model_dump(mode='json')
            providers[provider['name']].append(dict(provider, source=source, component_id=value['component_id']))
    relation_map = {}
    for raw in relations:
        check()
        value = Relation.model_validate(raw).model_dump(mode='json')
        _require(value['relation_id'] not in relation_map)
        relation_map[value['relation_id']] = value
    file_map = {}
    for raw in files:
        value = FileEntry.model_validate(raw).model_dump(mode='json')
        _require(value['file_entry_id'] not in file_map)
        file_map[value['file_entry_id']] = value
    for ids in (selected, enable, disable):
        _require(len(ids) == len(set(ids)))
        for item in ids:
            _id(item)
    selected, enable, disable = set(selected), set(enable), set(disable)
    available = {c['release_id'] for c in component_map.values()} | {f['release_id'] for f in file_map.values()}
    _require(enable <= available and disable <= selected)
    roots = selected | enable
    scope = {'fingerprint_id':fingerprint['fingerprint_id'], 'fingerprint_coverage':fingerprint['coverage'],
             'loader_version':fingerprint['loader_version'], 'rule_version':RULE_VERSION}
    local_source = {'kind':'loader_rule', 'reference':RULE_VERSION, 'observed_at':fingerprint['observed_at']}
    findings, unknowns, seen_findings = [], [], set()

    def finding(code, category, severity, message, *, nodes=(), release_ids=(), relation=None, chain=None, unknown=False, source=None, detail=None):
        check()
        ids = sorted(set(nodes))
        releases = sorted(set(release_ids) | {component_map[node]['release_id'] for node in ids if node in component_map})
        relation_ids = [relation['relation_id']] if relation else []
        proof = {'component_ids':ids, 'relation_ids':relation_ids} if chain is None else chain
        item = dict(category=category, severity=severity, code=code, message=message,
                    component_ids=ids, release_ids=releases, relation_ids=relation_ids,
                    source=source or (relation['source'] if relation else local_source),
                    scope=dict(scope, relation_fingerprint_id=relation['fingerprint_scope'] if relation else None), reason_chain=proof)
        if detail is not None:
            item['detail'] = detail
        item['id'] = hashlib.sha256(_json(item).encode()).hexdigest()
        if item['id'] not in seen_findings:
            _require(len(findings) < MAX_FINDINGS, 'GRAPH_LIMIT_EXCEEDED')
            findings.append(item)
            seen_findings.add(item['id'])
            if unknown:
                unknowns.append(item['id'])

    if fingerprint['coverage'] != 'complete' or not v073.supports_version(fingerprint['loader_version']):
        finding('RUNTIME_SCOPE_UNKNOWN', 'runtime', 'warning', '当前运行指纹或加载器规则范围不完整，不能确认整体兼容性。', unknown=True)

    # Resolve graph edges without changing desired selections. Keep every edge,
    # including cycles and multiple reasons between the same two components.
    edges = defaultdict(list)
    relation_states = {}
    for relation in sorted(relation_map.values(), key=lambda r:r['relation_id']):
        applicability = _applicability(relation, fingerprint)
        state = {'applicability':applicability, 'targets':[], 'version_ok':True, 'capability':False}
        if relation['kind'] in ('hard_dependency', 'capability_requirement') and applicability == 'trusted':
            identity = relation['to_id']
            explicit_component = (relation['kind'] == 'hard_dependency' and isinstance(relation['capability'], str)
                                  and relation['capability'].startswith('component:'))
            if explicit_component:
                identity = _id(relation['capability'].removeprefix('component:'))
            if identity in component_map:
                state['targets'] = [identity]
                state['version_ok'] = version_satisfies(component_map[identity]['version_raw'], relation['requirement_raw'])
            elif explicit_component:
                state['missing_component_id'] = identity
            elif relation['capability'] is not None:
                state['capability'] = True
                candidates = [provider for provider in providers[relation['capability']]
                              if _applicability(provider, fingerprint) == 'trusted']
                minimum = relation['requirement_raw']
                if type(minimum) is not int or minimum < 0:
                    state['version_ok'] = None
                else:
                    satisfying = [p for p in candidates if p['value'] >= minimum]
                    chosen = [p for p in satisfying if component_map[p['component_id']]['release_id'] in roots]
                    if chosen:
                        state['targets'] = sorted({p['component_id'] for p in chosen})
                    elif len({component_map[p['component_id']]['release_id'] for p in satisfying}) == 1:
                        state['targets'] = sorted({p['component_id'] for p in satisfying})
                    else:
                        state['version_ok'] = False if candidates and not satisfying else None
            for target in state['targets']:
                if relation['from_id'] in component_map:
                    edges[relation['from_id']].append((target, relation['relation_id']))
        relation_states[relation['relation_id']] = state
    for values in edges.values():
        values.sort()

    # A release selects all its components; closure must account for every one.
    by_release = defaultdict(list)
    for node, component in component_map.items():
        by_release[component['release_id']].append(node)
    for values in by_release.values():
        values.sort()
    closure = set(roots)
    paths = {}
    queue = deque()
    for release in sorted(roots):
        for node in by_release[release]:
            paths[node] = ([node], [])
            queue.append(node)
    while queue:
        check()
        node = queue.popleft()
        for target, relation_id in edges[node]:
            release = component_map[target]['release_id']
            closure.add(release)
            for sibling in by_release[release]:
                if sibling not in paths:
                    nodes, ids = paths[node]
                    # Sibling inclusion follows package selection, not a made-up
                    # dependency edge; retain target as the edge endpoint.
                    paths[sibling] = (nodes + [target] + ([sibling] if sibling != target else []), ids + [relation_id])
                    queue.append(sibling)
    enable_reasons = []
    for node in sorted(paths):
        for target, relation_id in edges[node]:
            nodes, ids = paths[node]
            enable_reasons.append({'release_id':component_map[target]['release_id'],
                                   'component_ids':nodes + [target], 'relation_ids':ids + [relation_id]})
    complete = True
    for relation in sorted(relation_map.values(), key=lambda r:r['relation_id']):
        source_id, target_id = relation['from_id'], relation['to_id']
        if source_id not in component_map:
            finding('RELATION_SOURCE_UNKNOWN', 'dependency', 'warning', '关系的来源组件缺失，无法确认适用对象。', relation=relation, unknown=True)
            complete = False
            continue
        if component_map[source_id]['release_id'] not in closure:
            continue
        state = relation_states[relation['relation_id']]
        applicability = state['applicability']
        nodes = [source_id] + ([target_id] if target_id is not None else state['targets'])
        if state.get('missing_component_id'):
            nodes.append(state['missing_component_id'])
        if applicability == 'out_of_scope':
            finding('RELATION_OUT_OF_SCOPE', 'dependency', 'info', '该规则属于其他运行指纹，本次未应用。', nodes=nodes, relation=relation)
            continue
        kind = relation['kind']
        if kind in ('hard_dependency', 'capability_requirement'):
            if applicability != 'trusted':
                finding('DEPENDENCY_UNCONFIRMED', 'dependency', 'warning', '依赖声明的可信度或指纹范围不足，仅供核对，不据此自动启用或阻断。', nodes=nodes, relation=relation, unknown=True)
                complete = False
            elif state['capability'] and not state['targets']:
                definite = state['version_ok'] is False
                finding('CAPABILITY_VERSION_UNSATISFIED' if definite else 'CAPABILITY_UNKNOWN', 'dependency',
                        'blocker' if definite else 'warning', '明确的能力版本不足。' if definite else '缺少可唯一确认的能力提供声明，程序集名称和版本不能代替运行时 API。',
                        nodes=nodes, relation=relation, unknown=not definite)
                complete = False
            elif not state['targets']:
                finding('HARD_DEPENDENCY_MISSING', 'dependency', 'blocker', '当前规则明确要求的组件不在本地包库中。', nodes=nodes, relation=relation)
                complete = False
            else:
                if state['version_ok'] is not True:
                    unknown = state['version_ok'] is None
                    finding('DEPENDENCY_VERSION_UNKNOWN' if unknown else 'DEPENDENCY_VERSION_UNSATISFIED', 'dependency',
                            'warning' if unknown else 'blocker', '版本原文或范围语法无法可靠比较。' if unknown else '已锁定版本不满足这条依赖规则。',
                            nodes=nodes, relation=relation, unknown=unknown)
                    complete = False
                if any(component_map[target]['release_id'] not in roots for target in state['targets']):
                    finding('HARD_DEPENDENCY_DISABLED', 'dependency', 'blocker', '依赖组件已在包库中，但当前配置尚未启用；请核对启用闭包预览。', nodes=nodes, relation=relation)
        elif kind == 'mutual_exclusion':
            if target_id in component_map and component_map[target_id]['release_id'] in closure:
                hard = applicability == 'trusted'
                finding('MUTUAL_EXCLUSION', 'dependency', 'blocker' if hard else 'warning', '这组组件有互斥规则，请检查来源和适用范围。', nodes=nodes, relation=relation, unknown=not hard)
                if hard:
                    complete = False
        elif kind in ('soft_dependency', 'runtime_interaction'):
            finding('SOFT_DEPENDENCY' if kind == 'soft_dependency' else 'RUNTIME_INTERACTION',
                    'dependency' if kind == 'soft_dependency' else 'runtime', 'warning',
                    '这是编译引用或软依赖提示，不等于最低兼容版本。' if kind == 'soft_dependency' else '运行交互需要结合本次会话核实，不作为确定阻断。', nodes=nodes, relation=relation)
        else:
            finding('RELATION_REVIEW', 'file' if kind == 'file_conflict' else 'runtime', 'warning',
                    '保留已有关系作为核对线索，具体冲突由当前文件或组件事实另行检查。', nodes=nodes, relation=relation)

    cycles = []
    for group in _scc(set(component_map), edges, check):
        if not any(component_map[node]['release_id'] in closure for node in group):
            continue
        ids = sorted({relation_id for node in group for target, relation_id in edges[node] if target in group})
        record = {'component_ids':group, 'release_ids':sorted({component_map[node]['release_id'] for node in group}), 'relation_ids':ids}
        cycles.append(record)
        finding('DEPENDENCY_CYCLE', 'dependency', 'warning', '硬依赖形成环，闭包已保留全部关系；同时启用可满足选择约束，不推断加载顺序。', nodes=group, chain=record)

    # All reverse dependants, preserving edges through cycles. Each edge has a
    # witness path to a requested disabled component; no weak-edge deletion.
    reverse = defaultdict(list)
    for node, targets in edges.items():
        if component_map[node]['release_id'] in selected:
            for target, relation_id in targets:
                reverse[target].append((node, relation_id))
    reverse_paths, queue = {}, deque()
    for release in sorted(disable):
        for node in by_release[release]:
            reverse_paths[node] = ([node], [])
            queue.append(node)
    affected = set(disable)
    while queue:
        check()
        target = queue.popleft()
        for node, relation_id in sorted(reverse[target]):
            state = relation_states[relation_id]
            if state['capability'] and any(component_map[provider]['release_id'] in selected - affected
                                            for provider in state['targets']):
                # An explicit capability is an OR requirement. Disabling one
                # provider cannot imply removal of a consumer still satisfied
                # by another selected, confirmed provider.
                continue
            release = component_map[node]['release_id']
            affected.add(release)
            for sibling in by_release[release]:
                if sibling not in reverse_paths:
                    nodes, ids = reverse_paths[target]
                    reverse_paths[sibling] = ([sibling] + ([node] if sibling != node else []) + nodes, [relation_id] + ids)
                    queue.append(sibling)
    disable_reasons = []
    for target in sorted(reverse_paths):
        for node, relation_id in sorted(reverse[target]):
            if component_map[node]['release_id'] not in affected:
                continue
            nodes, ids = reverse_paths[target]
            disable_reasons.append({'release_id':component_map[node]['release_id'], 'component_ids':[node] + nodes, 'relation_ids':[relation_id] + ids})

    targets = defaultdict(list)
    prefixes = {'mods':'mods','plugins':'plugins','user_libs':'userlibs','user_data':'userdata','game_root':''}
    for file in file_map.values():
        if file['release_id'] in roots:
            target = '/'.join(part for part in (prefixes[file['target_root_kind']], file['relative_path']) if part)
            targets[unicodedata.normalize('NFC', target).casefold()].append(file)
    for target, entries in sorted(targets.items()):
        if len(entries) < 2:
            continue
        hashes = {entry['source_hash'] for entry in entries}
        finding('TARGET_HASH_CONFLICT' if len(hashes) > 1 else 'TARGET_SHARED_IDENTICAL', 'file',
                'blocker' if len(hashes) > 1 else 'info',
                '同一目标位置存在不同内容，不能生成写入计划。' if len(hashes) > 1 else '同一目标的内容哈希一致；这不代表可跳过所有权校验。',
                release_ids=[entry['release_id'] for entry in entries], detail={'target':target, 'file_entry_ids':sorted(e['file_entry_id'] for e in entries), 'hashes':sorted(hashes)})
    assemblies, content_ids = defaultdict(list), defaultdict(list)
    component_files = defaultdict(list)
    manifests = {}
    for file in file_map.values():
        component_files[(file['release_id'], file['source_hash'])].append(file)
        if file['release_id'] in roots and file['relative_path'].endswith('/manifest.json'):
            path = '/'.join(part for part in (prefixes[file['target_root_kind']], file['relative_path']) if part)
            manifests[path.rsplit('/', 1)[0]] = {'present':True}
    for component in component_map.values():
        if component['release_id'] not in roots:
            continue
        if component['assembly_name']:
            stage_states = {}
            relevant_files = component_files[(component['release_id'], component['artifact_hash'])]
            for file in relevant_files:
                path = '/'.join(part for part in (prefixes[file['target_root_kind']], file['relative_path']) if part)
                prediction = v073.predict(path, loader_version=fingerprint['loader_version'], options=loader_options, manifests=manifests)
                if prediction['status'] == 'excluded':
                    continue
                stage = prediction['stage'] or 'unknown'
                eligible = prediction['status'] == 'candidate' and (stage == 'UserLibs' or component['melon_info'] is not None)
                stage_states[stage] = stage_states.get(stage, True) and eligible
            if not relevant_files:
                stage_states['unknown'] = False
            for stage, eligible in stage_states.items():
                assemblies[(component['assembly_name'], stage)].append(component | {'loader_eligible':eligible})
        for item in component['content_ids']:
            content_ids[(item['framework'],item['type'],item['value'])].append(component)
    for (name, stage), entries in sorted(assemblies.items()):
        if len({entry['artifact_hash'] for entry in entries}) < 2:
            continue
        options_known = isinstance(loader_options, dict) and set(loader_options) == v073.OPTION_KEYS and all(type(v) is bool for v in loader_options.values())
        known = (v073.supports_version(fingerprint['loader_version']) and fingerprint['coverage'] == 'complete'
                 and stage != 'unknown' and options_known and all(entry['loader_eligible'] for entry in entries))
        # .NET System.Version pads omitted build/revision with -1, not zero.
        versions = [v073._version(entry['assembly_version']) for entry in entries]
        predictable = known and all(version is not None for version in versions) and len(set(versions)) > 1
        finding('ML073_ASSEMBLY_SHADOW' if predictable else 'ASSEMBLY_SHADOW_UNKNOWN', 'runtime', 'warning',
                '按 MelonLoader 0.7.3 规则预计优先加载同名高版本，实际加载以本次日志为准。' if predictable else '存在同名程序集，但当前指纹或版本不足以预测加载结果。',
                nodes=[entry['component_id'] for entry in entries], unknown=not predictable,
                detail={'assembly_name':name, 'stage':stage, 'assembly_versions':sorted({entry['assembly_version'] or '' for entry in entries})})
    for namespace, entries in sorted(content_ids.items()):
        if len({entry['artifact_hash'] for entry in entries}) < 2:
            continue
        finding('CONTENT_ID_COLLISION', 'runtime', 'warning', '同一框架和类型内存在重复内容 ID，需核实框架的处理规则。',
                nodes=[entry['component_id'] for entry in entries], unknown=True,
                detail={'framework':namespace[0], 'type':namespace[1], 'value':namespace[2]})
    findings.sort(key=lambda item:({'blocker':0,'warning':1,'info':2}[item['severity']], item['category'], item['code'], item['id']))
    result = {'schema_version':1, 'rule_version':RULE_VERSION, 'scope':scope,
              'dependency_coverage':'stored_facts_only', 'runtime_compatibility':'unknown',
              'blocking':any(item['severity'] == 'blocker' for item in findings), 'findings':findings,
              'enable_preview':{'release_ids':sorted(closure), 'added_release_ids':sorted(closure-selected), 'complete':complete, 'reason_chains':enable_reasons},
              'disable_preview':{'release_ids':sorted(disable), 'affected_release_ids':sorted(affected-disable), 'reason_chains':disable_reasons},
              'cycles':cycles, 'unknowns':sorted(unknowns)}
    if build_previews:
        for key, candidate in (('enable_preview', closure), ('disable_preview', selected - affected)):
            check()
            reviewed = _evaluate(original_components, relations, files, fingerprint, sorted(candidate), [], [], loader_options, check, False)
            result[key]['candidate_blocking'] = reviewed['blocking']
            result[key]['candidate_finding_codes'] = sorted({item['code'] for item in reviewed['findings']})
            result[key]['candidate_unknown_count'] = len(reviewed['unknowns'])
        result['disable_preview']['complete'] = True
        result['disable_preview']['scope'] = 'confirmed_applicable_dependency_edges'
    _require(len(_json(result).encode('utf-8')) <= MAX_OUTPUT_BYTES, 'GRAPH_LIMIT_EXCEEDED')
    check()
    return result
