"""Fixed-source log previews; immutable selected evidence, never client paths."""
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
from uuid import NAMESPACE_URL, uuid4, uuid5

from backend.diagnostics.service import user_log_path
from backend.games.probably_stolen.layout import identity, safe_path
from backend.games.probably_stolen.update_state import game_files_modified_at, timestamp
from .gateway import AIError
from .redaction import redact
from .tool_policy import ToolScope

LOG_BYTES = 8192
CONTEXT_BYTES = 6000
ERROR_LINE = re.compile(r'\b(error|exception|fatal|failed|failure)\b|错误|异常|失败', re.I)
STARTUP_LINE = re.compile(r'^(?:\[[^\]\r\n]+\]\s*)*(?:Initialize engine version:|Mono path\[|audit: (?:game |[A-Za-z0-9_.-]+ v)|MelonLoader v\S+|Game (?:Name|Type|Version):|Unity Version:)', re.I)
STARTUP_BANNER = re.compile(r'^(?:\[[^\]\r\n]+\]\s*)*(?:Initialize engine version:|MelonLoader v\S+)', re.I)
GENERATION_LINE = re.compile(r'^(?:\[[^\]\r\n]+\]\s*)*\[Il2CppAssemblyGenerator\]', re.I)


def startup_indices(lines):
    """Reserve a small part of the existing budget for the latest visible startup."""
    banners = [i for i, line in enumerate(lines) if STARTUP_BANNER.match(line)]
    start = banners[-1] if banners else 0
    environment = [i for i in range(start, len(lines)) if STARTUP_LINE.match(lines[i])]
    generation = [i for i in range(start, len(lines)) if GENERATION_LINE.match(lines[i])]
    selected, used = set(), 0
    for index in environment + list(reversed(generation)):
        cost = len(lines[index].encode('utf-8')) + 1
        if len(selected) < 48 and used + cost <= 2048:
            selected.add(index)
            used += cost
    return selected, used


def clean_log_text(text, secrets=(), manager_root=None):
    """One privacy projection for notebook, selected evidence and AI previews."""
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    text = redact(text, secrets, partial=True)
    if manager_root is not None:
        for value in (str(manager_root), str(manager_root).replace('\\', '/')):
            text = text.replace(value, '[管理器数据目录]')
    text = re.sub(r'(?i)\b[A-Z]:[\\/]Users[\\/][^\\/\r\n]+', '%USERPROFILE%', text)
    text = re.sub(r'\bowner=\S+', 'owner=[已隐藏]', text)
    text = re.sub(r'\bhash(?:=| )[^\s,]+', 'hash=[已隐藏]', text)
    text = re.sub(r'(?i)\b(steam(?:id|_id|userid|accountid)|ownerid)(\s*[:=]\s*)[0-9]+', r'\1\2[已隐藏]', text)
    text = re.sub(r'\b7656119\d{10}\b', '[Steam 账号已隐藏]', text)
    return text


