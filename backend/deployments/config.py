"""Ordinary configuration saves through the existing installation guard."""
from contextlib import contextmanager
import hashlib
from pathlib import Path

from backend.config_adapters.pswallpaper import ConfigRejected, MAX_BYTES, parse
from .journal import private_dir
from .paths import GuardRejected, _information, file_identity, flush_file, open_handle, pinned_chain


def _read(handle):
    import pywintypes
    import win32file
    info = _information(handle, False)
    size = (info[5] << 32) | info[6]
    if size > MAX_BYTES:
        raise ConfigRejected('CONFIG_UNSUPPORTED')
    try:
        win32file.SetFilePointer(handle, 0, 0)
        data = win32file.ReadFile(handle, size)[1] if size else b''
    except pywintypes.error:
        raise ConfigRejected('CONFIG_IO') from None
    if len(data) != size:
        raise ConfigRejected('CONFIG_IO')
    return data


def _write(handle, data):
    import pywintypes
    import win32file
    try:
        win32file.SetFilePointer(handle, 0, 0)
        if data and win32file.WriteFile(handle, data)[1] != len(data):
            raise ConfigRejected('CONFIG_IO')
        win32file.SetEndOfFile(handle)
        flush_file(handle)
    except pywintypes.error:
        raise ConfigRejected('CONFIG_IO')


class ConfigService:
    def __init__(self, paths, guards):
        self.paths, self.guards = paths, guards

    @staticmethod
    def _adapter(adapter_id):
        if adapter_id != 'pswallpaper':
            raise ConfigRejected('CONFIG_UNSUPPORTED')

    @contextmanager
    def _file(self, installation, *, update=False):
        # The RPC never accepts a path. Do not add UserData to ordinary package roots.
        path = Path(installation.canonical_root) / 'UserData' / 'PSWallpaper.cfg'
        try:
            with pinned_chain(path.parent) as chain:
                if dict(chain).get(str(Path(installation.canonical_root)).casefold()) != installation.file_identity:
                    raise GuardRejected('GUARD_INSTALLATION_CHANGED')
                handle = open_handle(path, parent=chain.handles[-1], update=update)
                try:
                    chain.check()
                    yield handle, chain
                    chain.check()
                finally:
                    handle.Close()
        except FileNotFoundError:
            raise ConfigRejected('CONFIG_MISSING') from None
        except OSError:
            raise ConfigRejected('CONFIG_IO') from None
        except GuardRejected as exc:
            if exc.code == 'GUARD_PATH_UNAVAILABLE':
                raise ConfigRejected('CONFIG_IO') from None
            raise

    @staticmethod
    def _view(raw):
        result = dict(adapter_id='pswallpaper', display_name='壁纸 Mod 设置',
                      relative_path='UserData/PSWallpaper.cfg', revision=hashlib.sha256(raw).hexdigest())
        try:
            document = parse(raw)
            return dict(result, editable=True, fields=document.fields())
        except ConfigRejected:
            return dict(result, editable=False, fields=[],
                        message='此配置的格式或编码暂不支持可靠编辑，请使用外部编辑器；原文件未改动。')

    def read(self, installation_id, adapter_id, *, check=lambda: None):
        self._adapter(adapter_id)
        with self.guards._installation(installation_id,check=check) as (installation, _):
            with self._file(installation) as (handle, _):
                check()
                result = self._view(_read(handle))
                check()
                return result

    def _backup(self, installation_id, before):
        with private_dir(self.paths.root, 'config-backups/' + installation_id, create=True) as chain:
            leaf = 'PSWallpaper.cfg'
            try:
                handle = open_handle(leaf, parent=chain.handles[-1], update=True)
            except FileNotFoundError:
                handle = open_handle(leaf, parent=chain.handles[-1], create=True)
            try:
                _write(handle, before)
                if _read(handle) != before:
                    raise ConfigRejected('CONFIG_IO')
            finally:
                handle.Close()

    def save(self, installation_id, adapter_id, expected_revision, changes, backup=True):
        self._adapter(adapter_id)
        with self.guards.operation(installation_id, 'apply') as session:
            with self._file(session.installation, update=True) as (handle, chain):
                before = _read(handle)
                if hashlib.sha256(before).hexdigest() != expected_revision:
                    raise ConfigRejected('CONFIG_CHANGED')
                after = parse(before).patch(changes)
                if after == before:
                    return dict(self._view(before), saved=True, changed=False, backup_created=False)
                if backup:
                    self._backup(installation_id, before)
                session.check()
                identity = file_identity(_information(handle, False))
                try:
                    _write(handle, after)
                    if _read(handle) != after or file_identity(_information(handle, False)) != identity:
                        raise ConfigRejected('CONFIG_IO')
                    chain.check()
                except (OSError, ConfigRejected, GuardRejected):
                    # Best effort on this same pinned file, without another approval/transaction.
                    # A hardware failure can still require the optional original backup.
                    try:
                        _write(handle, before)
                    except (OSError, ConfigRejected, GuardRejected):
                        pass
                    raise ConfigRejected('CONFIG_IO') from None
                return dict(self._view(after), saved=True, changed=True, backup_created=backup)
