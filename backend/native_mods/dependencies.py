"""Stable dependency ordering and explicit closure/reverse-impact previews."""
import heapq
from .errors import NativeModError, issue
from .paths import valid_id


def ordered_ids(ids):
    if not isinstance(ids, list) or len(ids) > 1024 or any(not valid_id(x) for x in ids) or len(set(ids)) != len(ids):
        raise NativeModError('NATIVE_INVALID_ORDER')
    return list(ids)


def analyze(mods, ids, rules=()):
    ids = ordered_ids(ids)
    installed = {m['id']: m for m in mods}
    selected = set(ids)
    issues, edges = [], {x: set() for x in ids}
    for ident in ids:
        mod = installed.get(ident)
        if mod is None or not mod.get('installed'):
            issues.append(issue('NATIVE_MOD_MISSING', ident))
            continue
        issues.extend(x for x in mod.get('issues', []) if x.get('severity') == 'error')
        for dep in mod.get('prerequisites', []):
            if dep not in selected:
                issues.append(issue('NATIVE_DEPENDENCY_MISSING' if dep not in installed else 'NATIVE_DEPENDENCY_DISABLED', ident, dependency=dep))
            else:
                edges[dep].add(ident)
    if not isinstance(rules, (list, tuple)) or len(rules) > 2048:
        raise NativeModError('NATIVE_INVALID_RULE')
    for rule in rules:
        if not isinstance(rule, dict) or not valid_id(rule.get('before')) or not valid_id(rule.get('after')):
            raise NativeModError('NATIVE_INVALID_RULE')
        if rule['before'] in selected and rule['after'] in selected:
            edges[rule['before']].add(rule['after'])
    position = {v: i for i, v in enumerate(ids)}
    indegree = {v: 0 for v in ids}
    for targets in edges.values():
        for target in targets:
            indegree[target] += 1
    queue = [position[v] for v in ids if indegree[v] == 0]
    heapq.heapify(queue)
    result = []
    while queue:
        node = ids[heapq.heappop(queue)]
        result.append(node)
        for target in sorted(edges[node], key=position.get):
            indegree[target] -= 1
            if indegree[target] == 0:
                heapq.heappush(queue, position[target])
    if len(result) != len(ids):
        # Iterative DFS avoids recursion failures on large dependency chains.
        remaining = {n for n in ids if indegree[n] > 0}
        stack, active, visited = [], {}, set()
        cycle = []
        for start in ids:
            if start not in remaining or start in visited:
                continue
            stack = [(start, iter(sorted(edges[start], key=position.get)))]
            active[start] = 0
            while stack and not cycle:
                node, iterator = stack[-1]
                target = next(iterator, None)
                if target is None:
                    stack.pop(); active.pop(node, None); visited.add(node)
                elif target in active:
                    cycle = [x[0] for x in stack[active[target]:]] + [target]
                elif target not in visited:
                    active[target] = len(stack); stack.append((target, iter(sorted(edges[target], key=position.get))))
            if cycle:
                break
        issues.append(issue('NATIVE_DEPENDENCY_CYCLE', cycle=cycle))
        result = ids  # Never expose a partial plan as applicable.
    for before, targets in edges.items():
        for after in targets:
            if position[before] > position[after]:
                issues.append(issue('NATIVE_ORDER_WARNING', after, severity='warning', before=before))
    return dict(enabled_ids=ids, sorted_ids=result, issues=issues,
                blocking=any(x['severity'] == 'error' for x in issues),
                approval_required_ids=[x for x in ids if installed.get(x, {}).get('approval', {}).get('state') != 'approved'])


def enable_closure(mods, ids, ident):
    ids = ordered_ids(ids)
    if not valid_id(ident):
        raise NativeModError('NATIVE_INVALID_ID')
    found = {m['id']: m for m in mods}
    pending, seen, result = [ident], set(ids), list(ids)
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node); result.append(node)
        pending.extend(reversed(found.get(node, {}).get('prerequisites', [])))
    return analyze(mods, result)


def disable_closure(mods, ids, ident):
    ids = ordered_ids(ids)
    affected = {ident}
    for _ in range(len(ids)):
        added = {m['id'] for m in mods if m['id'] in ids and any(d in affected for d in m.get('prerequisites', []))}
        if added <= affected:
            break
        affected |= added
    return {'affected_ids': [x for x in ids if x in affected], 'enabled_ids': [x for x in ids if x not in affected]}
