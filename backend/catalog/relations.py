"""Build a provenance-preserving observation graph from one inventory generation.

These nodes are inventory entry IDs, not registered Component/ModRelease DTOs.
Static evidence never proves registration, compatibility or a deployment block.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from itertools import combinations
import json
from uuid import NAMESPACE_URL, uuid5


SOURCE_KINDS = {'author_manifest', 'embedded_metadata', 'loader_rule', 'runtime_log', 'user', 'heuristic'}
CONFIDENCES = {'confirmed', 'inferred', 'unknown'}
KINDS = {'hard_dependency', 'soft_dependency', 'capability_requirement', 'mutual_exclusion',
         'assembly_shadow', 'file_conflict', 'content_id_conflict', 'runtime_interaction'}
RUNTIMES = {'itemforge.runtime_api': 'ItemForgeRuntime',
            'characterforge.runtime_api': 'CharacterForgeRuntime'}


def _source(value):
    if (not isinstance(value, dict) or set(value) != {'kind', 'reference', 'observed_at'} or
            not isinstance(value['kind'], str) or
            value['kind'] not in SOURCE_KINDS or
            any(not isinstance(value[k], str) or not value[k] or len(value[k]) > 4096
                for k in ('reference', 'observed_at'))):
        raise ValueError('INVALID_RELATION_SOURCE')
    try:
        timestamp = datetime.fromisoformat(value['observed_at'].replace('Z', '+00:00'))
        if timestamp.utcoffset() != timedelta(0):
            raise ValueError('INVALID_RELATION_SOURCE')
    except (ValueError, TypeError) as exc:
        raise ValueError('INVALID_RELATION_SOURCE') from exc
    return deepcopy(value)


def _declaration(value, by_id):
    allowed = {'from_id', 'to_id', 'capability', 'kind', 'requirement_raw', 'source', 'confidence'}
    required = {'from_id', 'kind', 'requirement_raw', 'source', 'confidence'}
    if not isinstance(value, dict) or set(value) - allowed or not required <= set(value):
        raise ValueError('INVALID_RELATION_DECLARATION')
    if (not isinstance(value['kind'], str) or not isinstance(value['confidence'], str) or
            value['kind'] not in KINDS or value['confidence'] not in CONFIDENCES):
        raise ValueError('INVALID_RELATION_DECLARATION')
    if (not isinstance(value['from_id'], str) or
            (value.get('to_id') is not None and not isinstance(value['to_id'], str)) or
            value['from_id'] not in by_id or bool(value.get('to_id')) == bool(value.get('capability'))):
        raise ValueError('INVALID_RELATION_ENDPOINT')
    if value.get('to_id') and value['to_id'] not in by_id:
        raise ValueError('INVALID_RELATION_ENDPOINT')
    if value.get('capability') and (not isinstance(value['capability'], str) or len(value['capability']) > 256):
        raise ValueError('INVALID_RELATION_ENDPOINT')
    raw = value['requirement_raw']
    if raw is not None and (type(raw) not in (str, int) or len(str(raw)) > 4096):
        raise ValueError('INVALID_RELATION_REQUIREMENT')
    source = _source(value['source'])
    if source['kind'] == 'heuristic' and value['confidence'] == 'confirmed':
        raise ValueError('HEURISTIC_CANNOT_BE_CONFIRMED')
    return dict(deepcopy(value), source=source)


def build_graph(entries, *, generation, installation_id, observed_at, declarations=()):
    """Pure, deterministic graph. ``declarations`` is a trusted backend-only input.

    A confirmed author/user declaration confirms the declaration, not satisfaction.
    No declaration can supply arbitrary resolution, API minimum or fingerprint fields.
    """
    if type(generation) is not int or generation < 1 or not isinstance(installation_id, str) or not installation_id:
        raise ValueError('INVALID_GRAPH_SCOPE')
    _source({'kind': 'heuristic', 'reference': 'inventory', 'observed_at': observed_at})
    entries = list(entries)
    if len(entries) > 10000:
        raise ValueError('GRAPH_ENTRY_LIMIT')
    by_id = {row['entry_id']: row for row in entries}
    if len(by_id) != len(entries):
        raise ValueError('DUPLICATE_GRAPH_ENTRY')
    present = [row for row in entries if row.get('presence', 'present') != 'missing']
    by_path = {row['relative_path']: row for row in present}
    by_assembly = {}
    nodes, relations, diagnostics = [], [], []
    truncated = False

    def analysis(row):
        return row.get('analysis') or {}

    def assembly(row):
        return analysis(row).get('assembly_name') or row.get('assembly_name')

    def source(kind, reference):
        return {'kind': kind, 'reference': reference, 'observed_at': observed_at}

    def add(row, *, kind, source, confidence, target=None, **fields):
        nonlocal truncated
        if len(relations) >= 4096:
            if not truncated:
                diagnostics.append({'code': 'GRAPH_RELATION_LIMIT', 'limit': 4096,
                                    'source': globals_source,
                                    'fingerprint_scope': {'installation_id': installation_id, 'generation': generation}})
            truncated = True
            return
        edge = dict(from_id=row['entry_id'], to_id=target['entry_id'] if target else None,
                    kind=kind, source=_source(source), confidence=confidence,
                    fingerprint_scope={'installation_id': installation_id, 'generation': generation,
                                       'from_sha256': row.get('sha256'),
                                       'to_sha256': target.get('sha256') if target else None}, **fields)
        stable = json.dumps(edge, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        edge['relation_id'] = str(uuid5(NAMESPACE_URL, 'psmm.inventory-relation.v1:' + stable))
        relations.append(edge)

    for row in entries:
        data = analysis(row)
        nodes.append({'entry_id': row['entry_id'], 'relative_path': row['relative_path'],
                      'sha256': row.get('sha256'), 'presence': row.get('presence', 'present'),
                      'status': row.get('status', 'unknown'), 'roles': sorted(set(data.get('roles', ['unknown']))),
                      'content_ids': deepcopy(data.get('content_ids', [])),
                      'assembly_name': assembly(row),
                      'assembly_version': data.get('assembly_version', row.get('assembly_version')),
                      'registration_status': 'unknown', 'dependency_coverage': 'unknown',
                      'prediction': deepcopy(row.get('prediction', {})),
                      'fingerprint_scope': {'installation_id': installation_id, 'generation': generation,
                                            'sha256': row.get('sha256')}})
        if row.get('presence', 'present') != 'missing' and assembly(row):
            by_assembly.setdefault(assembly(row), []).append(row)

    globals_source = source('heuristic', 'inventory_graph/limits')
    content_ids = {}
    for row in present:
        if truncated:
            break
        data = analysis(row)
        refs = (row.get('metadata') or {}).get('references', [])
        for index, ref in enumerate(refs):
            if truncated:
                break
            name = ref.get('name')
            if not isinstance(name, str) or not name:
                continue
            targets = by_assembly.get(name, [])
            for target in targets or [None]:
                if truncated:
                    break
                add(row, kind='soft_dependency', target=target, confidence='inferred',
                    source=source('embedded_metadata', f"{row['relative_path']}#AssemblyRef/{index}"),
                    capability=None if target else 'assembly:' + name,
                    requirement_raw=ref.get('version'), requirement_semantics='compile_reference_only',
                    resolution='candidate' if target else 'unresolved', compatibility='unknown')
        for req in data.get('requirements', []):
            if truncated:
                break
            capability = req['capability']
            candidates = by_assembly.get(RUNTIMES.get(capability), [])
            # An opaque DLL may still provide this API; missing is only a static name search result.
            unknown_entries = any(other.get('status') not in ('managed', 'native')
                                  and other['relative_path'].lower().endswith('.dll') for other in present)
            add(row, kind='capability_requirement', source=req['source'], confidence=req['confidence'],
                capability=capability, framework=req['framework'], minimum=req['minimum'],
                requirement_raw=req['requirement_raw'], candidate_ids=[c['entry_id'] for c in candidates],
                resolution='candidate' if candidates else 'unknown' if unknown_entries else 'missing',
                compatibility='unknown', resolution_scope='static_assembly_name_search')
        seen = set()
        for content in data.get('content_ids', []):
            key = (content['framework'], content['type'], content['value'])
            if key not in seen:
                content_ids.setdefault(key, []).append((row, content))
                seen.add(key)
        for path in row.get('shadowed_by', []):
            if truncated:
                break
            target = by_path.get(path)
            prediction = row.get('prediction') or {}
            if target is not None and prediction.get('rule_version'):
                add(row, kind='assembly_shadow', target=target, confidence='inferred',
                    source=source('loader_rule', str(prediction['rule_version']) + '/AssemblyVersion'),
                    requirement_raw=row.get('assembly_version'), resolution='predicted',
                    registration_status='unknown')
        for diagnostic in data.get('diagnostics', []):
            diagnostics.append({'entry_id': row['entry_id'], 'detail': deepcopy(diagnostic),
                                'fingerprint_scope': {'installation_id': installation_id,
                                                      'generation': generation, 'sha256': row.get('sha256')}})

    for key, values in content_ids.items():
        if truncated:
            break
        for (left, left_id), (right, right_id) in combinations(values, 2):
            add(left, kind='content_id_conflict', target=right, confidence='confirmed',
                source=left_id.get('source') or source('embedded_metadata', left['relative_path'] + '#content_ids'),
                requirement_raw=key[2], namespace={'framework': key[0], 'type': key[1]},
                target_source=_source(right_id.get('source') or source('embedded_metadata', right['relative_path'] + '#content_ids')),
                resolution='static_collision', runtime_effect='unknown')
            if truncated:
                break

    declarations = list(declarations)
    if len(declarations) > 1000:
        raise ValueError('GRAPH_DECLARATION_LIMIT')
    for value in declarations:
        declaration = _declaration(value, by_id)
        row = by_id[declaration.pop('from_id')]
        target = by_id.get(declaration.pop('to_id', None))
        add(row, target=target, declaration=True, resolution='not_evaluated', **declaration)
    return {'schema_version': 1, 'graph_kind': 'inventory_observation',
            'installation_id': installation_id, 'generation': generation, 'observed_at': observed_at,
            'nodes': nodes, 'relations': relations, 'diagnostics': diagnostics,
            'coverage': 'partial' if truncated else 'complete',
            'dependency_coverage': 'unknown', 'plan_blocking_evaluated': False}
