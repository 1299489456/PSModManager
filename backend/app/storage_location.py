"""A small boot locator and a copy-before-switch desktop data migration."""
from contextlib import ExitStack, closing, nullcontext
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
from uuid import uuid4

from .paths import overlaps, reject_reparse, resolve_paths
from .preferences import Preferences
from backend.catalog.archive_policy import locked_path

REDIRECT = '.storage-redirect.json'
LOCATOR = 'PSModManager-storage.json'

MESSAGES = {
    'STORAGE_BUSY': '数据迁移已经开始，请等待结果并重启管理器。',
    'STORAGE_TARGET_INVALID': '请选择本地磁盘上的独立数据目录，不能位于游戏、受保护目录或当前数据目录之内。',
    'STORAGE_TARGET_NOT_EMPTY': '目标目录不是空目录；请选择新的 PSModManager 专用目录，不会覆盖已有文件。',
    'STORAGE_RECOVERY_REQUIRED': '有操作尚未结束或恢复记录待处理，请先完成操作并重新打开管理器后迁移。',
    'STORAGE_NOT_READY': '请等待迁移完成后再重启。',
    'STORAGE_SPACE_INSUFFICIENT': '目标磁盘空间不足，请腾出空间或选择其他磁盘。',
    'STORAGE_OPERATIONS_RUNNING': '后台文件操作仍在结束中，请等待片刻后再点击重启；不会强行结束操作。',
}


class StorageError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(MESSAGES[code])


def _read(path):
    reject_reparse(path)
    with locked_path(path), path.open('r', encoding='utf-8') as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError('数据位置记录过大；请保留原文件并检查数据位置')
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != {'product', 'version', 'root'}
            or value['product'] != 'PSModManager' or type(value['version']) is not int
            or value['version'] != 1 or not isinstance(value['root'], str)):
        raise ValueError('数据位置记录无效；请保留原文件并检查数据位置')
    return value['root']


def _write(path, root):
    path.parent.mkdir(parents=True, exist_ok=True)
    reject_reparse(path)
    with locked_path(path.parent, directory=True):
        if path.exists():
            with locked_path(path):
                pass
        temporary = path.parent / ('.storage-' + uuid4().hex + '.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(json.dumps(dict(product='PSModManager', version=1, root=str(root)),
                                        ensure_ascii=False).encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def locator_path(*, environ=None):
    local = (os.environ if environ is None else environ).get('LOCALAPPDATA')
    return Path(local) / LOCATOR if local else None


def startup_paths(value=None, protected_roots=(), *, environ=None):
    """Explicit roots keep their contract; only an actual migration redirects them.

    Keeping a tiny forwarding record in a migrated root also preserves existing
    nxm registrations and shortcuts containing --psmm-data-root.
    """
    locator = locator_path(environ=environ) if value is None else None
    if locator is not None and locator.exists():
        value = _read(locator)
    paths = resolve_paths(value, protected_roots, environ=environ)
    seen = set()
    for _ in range(16):
        if paths.root in seen:
            raise ValueError('数据位置记录形成循环，请检查迁移后的目录')
        seen.add(paths.root)
        redirect = paths.root / REDIRECT
        if not redirect.exists():
            if locator is not None and locator.exists() and (not paths.config.is_file() or not paths.database.is_file()):
                raise ValueError('已选择的数据目录不可用，请连接对应磁盘；不会创建空白数据覆盖位置记录')
            return paths
        paths = resolve_paths(_read(redirect), protected_roots, environ=environ)
        if not paths.config.is_file() or not paths.database.is_file():
            raise ValueError('迁移后的数据目录不可用，请连接对应磁盘；不会新建空白数据覆盖原记录')
    raise ValueError('数据迁移记录层数过多，请直接使用最新数据目录')


