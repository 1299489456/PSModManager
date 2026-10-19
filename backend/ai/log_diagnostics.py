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

from backend.diagnostics.service import LogService, user_log_path
from backend.games.probably_stolen.layout import identity, safe_path
from .gateway import AIError
from .redaction import redact
from .tool_policy import ToolScope

LOG_BYTES = 8192
CONTEXT_BYTES = 6000
ERROR_LINE = re.compile(r'\b(error|exception|fatal|failed|failure)\b|错误|异常|失败', re.I)


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
            paths, errors = LogService._paths(root, ())
            warnings += ['部分 MelonLoader 归档未能列出。'] if errors else []
            for relative, kind in paths:
                records.append(('game', root, relative, '游戏 / MelonLoader · ' + relative.split('/')[-1], kind == 'latest'))
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
                status=status, bytes_total=size, default=default and status == 'available', _root=base)
        return sources, warnings

    def sources(self, installation_id=None):
        sources, warnings = self._sources(installation_id)
        return dict(sources=[{key: value for key, value in row.items() if key != '_root'} for row in sources.values()], warnings=warnings)

    def _secrets(self):
        ai = self.context.ai
        config = ai._config()
        if config['model']:
            ai.credentials.get(config)
        return tuple(ai.credentials.secrets)

    def _clean(self, text, secrets):
        text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
        text = redact(text, secrets, partial=True)
        text = re.sub(r'(?i)\b[A-Z]:[\\/]Users[\\/][^\\/\s]+', '%USERPROFILE%', text)
        # Local root names are not useful to a remote diagnostic model.
        for value in (str(self.context.paths.root), str(self.context.paths.root).replace('\\', '/')):
            text = text.replace(value, '[管理器数据目录]')
        return text

    def _read_window(self, row, secrets, *, maximum, end=None):
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
                stream.seek(offset)
                raw = stream.read(end - offset)
                after = os.fstat(stream.fileno())
                final = safe_path(root, relative).stat()
                if (before.st_dev, before.st_ino) != (final.st_dev, final.st_ino) or after.st_size < before.st_size:
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
                text = self._clean(text, secrets)
                if row['kind'] != 'manager':
                    text = text.replace(str(root), '[游戏目录]').replace(str(root).replace('\\', '/'), '[游戏目录]')
                return dict(source_id=row['source_id'], label=row['label'], relative_path=relative, text=text,
                    bytes_total=before.st_size, byte_start=offset, byte_end=end, bytes_read=end-original_offset,
                    previous_before=offset if offset > 0 and offset < end else original_offset if original_offset > 0 else None,
                    truncated=offset > 0 or end < before.st_size, line_origin='本页',
                    line_count=len(text.splitlines()), encoding=encoding,
                    skipped_long_line=bool(original_offset and not raw),
                    captured_at=datetime.now(timezone.utc).isoformat())
        except (OSError, ValueError):
            raise AIError('AI_LOG_UNREADABLE') from None

    def read(self, installation_id, source_id, before=None):
        sources, _ = self._sources(installation_id)
        if source_id not in sources:
            raise AIError('AI_LOG_SOURCE_INVALID')
        return self._read_window(sources[source_id], self._secrets(), maximum=32768, end=before)

    def _read(self, row, secrets):
        scan = self._read_window(row, secrets, maximum=262144)
        lines = scan['text'].splitlines()
        errors = [index for index, line in enumerate(lines) if ERROR_LINE.search(line)]
        indices = set()
        used = 0
        # Prefer the latest error with adjacent stack/context lines, while retaining
        # complete lines and exposing the exact excerpt that the model receives.
        groups = [range(max(0, index-3), min(len(lines), index+5)) for index in reversed(errors)] if errors else [range(len(lines)-1, -1, -1)]
        for group in groups:
            for index in group:
                if index in indices:
                    continue
                cost = len(lines[index].encode('utf-8')) + 1
                if len(indices) >= 240 or used + cost > LOG_BYTES:
                    continue
                indices.add(index)
                used += cost
            if len(indices) >= 240 or used >= LOG_BYTES:
                break
        ordered = sorted(indices)
        return dict(scan, text='\n'.join(lines[index] for index in ordered),
            line_numbers=[index+1 for index in ordered], line_count=len(ordered),
            line_origin='本次扫描片段', selection_mode='error_context' if errors else 'tail',
            error_matches=len(errors), scanned_bytes=scan['bytes_read'],
            truncated=scan['truncated'] or len(ordered) < len(lines))

    def _context(self, installation_id, secrets):
        if not installation_id:
            return {}, []
        tools = self.context.ai.tools
        scope = ToolScope(installation_id)
        result, warnings = {}, []
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
                warnings.append('游戏与 Loader 信息暂不可读。' if label == 'installation' else 'Mod 状态暂不可读。')
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
            sections.append('游戏、Loader 与 Mod 上下文：\n' + json.dumps(snapshot['context'], ensure_ascii=False))
        for log in snapshot['logs']:
            sections.append(f"日志：{log['label']} ({log['relative_path']})；行号为本次扫描片段内行号；"
                            + ('错误及相邻上下文。' if log['selection_mode'] == 'error_context' else '最近日志。')
                            + (f"资料已截断：扫描末尾 {log['scanned_bytes']} 字节，未选行不包含在内。" if log['truncated'] else '当前完整内容。')
                            + '\n' + '\n'.join(f'{number}: {line}' for number, line in zip(log['line_numbers'], log['text'].splitlines())))
        if snapshot['warnings']:
            sections.append('资料范围：' + '；'.join(snapshot['warnings']))
        prompt = '\n\n'.join(sections)
        if len(prompt.encode('utf-8')) > 40000:
            # Line numbering must not silently expand a bounded preview past the send budget.
            raise AIError('AI_INPUT_LIMIT')
        return self.context.ai.send(request_id, question, session_id=session_id,
            installation_id=snapshot['installation_id'] if snapshot['include_context'] else None,
            diagnostic=dict(snapshot_id=snapshot_id, prompt=prompt))
