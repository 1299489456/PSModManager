"""Bounded byte-accurate log tailing. State is a cursor, never a path grant."""
from __future__ import annotations

import base64
import codecs
import hashlib
import os
from pathlib import Path, PureWindowsPath
import uuid

from backend.games.probably_stolen.layout import approved_root, safe_path, signature


class Tailer:
    def __init__(self, root, relative, *, state=None, chunk_bytes=65536,
                 max_line_bytes=16384, max_read_bytes=262144, max_records=1024, cancelled=None):
        self.root = approved_root(root)
        win = PureWindowsPath(relative)
        if (not isinstance(relative, str) or not relative or win.is_absolute() or win.drive
                or '\\' in relative or any(p in ('', '.', '..') or ':' in p for p in relative.split('/'))):
            raise ValueError('invalid_log_relative_path')
        for value in (chunk_bytes, max_line_bytes, max_read_bytes):
            if type(value) is not int or not 1 <= value <= 1048576:
                raise ValueError('invalid_log_read_limit')
        if type(max_records) is not int or not 1 <= max_records <= 4096:
            raise ValueError('invalid_log_record_limit')
        self.relative = relative
        self.chunk_bytes, self.max_line_bytes, self.max_read_bytes = chunk_bytes, max_line_bytes, max_read_bytes
        self.max_records = max_records
        self.cancelled = cancelled or (lambda: False)
        self.epoch = str(uuid.uuid4())
        self.file_identity = None
        self.offset = 0
        self.pending = b''
        self.decoder = codecs.getincrementaldecoder('utf-8')('replace')
        self.ansi = ''
        self.fragmented = False
        self.invalidated = False
        self._snapshot_cache = None
        self.head = self.tail = b''
        if state is not None:
            self._restore(state)

    def _restore(self, state):
        try:
            epoch = str(uuid.UUID(state['epoch']))
            offset = state['offset']
            pending = base64.b64decode(state['pending'], validate=True)
            decoder = base64.b64decode(state['decoder'], validate=True)
            head = base64.b64decode(state['head'], validate=True)
            tail = base64.b64decode(state['tail'], validate=True)
            if (type(offset) is not int or offset < len(pending) or offset > 2**63 - 1
                    or len(pending) >= self.max_line_bytes or len(decoder) > 3
                    or len(head) > min(offset, 256) or len(tail) > min(offset, 256)
                    or state['ansi'] not in ('', 'esc', 'csi', 'osc', 'osc_esc')
                    or type(state['fragmented']) is not bool
                    or not isinstance(state['file_identity'], (str, type(None)))):
                raise ValueError()
            self.epoch, self.offset, self.pending = epoch, offset, pending
            self.file_identity = state['file_identity']
            self.head, self.tail = head, tail
            self.decoder.setstate((decoder, 0))
            self.ansi, self.fragmented = state['ansi'], state['fragmented']
            self.invalidated = state.get('invalidated', False)
            if type(self.invalidated) is not bool: raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise ValueError('invalid_log_cursor') from None

    def state(self):
        encode = lambda b: base64.b64encode(b).decode('ascii')
        return dict(epoch=self.epoch, file_identity=self.file_identity, offset=self.offset,
                    pending=encode(self.pending), decoder=encode(self.decoder.getstate()[0]),
                    ansi=self.ansi, fragmented=self.fragmented, invalidated=self.invalidated,
                    head=encode(self.head), tail=encode(self.tail))

    def _plain(self, value):
        output = []
        for c in value:
            if not self.ansi:
                if c == '\x1b': self.ansi = 'esc'
                else: output.append(c)
            elif self.ansi == 'esc':
                self.ansi = 'csi' if c == '[' else 'osc' if c == ']' else ''
            elif self.ansi == 'csi':
                if '@' <= c <= '~': self.ansi = ''
            elif self.ansi == 'osc':
                if c == '\x07': self.ansi = ''
                elif c == '\x1b': self.ansi = 'osc_esc'
            else:
                self.ansi = '' if c == '\\' else 'osc'
        return ''.join(output)

    def _record(self, raw, start, *, complete=False, abandoned=False):
        strict = codecs.getincrementaldecoder('utf-8')('strict')
        strict.setstate(self.decoder.getstate())
        decode_errors = False
        try: strict.decode(raw, final=complete or abandoned)
        except UnicodeDecodeError: decode_errors = True
        text = self.decoder.decode(raw, final=complete or abandoned)
        result = dict(byte_start=start, byte_end=start + len(raw), raw=raw,
                      text=self._plain(text), complete_line=complete,
                      fragment=self.fragmented or not complete, decode_errors=decode_errors,
                      epoch=self.epoch)
        if abandoned: result['abandoned'] = True
        self.fragmented = not complete
        if complete or abandoned:
            self.decoder.reset()
            self.ansi = ''
        return result

    def _reset(self, records, new_identity):
        if self.pending:
            records.append(self._record(self.pending, self.offset - len(self.pending), abandoned=True))
        elif self.decoder.getstate()[0]:
            # All bytes already belong to emitted fragments; flush display decoder only.
            records.append(self._record(b'', self.offset, abandoned=True))
        self.epoch = str(uuid.uuid4())
        self.file_identity, self.offset, self.pending = new_identity, 0, b''
        self.head = self.tail = b''
        self.decoder.reset()
        self.ansi, self.fragmented = '', False
        self.invalidated = False
        self._snapshot_cache = None

    def poll(self):
        records = []
        snapshot = None
        status, error, caught_up = 'ok', None, False
        try:
            path = safe_path(self.root, self.relative)
            # Anchor checks must reread the file, not Python's stale read buffer.
            with path.open('rb', buffering=0) as stream:
                before = os.fstat(stream.fileno())
                # Revalidate pathname and opened handle to reject replacement races.
                if signature(safe_path(self.root, self.relative).stat()) != signature(before):
                    raise ValueError('log_open_drift')
                ident = f'{before.st_dev:x}:{before.st_ino:x}'
                reset = self.invalidated or self.file_identity is not None and (ident != self.file_identity or before.st_size < self.offset)
                if not reset and self.offset:
                    stream.seek(0)
                    reset = stream.read(len(self.head)) != self.head
                    stream.seek(self.offset - len(self.tail))
                    reset = reset or stream.read(len(self.tail)) != self.tail
                if reset: self._reset(records, ident)
                self.file_identity = ident
                stream.seek(self.offset)
                budget = min(self.max_read_bytes, max(0, before.st_size - self.offset))
                while budget and len(records) < self.max_records and not self.cancelled():
                    # Pending contains no complete line and is shorter than max_line_bytes.
                    # Each added byte can therefore produce at most one record. Limiting
                    # reads by remaining slots bounds output without overreading/dropping bytes.
                    chunk = stream.read(min(budget, self.chunk_bytes, self.max_records - len(records)))
                    if not chunk: break
                    self.offset += len(chunk)
                    budget -= len(chunk)
                    self.head = (self.head + chunk)[:256]
                    self.tail = (self.tail + chunk)[-256:]
                    self.pending += chunk
                    while self.pending:
                        newline = self.pending.find(b'\n', 0, self.max_line_bytes)
                        complete = newline >= 0
                        length = newline + 1 if complete else self.max_line_bytes
                        if not complete and len(self.pending) < length: break
                        start = self.offset - len(self.pending)
                        raw, self.pending = self.pending[:length], self.pending[length:]
                        records.append(self._record(raw, start, complete=complete))
                stream.seek(0)
                anchors_match = stream.read(len(self.head)) == self.head
                stream.seek(max(0, self.offset - 256))
                anchors_match = anchors_match and stream.read(len(self.tail)) == self.tail
                after = os.fstat(stream.fileno())
                stable = signature(after) == signature(before)
                append_only = (after.st_dev == before.st_dev and after.st_ino == before.st_ino
                               and after.st_size > before.st_size and after.st_size >= self.offset)
                if not anchors_match or not (stable or append_only):
                    # A normal append retains the cursor; overwritten observed anchors do not.
                    status, error = 'error', 'log_read_drift'
                    self.invalidated = True
                    self._snapshot_cache = None
                else:
                    caught_up = self.offset == after.st_size
                    if caught_up and stable and after.st_size <= 8 * 1024 * 1024 and not self.cancelled():
                        if self._snapshot_cache and self._snapshot_cache[0] == signature(after):
                            snapshot = dict(self._snapshot_cache[1])
                    if caught_up and stable and snapshot is None and after.st_size <= 8 * 1024 * 1024 and not self.cancelled():
                        stream.seek(0)
                        digest = hashlib.sha256()
                        left = after.st_size
                        while left and not self.cancelled():
                            chunk = stream.read(min(65536, left))
                            if not chunk: break
                            digest.update(chunk)
                            left -= len(chunk)
                        if not left and signature(os.fstat(stream.fileno())) == signature(after):
                            snapshot = dict(sha256=digest.hexdigest(), size=after.st_size)
                            self._snapshot_cache = (signature(after), dict(snapshot))
        except FileNotFoundError:
            status, error = 'missing', 'log_missing'
        except (OSError, ValueError) as exc:
            status, error = 'error', str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        result = dict(records=records, state=self.state(), epoch=self.epoch,
                      file_identity=self.file_identity, status=status, error_code=error, caught_up=caught_up)
        if snapshot is not None: result['snapshot'] = snapshot
        return result