def relaunch(root, protected_roots=()):
    command = [sys.executable]
    if not getattr(sys, 'frozen', False):
        command.append(str(Path(__file__).resolve().parents[2] / 'main.py'))
    command += ['--psmm-data-root', str(root)]
    for protected in protected_roots:
        command += ['--psmm-protected-root', str(protected)]
    # No shell, URL, credentials, or inherited pipe/SQLite handles.
    subprocess.Popen(command, close_fds=True, cwd=str(Path(sys.executable).parent))


class StorageLocation:
    def __init__(self, context, *, first_run=False, locator=None, protected_roots=()):
        self.context = context
        self.locator = locator
        self.protected_roots = tuple(protected_roots)
        self._lock = threading.RLock()
        self._thread = None
        self.blocked = False
        self._state = dict(root=str(context.paths.root), first_run=first_run, phase='idle',
                           target=None, completed=0, total=None, message='', previous_root=None)

    def status(self):
        root = self.context.paths.root
        with self._lock:
            result = dict(self._state)
        result['locations'] = [dict(label=label, path=str(root / child)) for label, child in (
            ('原生模组方案、规则与恢复点', 'native-mods'),
            ('模组安全报告与处置记录', 'mod-security'),
            ('模组静态分析缓存', 'mod-security-analysis'),
            ('Mod 包库', 'library'), ('方案与安装记录', 'psmodmanager.sqlite3'),
            ('应用配置', 'config.json'), ('部署历史与恢复记录', 'deployments'),
            ('工坊信息与封面缓存', 'cache/workshop'), ('AI 翻译缓存', 'ai-translations.json'),
            ('下载缓存', 'downloads'), ('导入暂存', 'imports'),
            ('方案分享暂存', 'sharing'), ('元数据临时工作区', 'import-worker'),
            ('应用日志', 'logs'), ('WebView 缓存', 'webview2'))]
        if not result['previous_root']:
            result['previous_root'] = self.context.preferences.read_config().get('storage_previous_root')
        return result

    def accept(self):
        if self.blocked:
            raise StorageError('STORAGE_BUSY')
        self.context.preferences.update('storage_setup_complete', True)
        with self._lock:
            self._state['first_run'] = False
        return self.status()

    def _target(self, value):
        source = self.context.paths.root
        # Protect every registered game, not only the known default installation.
        with closing(sqlite3.connect((source / 'psmodmanager.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
            games = [row[0] for row in db.execute('SELECT canonical_root FROM installations')]
        try:
            target = resolve_paths(value, (*self.protected_roots, *games)).root
        except (OSError, ValueError):
            raise StorageError('STORAGE_TARGET_INVALID') from None
        if overlaps(source, target):
            raise StorageError('STORAGE_TARGET_INVALID')
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise StorageError('STORAGE_TARGET_NOT_EMPTY')
        return target

    def start(self, target):
        policy = getattr(self.context, 'workshop_updates', None)
        with self._lock, (policy.migration_gate() if policy is not None else nullcontext()):
            if self.blocked:
                raise StorageError('STORAGE_BUSY')
            security = getattr(self.context, 'mod_security', None)
            if security is not None and security.is_busy():
                raise StorageError('STORAGE_OPERATIONS_RUNNING')
            workshop = getattr(self.context, 'workshop', None)
            if workshop is not None and workshop.is_busy():
                # A Steam operation is not a cancellable local JobManager task.
                # Do not discard its callback receipt by closing the service to
                # migrate app data while Steam can still change package files.
                raise StorageError('STORAGE_OPERATIONS_RUNNING')
            destination = self._target(target)
            from .storage_history import validate_migration_source
            try:
                validate_migration_source(self.context.paths.root)
            except (OSError, ValueError, KeyError, sqlite3.Error):
                raise StorageError('STORAGE_RECOVERY_REQUIRED') from None
            self.blocked = True
            self._state.update(phase='copying', target=str(destination), completed=0, total=None,
                               message='正在停止后台操作；请勿关闭窗口。')
            self._thread = threading.Thread(target=self._run, args=(destination,),
                                            name='PSModManager-storage', daemon=False)
            self._thread.start()
            return dict(self._state)

    def _progress(self, completed, total, message):
        with self._lock:
            self._state.update(completed=completed, total=total, message=message)

    def _run(self, target):
        source = self.context.paths.root
        copied = False
        stopping = True
        try:
            inbox = getattr(self.context, 'nxm_inbox', None)
            if inbox is not None:
                inbox.stop_accepting()
            # The startup Store belongs to the main thread. It is idle and stays
            # there until normal teardown; the copy uses SQLite's backup API.
            self.context.close(keep_api=True, keep_store=True)
            stopping = False
            self._target(str(target))
            from .storage_history import validate_migration_source, rebind_migrated_history
            validate_migration_source(source)
            copy_data(source, target, progress=self._progress, rebind=rebind_migrated_history)
            copied = True
            credentials_copied = copy_saved_credentials(self.context, target)
            # Commit last. A failed/incomplete copy never becomes a startup root.
            _write(source / REDIRECT, target)
            warning = '' if credentials_copied else ' 部分 Windows 凭据未能迁移，请在新目录重新填写 AI 密钥。'
            if self.locator is not None:
                try:
                    _write(self.locator, target)
                except (OSError, ValueError):
                    # The existing locator/default root follows the durable
                    # redirect, so a failed convenience update loses no data.
                    warning += ' 启动位置将通过原目录中的转向记录读取。'
            with self._lock:
                self._state.update(phase='ready', first_run=False, previous_root=str(source),
                    message='迁移完成，重启后使用新目录。旧目录保留为备份。' + warning)
        except Exception as exc:
            # Do not echo filenames, credentials, or exception payloads to JS.
            native = getattr(exc, 'winerror', None)
            detail = (MESSAGES[exc.code] if isinstance(exc, StorageError) else
                '后台操作未能安全停止，请等待当前操作结束后重启重试。' if stopping else
                {5: '没有文件读写权限，请选择可写目录后重试。',
                 32: '文件被占用，请关闭其他管理器或占用程序后重试。',
                 33: '文件被占用，请关闭其他管理器或占用程序后重试。',
                 112: MESSAGES['STORAGE_SPACE_INSUFFICIENT']}.get(native,
                    '请检查目标磁盘、文件占用和恢复记录，再重启管理器重试。'))
            if copied:
                detail += ' 新目录的已复制数据也已保留；重试时请选择另一个空目录。'
            with self._lock:
                self._state.update(phase='failed', message='迁移未完成，仍使用原目录。' + detail)

    def wait(self):
        if self._thread is not None:
            self._thread.join()

    def restart(self):
        with self._lock:
            if self._state['phase'] not in ('ready', 'failed'):
                raise StorageError('STORAGE_NOT_READY')
            if self.context.jobs is not None and not self.context.jobs.is_idle():
                raise StorageError('STORAGE_OPERATIONS_RUNNING')
            root = self._state['target'] if self._state['phase'] == 'ready' else self._state['root']
        self.context.request_restart(root)
        return {'restarting': True}


def copy_saved_credentials(context, target):
    """Rebind only our remembered records through the existing keyring backend.

    Session-only credentials stay session-only. No secret enters a file, log,
    result DTO, or subprocess argument; original records remain for recovery.
    """
    from .identity import KEYRING_SERVICE
    from .paths import AppPaths
    from backend.ai.credentials import CredentialStore
    success = True
    pairs = []
    try:
        registry = context.preferences.read_config().get('ai_providers')
        if registry is not None:
            for provider in registry['providers']:
                if provider.get('model'):
                    config = {name: provider[name] for name in ('provider', 'base_url', 'model')}
                    config['_provider_id'] = provider['id']
                    pairs.append((context.ai.credentials, config))
        else:
            config = context.ai._config()
            if config.get('model'):
                pairs.append((context.ai.credentials, config))
    except ValueError:
        success = False
    for credentials, config in pairs:
        try:
            backend = credentials._backend
            key = backend.get_password(KEYRING_SERVICE, credentials.account(config))
            if key:
                account = CredentialStore(AppPaths(Path(target)), backend).account(config)
                backend.set_password(KEYRING_SERVICE, account, key)
                if backend.get_password(KEYRING_SERVICE, account) != key:
                    success = False
        except Exception:
            success = False
    return success


def copy_data(source, target, *, progress=lambda *_: None, rebind=lambda *_: None):
    """Copy to an owned sibling staging directory, verify, then publish empty target.

    WebView is still displaying progress. Its disposable cache is rebuilt after
    restart, rather than copying Chromium's live locks. All durable PSMM data,
    including private approval keys and history, is copied.
    """
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    reject_reparse(target)
    staging = target.parent / ('.psmm-migration-' + uuid4().hex)
    published = False
    with ExitStack() as pins:
        pins.enter_context(locked_path(source, directory=True))
        pins.enter_context(locked_path(target.parent, directory=True))
        files, total = [], 0
        for directory, dirs, names in os.walk(source, followlinks=False):
            directory = Path(directory)
            reject_reparse(directory)
            if directory == source:
                dirs[:] = [name for name in dirs if name != 'webview2']
            for name in dirs:
                reject_reparse(directory / name)
            for name in names:
                path = directory / name
                relative = path.relative_to(source)
                if relative.as_posix() in {REDIRECT, 'psmodmanager.sqlite3-wal', 'psmodmanager.sqlite3-shm', 'psmodmanager.sqlite3-journal'}:
                    continue
                info = path.lstat()
                reject_reparse(path)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('数据中存在不支持的链接或特殊文件')
                files.append((path, relative, info.st_size))
                total += info.st_size
        if shutil.disk_usage(target.parent).free < total + 1024 * 1024:
            raise StorageError('STORAGE_SPACE_INSUFFICIENT')
        staging.mkdir()
        try:
            copied = 0
            progress(0, total, '正在复制数据；请勿关闭窗口。')
            for path, relative, size in files:
                destination = staging / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if path == source / 'psmodmanager.sqlite3':
                    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as origin, closing(sqlite3.connect(destination)) as db:
                        origin.backup(db)
                        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or db.execute('PRAGMA foreign_key_check').fetchall():
                            raise ValueError('迁移数据库校验失败')
                else:
                    with locked_path(path), path.open('rb') as origin, destination.open('xb') as output:
                        before = os.fstat(origin.fileno())
                        shutil.copyfileobj(origin, output, length=1024 * 1024)
                        output.flush()
                        os.fsync(output.fileno())
                        origin.seek(0)
                        # Direct byte comparison, not a second hash inventory.
                        with destination.open('rb') as verify:
                            while block := origin.read(1024 * 1024):
                                if verify.read(len(block)) != block:
                                    raise OSError('迁移文件校验失败')
                            if verify.read(1):
                                raise OSError('迁移文件长度不符')
                        after = os.fstat(origin.fileno())
                        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                            raise OSError('迁移期间原文件发生变化')
                copied += size
                progress(copied, total, '正在复制并校验数据；请勿关闭窗口。')
            from .paths import AppPaths
            preferences = Preferences(AppPaths(staging))
            preferences.update('storage_setup_complete', True)
            preferences.update('storage_previous_root', str(source))
            rebind(source, staging)
            # Recheck immediately before publication. Never merge or replace a
            # pre-existing directory containing user files.
            reject_reparse(target)
            if target.exists():
                target.rmdir()  # only an empty directory can be removed
            os.rename(staging, target)
            published = True
            progress(total, total, '数据复制与校验完成。')
        finally:
            if not published and staging.exists():
                # Only our freshly created, non-redirected staging tree.
                assert staging.resolve().parent == target.parent.resolve()
                assert staging.name.startswith('.psmm-migration-')
                reject_reparse(staging)
                for child in staging.rglob('*'):
                    reject_reparse(child)
                shutil.rmtree(staging)