class LogDiagnostics:
    def __init__(self, context):
        self.context = context
        self._snapshots = OrderedDict()
        self._lock = threading.RLock()

    def _installation(self, installation_id):
        if not installation_id:
            return None
        self.context.ai.tools.validate_scope(ToolScope(installation_id))
        db, installation = self.context.logs._reader(installation_id)
        db.close()
        root = Path(installation['canonical_root'])
        if identity(root) != installation['file_identity']:
            raise AIError('AI_LOG_SOURCE_INVALID')
        if root == self.context.paths.root or root in self.context.paths.root.parents or self.context.paths.root in root.parents:
            raise AIError('AI_LOG_SOURCE_INVALID')
        return root

    def _sources(self, installation_id):
        self.context.paths.check_owned_paths()
        root = self._installation(installation_id)
        records = [('manager', self.context.paths.root, 'logs/psmodmanager.log', '管理器日志', True)]
        for number in (1, 2):
            relative = f'logs/psmodmanager.log.{number}'
            if (self.context.paths.root / relative).exists():
                records.append(('manager', self.context.paths.root, relative, f'管理器日志 · 上次 {number}', False))
        warnings = []
        if root is not None:
            db, installation = self.context.logs._reader(installation_id)
            db.close()
            paths, errors = self.context.logs.source_specs(installation)
            warnings += ['部分游戏归档未能列出。'] if errors else []
            for spec in paths:
                relative, kind = spec['relative_path'], spec['kind']
                records.append(('game', spec['root'], relative, '游戏日志 · ' + relative.split('/')[-1], kind == 'latest'))
            # Reuse only explicitly registered Mod log sources, never walk UserData.
            for row in self.context.logs.sources(installation_id)['sources']:
                if row['kind'] == 'user':
                    relative = user_log_path(row['relative_path'])
                    records.append(('mod', root, relative, 'Mod 日志 · ' + relative, False))
        sources = {}
        for kind, base, relative, label, default in records:
            source_id = str(uuid5(NAMESPACE_URL, f'PSModManager.logs:{installation_id}:{kind}:{relative}'))
            status, size = 'available', 0
            try:
                size = safe_path(base, relative).stat().st_size
            except FileNotFoundError:
                status = 'missing'
            except (OSError, ValueError):
                status = 'unreadable'
            sources[source_id] = dict(source_id=source_id, kind=kind, label=label, relative_path=relative,
                status=status, bytes_total=size, default=default and status == 'available', _root=base, _game_root=root)
        return sources, warnings

    def sources(self, installation_id=None):
        sources, warnings = self._sources(installation_id)
        reconciliation = None
        native = getattr(self.context, 'native_mods', None)
        if installation_id and native is not None:
            db, installation = self.context.logs._reader(installation_id)
            db.close()
            reconciliation = native.snapshot(installation).get('runtime_reconciliation')
        return dict(sources=[{key: value for key, value in row.items() if not key.startswith('_')} for row in sources.values()],
                    warnings=warnings, runtime_reconciliation=reconciliation)

    def _secrets(self):
        ai = self.context.ai
        config = ai._config()
        if config['model']:
            ai.credentials.get(config)
        return tuple(ai.credentials.secrets)

    def _clean(self, text, secrets):
        return clean_log_text(text,secrets,self.context.paths.root)

    def _read_window(self, row, secrets, *, maximum, end=None, include_startup=False):
        root, relative = row['_root'], row['relative_path']
        try:
            target = safe_path(root, relative)
            with target.open('rb', buffering=0) as stream:
                before = os.fstat(stream.fileno())
                path_info = safe_path(root, relative).stat()
                if (before.st_dev, before.st_ino) != (path_info.st_dev, path_info.st_ino):
                    raise ValueError('changed_source')
                header = stream.read(3)
                encoding = 'utf-16-le' if header.startswith(b'\xff\xfe') else 'utf-16-be' if header.startswith(b'\xfe\xff') else 'utf-8-sig'
                end = before.st_size if end is None else min(end, before.st_size)
                offset = max(0, end - maximum)
                if encoding.startswith('utf-16'):
                    offset += offset % 2
                startup_text = None
                if include_startup and offset and row['kind'] == 'game':
                    stream.seek(0)
                    raw_startup = stream.read(min(before.st_size, 32768))
                    startup_text = raw_startup.decode(encoding, errors='replace').lstrip('\ufeff')
                    if len(raw_startup) < before.st_size:
                        startup_text = startup_text.rsplit('\n', 1)[0] if '\n' in startup_text else ''
                stream.seek(offset)
                raw = stream.read(end - offset)
                after = os.fstat(stream.fileno())
                final = safe_path(root, relative).stat()
                if ((before.st_dev, before.st_ino) != (final.st_dev, final.st_ino) or after.st_size < before.st_size
                        or after.st_size == before.st_size and after.st_mtime_ns != before.st_mtime_ns):
                    raise ValueError('changed_source')
                original_offset = offset
                # Find the byte boundary before decoding, so UTF-8 continuation
                # bytes cannot corrupt the cursor for the preceding page.
                if offset:
                    newline = b'\n\x00' if encoding == 'utf-16-le' else b'\x00\n' if encoding == 'utf-16-be' else b'\n'
                    position = raw.find(newline)
                    if position >= 0:
                        consumed = position + len(newline)
                        offset += consumed
                        raw = raw[consumed:]
                    else:
                        raw = b''
                text = raw.decode(encoding, errors='replace').lstrip('\ufeff')
                # A cut first line may begin in the middle of a secret assignment.
                game_root = row.get('_game_root') or root
                def clean_window(value):
                    if row['kind'] != 'manager':
                        for base,label in ((root,'[游戏日志目录]'),(game_root,'[游戏目录]')):
                            value=value.replace(str(base),label).replace(str(base).replace('\\','/'),label)
                    return self._clean(value,secrets)
                text=clean_window(text)
                if startup_text is not None:
                    startup_text=clean_window(startup_text)
                game_modified = game_files_modified_at(game_root) if row['kind'] != 'manager' else None
                freshness = ('predates_game_files' if before.st_mtime < game_modified else 'not_older_than_game_files') if game_modified is not None else 'unknown'
                return dict(source_id=row['source_id'], label=row['label'], relative_path=relative, text=text,
                    bytes_total=before.st_size, byte_start=offset, byte_end=end, bytes_read=end-original_offset,
                    previous_before=offset if offset > 0 and offset < end else original_offset if original_offset > 0 else None,
                    truncated=offset > 0 or end < before.st_size, line_origin='本页',
                    line_count=len(text.splitlines()), encoding=encoding,
                    skipped_long_line=bool(original_offset and not raw),
                    source_modified_at=timestamp(before.st_mtime),
                    game_files_modified_at=timestamp(game_modified) if game_modified is not None else None,
                    freshness=freshness,
                    freshness_message=('这份日志早于当前游戏文件，可能来自更新前。运行更新后的游戏再刷新日志；当前分析仅供历史参考。'
                                       if freshness == 'predates_game_files' else ''),
                    **({'startup_window': startup_text} if include_startup else {}),
                    captured_at=datetime.now(timezone.utc).isoformat())
        except (OSError, ValueError):
            raise AIError('AI_LOG_UNREADABLE') from None

    def read(self, installation_id, source_id, before=None):
        sources, _ = self._sources(installation_id)
        if source_id not in sources:
            raise AIError('AI_LOG_SOURCE_INVALID')
        return self._read_window(sources[source_id], self._secrets(), maximum=32768, end=before)

    def _read(self, row, secrets):
        scan = self._read_window(row, secrets, maximum=262144, include_startup=True)
        startup_window = scan.pop('startup_window')
        lines = scan['text'].splitlines()
        errors = [index for index, line in enumerate(lines) if ERROR_LINE.search(line)]
        indices, used = startup_indices(lines) if row['kind'] == 'game' else (set(), 0)
        startup = None
        # A log larger than the tail window still needs its boot environment.
        # Keep it separately labelled; its line numbers are not tail offsets.
        if startup_window is not None and not any(STARTUP_BANNER.match(lines[i]) for i in indices):
            head_lines = startup_window.splitlines()
            head_indices, head_used = startup_indices(head_lines)
            if head_indices:
                ordered_head = sorted(head_indices)
                startup = dict(text='\n'.join(head_lines[i] for i in ordered_head),
                               line_numbers=[i + 1 for i in ordered_head], line_origin='文件开头（日志记载）')
                used += head_used
        # Prefer the latest error with adjacent stack/context lines, while retaining
        # complete lines and exposing the exact excerpt that the model receives.
        groups = [range(max(0, index-3), min(len(lines), index+5)) for index in reversed(errors)] if errors else [range(len(lines)-1, -1, -1)]
        for group in groups:
            for index in group:
                if index in indices:
                    continue
                cost = len(lines[index].encode('utf-8')) + 1
                if len(indices) + (len(startup['line_numbers']) if startup else 0) >= 240 or used + cost > LOG_BYTES:
                    continue
                indices.add(index)
                used += cost
            if len(indices) >= 240 or used >= LOG_BYTES:
                break
        ordered = sorted(indices)
        return dict(scan, text='\n'.join(lines[index] for index in ordered),
            line_numbers=[index+1 for index in ordered], line_count=len(ordered),
            line_origin='本次扫描片段', selection_mode='error_context' if errors else 'tail',
            startup_context=startup,
            error_matches=len(errors), scanned_bytes=scan['bytes_read'],
            truncated=scan['truncated'] or len(ordered) < len(lines))

    def _context(self, installation_id, secrets):
        if not installation_id:
            return {}, []
        tools = self.context.ai.tools
        scope = ToolScope(installation_id)
        result, warnings = {}, []
        if getattr(tools, 'native', None) is not None:
            for name, label, arguments in (
                ('get_installation_summary', 'installation', {}),
                ('get_mod_workspace', 'mods', {'limit': 20}),
                ('get_runtime_reconciliation', 'runtime_reconciliation', {'limit': 20}),
            ):
                try:
                    result[label] = json.loads(tools.execute(name, json.dumps(arguments), scope, lambda: None, secrets))['data']
                except (AIError, KeyError, ValueError):
                    warnings.append({'installation':'游戏信息暂不可读。','mods':'Mod 状态暂不可读。',
                                     'runtime_reconciliation':'本次启动对账暂不可读。'}[label])
            # Drop whole entries, not JSON bytes; retain counts and an explicit cursor.
            clipped = False
            for label in ('mods', 'runtime_reconciliation'):
                value = result.get(label, {})
                while value.get('items') and len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > CONTEXT_BYTES:
                    value['items'].pop()
                    value['truncated'] = True
                    value['next_offset'] = len(value['items'])
                    clipped = True
                if value.get('enabled_ids') and len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > CONTEXT_BYTES:
                    value['enabled_ids_total'] = len(value['enabled_ids'])
                    while value['enabled_ids'] and len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > CONTEXT_BYTES:
                        value['enabled_ids'].pop()
                        value['enabled_ids_truncated'] = clipped = True
            if clipped:
                warnings.append('上下文过长，模组明细已缩减；完整数量保留在摘要中，可继续分页查询。')
            return result, warnings
        for name, label, arguments in (
            ('get_installation_summary', 'installation', {}),
            ('get_installed_mods', 'mods', {}),
        ):
            try:
                if label == 'mods':
                    value = self.context.mod_workspace.list(installation_id, check=lambda: None)
                    entries = value['items']
                    result[label] = dict(items=[{key: self._clean(str(row[key]), secrets) if row.get(key) is not None else None
                        for key in ('id','release_id','display_name', 'version_raw', 'state','source')} for row in entries[:200]], total=len(entries),
                        snapshot_id=value['snapshot_id'],
                        enabled_count=len(value['enabled_ids']), truncated=len(entries) > 200,
                        scope='当前 Mod 列表：已发现的游戏 Mod 与包库版本')
                    if result[label]['truncated']:
                        warnings.append('Mod 上下文仅包含前 200 项；完整数量保留在摘要中。')
                else:
                    value = json.loads(tools.execute(name, json.dumps(arguments), scope, lambda: None, secrets))['data']
                    result[label] = value
            except (AIError, KeyError, ValueError):
                warnings.append('游戏信息暂不可读。' if label == 'installation' else 'Mod 状态暂不可读。')
        # Never cut JSON into an invalid or misleading partial inventory.
        if 'mods' in result:
            clipped = False
            while result['mods']['items'] and len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > CONTEXT_BYTES:
                result['mods']['items'].pop()
                result['mods']['truncated'] = clipped = True
            if clipped:
                warnings.append('上下文过长，Mod 列表已缩减；可在对话中继续查询。')
        return result, warnings

    def preview(self, installation_id, source_ids, include_context=True):
        if not 1 <= len(source_ids) <= 3 or len(set(source_ids)) != len(source_ids):
            raise AIError('AI_LOG_SOURCE_INVALID')
        sources, warnings = self._sources(installation_id)
        if any(source_id not in sources for source_id in source_ids):
            raise AIError('AI_LOG_SOURCE_INVALID')
        secrets = self._secrets()
        logs = [self._read(sources[source_id], secrets) for source_id in source_ids]
        context, notes = self._context(installation_id, secrets) if include_context else ({}, [])
        snapshot = dict(snapshot_id=str(uuid4()), installation_id=installation_id, include_context=include_context,
                        logs=logs, context=context, warnings=warnings + notes,
                        runtime_reconciliation=context.get('runtime_reconciliation'),
                        captured_at=datetime.now(timezone.utc).isoformat(), redacted=True)
        with self._lock:
            self._snapshots[snapshot['snapshot_id']] = snapshot
            while len(self._snapshots) > 12:
                self._snapshots.popitem(last=False)
        return deepcopy(snapshot)

    def send(self, request_id, snapshot_id, question, session_id=None):
        with self._lock:
            snapshot = self._snapshots.get(snapshot_id)
            if snapshot is None:
                raise AIError('AI_DIAGNOSTIC_EXPIRED')
        self._installation(snapshot['installation_id'])
        if not any(log['text'].strip() for log in snapshot['logs']):
            raise AIError('AI_LOG_EMPTY')
        sections = ['诊断资料快照：' + snapshot['captured_at']]
        if snapshot['context']:
            sections.append('游戏与 Mod 上下文：\n' + json.dumps(snapshot['context'], ensure_ascii=False))
        for log in snapshot['logs']:
            sections.append(f"日志：{log['label']} ({log['relative_path']})；文件最后写入：{log['source_modified_at']}；"
                            + (f"当前游戏文件最近修改：{log['game_files_modified_at']}；" if log['game_files_modified_at'] else '')
                            + (log['freshness_message'] + '\n' if log['freshness_message'] else '')
                            + '读取时间不是游戏运行时间；文件时间只能辅助判断，不能证明此日志对应当前版本。\n'
                            + '行号为本次扫描片段内行号；'
                            + ('错误及相邻上下文。' if log['selection_mode'] == 'error_context' else '最近日志。')
                            + (f"资料已截断：扫描末尾 {log['scanned_bytes']} 字节，未选行不包含在内。" if log['truncated'] else '当前完整内容。')
                            + '\n' + '\n'.join(f'{number}: {line}' for number, line in zip(log['line_numbers'], log['text'].splitlines())))
            if log.get('startup_context'):
                startup = log['startup_context']
                sections.append('同一日志文件开头的启动信息（历史声明，不代表当前运行环境）：\n'
                    + '\n'.join(f'{number}: {line}' for number, line in zip(startup['line_numbers'], startup['text'].splitlines())))
        if snapshot['warnings']:
            sections.append('资料范围：' + '；'.join(snapshot['warnings']))
        prompt = '\n\n'.join(sections)
        if len(prompt.encode('utf-8')) > 40000:
            # Line numbering must not silently expand a bounded preview past the send budget.
            raise AIError('AI_INPUT_LIMIT')
        return self.context.ai.send(request_id, question, session_id=session_id,
            installation_id=snapshot['installation_id'] if snapshot['include_context'] else None,
            diagnostic=dict(snapshot_id=snapshot_id, prompt=prompt))
