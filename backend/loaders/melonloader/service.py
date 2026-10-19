"""Detect and install the official loader without executing downloaded code."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import stat
import tempfile
import threading
import time
import tomllib
from urllib.request import Request, urlopen
from uuid import uuid4
import zipfile

from backend.app.paths import reject_reparse
from backend.catalog.archive_policy import canonical_name, locked_path
from backend.deployments.journal import private_dir
from backend.deployments.paths import open_handle, native_rename, pinned_chain
from backend.games.probably_stolen.fingerprint import file_version
from backend.games.probably_stolen.layout import EXE, safe_path
from .environment import pe_machine, environment_checks
from .offline import OfflineToolchain, OfflineError, GENERATOR

VERSION = '0.7.3'
RELEASE_URL = 'https://github.com/LavaGang/MelonLoader/releases/download/v0.7.3/MelonLoader.x64.zip'
OFFICIAL_SHA256 = '5b2b2f3d1cd42b59ec886c5bdc2663edae87a0097a4f4a8f58c0965a99dda416'
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
CORE_FILES = ('version.dll', 'MelonLoader/net6/MelonLoader.dll', 'MelonLoader/net6/MelonLoader.NativeHost.dll',
    'MelonLoader/net6/MelonLoader.runtimeconfig.json', 'MelonLoader/Dependencies/SupportModules/Il2Cpp.dll',
    'MelonLoader/Dependencies/Il2CppAssemblyGenerator/Il2CppAssemblyGenerator.dll')


class LoaderError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class MelonLoaderService:
    def __init__(self, paths, guards, *, release_sha256=OFFICIAL_SHA256, offline_resources=None):
        self.paths, self.guards = paths, guards
        self.release_sha256 = release_sha256  # Host configuration, never a bridge argument.
        self._sources = {}
        self._lock = threading.RLock()
        self.offline = OfflineToolchain(offline_resources)

    @staticmethod
    def _configuration(root):
        """Read user choices, not historical generator version records."""
        try:
            path = safe_path(root, 'UserData/Loader.cfg')
            if path.stat().st_size > 1024 * 1024:
                raise ValueError()
            document = tomllib.loads(path.read_text(encoding='utf-8-sig'))
            loader, unity = document.get('loader', {}), document.get('unityengine', {})
            if not isinstance(loader, dict) or type(loader.get('disable', False)) is not bool:
                raise ValueError()
            if (not isinstance(unity, dict)
                    or not isinstance(unity.get('force_il2cpp_dumper_version', ''), str)
                    or len(unity.get('force_il2cpp_dumper_version', '')) > 256
                    or not isinstance(unity.get('version_override', ''), str)
                    or len(unity.get('version_override', '')) > 256):
                return loader, '', '', False
            return loader, unity.get('force_il2cpp_dumper_version', ''), unity.get('version_override', ''), True
        except FileNotFoundError:
            return {}, '', '', True
        except (OSError, ValueError):
            return {}, '', '', False

    def _generation_state(self, root, loader_version, *, check=lambda: None):
        _, forced_dumper, unity_override, config_valid = self._configuration(root)
        recorded_unity, generator_config_valid = '', True
        try:
            path = safe_path(root, GENERATOR + 'Config.cfg')
            if path.stat().st_size > 1024 * 1024:
                raise ValueError()
            record = tomllib.loads(path.read_text(encoding='utf-8-sig')).get('Il2CppAssemblyGenerator', {})
            if not isinstance(record, dict) or not isinstance(record.get('UnityVersion', ''), str):
                raise ValueError()
            recorded_unity = record.get('UnityVersion', '')
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            generator_config_valid = False
        try:
            current_unity = file_version(safe_path(root, 'UnityPlayer.dll'))
        except (OSError, ValueError):
            current_unity = None
        return self.offline.assess(root, loader_version=loader_version, current_unity=current_unity,
            forced_dumper=forced_dumper, config_valid=config_valid, recorded_unity=recorded_unity,
            generator_config_valid=generator_config_valid, unity_override=unity_override, check=check)

    def status(self, installation_id, *, check=lambda: None):
        with self.guards._installation(installation_id, check=check) as (installation, _):
            root = Path(installation.canonical_root)
            checks, present, unreadable, damaged = [], [], [], []
            for name in CORE_FILES:
                check()
                try:
                    path = safe_path(root, name)
                    if path.stat().st_size and (not name.endswith('.dll') or pe_machine(path) in (0x14c, 0x8664)):
                        with path.open('rb'): pass
                        present.append(name)
                    else: damaged.append(name)
                except FileNotFoundError: pass
                except (OSError, ValueError): unreadable.append(name)
            missing = [name for name in CORE_FILES if name not in present]
            state = 'unreadable' if unreadable else 'installed' if not missing else 'incomplete' if present or damaged else 'not_installed'
            checks.append(dict(id='loader_files', label='MelonLoader 文件', status='ok' if not missing else 'unknown' if unreadable else 'missing',
                detail='加载器文件齐全' if not missing else '无法读取：' + '、'.join(unreadable) if unreadable else '缺少或损坏：' + '、'.join(missing)))
            version = file_version(root / 'MelonLoader/net6/MelonLoader.dll') if 'MelonLoader/net6/MelonLoader.dll' in present else None
            machine = pe_machine(safe_path(root, EXE))
            checks.append(dict(id='architecture', label='游戏架构', status='ok' if machine == 0x8664 else 'unknown',
                detail='Windows x64' if machine == 0x8664 else '当前安装入口需要 Windows x64 游戏。'))
            if 'version.dll' in present:
                machine_proxy = pe_machine(root / 'version.dll')
                checks.append(dict(id='proxy', label='启动代理', status='ok' if machine_proxy == 0x8664 else 'missing',
                    detail='version.dll · x64' if machine_proxy == 0x8664 else 'version.dll 架构不匹配或文件已损坏。'))
            config, _, _, config_valid = self._configuration(root)
            config_status = 'ok' if config_valid else 'unknown'
            disabled = config.get('disable') is True
            checks.append(dict(id='loader_enabled', label='加载器启用状态', status='disabled' if disabled else config_status,
                detail='UserData/Loader.cfg 的 disable=true，请改为 false。' if disabled else '无法读取 Loader.cfg，请检查配置格式。' if config_status=='unknown' else '未关闭 Mod 加载'))
            major = 6
            if CORE_FILES[3] in present:
                try:
                    cfg = root / CORE_FILES[3]
                    if cfg.stat().st_size > 64 * 1024: raise ValueError()
                    framework = json.loads(cfg.read_text(encoding='utf-8-sig'))['runtimeOptions']['framework']
                    if framework['name'] != 'Microsoft.NETCore.App': raise ValueError()
                    major = int(framework['version'].split('.')[0])
                    if not 6 <= major <= 20: raise ValueError()
                except (OSError, ValueError, KeyError, TypeError):
                    checks.append(dict(id='runtime_config',label='运行时配置',status='unknown',detail='无法读取 MelonLoader.runtimeconfig.json。'))
                    major = 6
            checks.extend(environment_checks(root, config, major))
            generation = self._generation_state(root, version, check=check)
            checks.append(dict(id='generation_dependencies', label='IL2CPP 生成依赖',
                status=generation['status'], detail=generation['message']))
            ready = state == 'installed' and all(row['status'] == 'ok' for row in checks)
            issue = next((row['detail'] for row in checks if row['status'] != 'ok'), '')
            message = 'MelonLoader 和运行环境已就绪。' if ready else issue
            if state == 'not_installed': message = '安装 MelonLoader 后，游戏才能加载 Mod。'
            return dict(installation_id=str(installation_id), status=state, version_raw=version,
                recommended_version=VERSION, ready=ready, message=message, checks=checks,
                generation_dependencies=generation,
                can_install=machine == 0x8664 and not unreadable,
                install_label=f'重新安装 MelonLoader {VERSION}' if state=='installed' else f'安装 MelonLoader {VERSION}')

    def _restore(self, session, *, check=lambda: None, progress=lambda *args: None, commit=None,
                 loader_version=None):
        """Restore only known official bytes under the caller's existing lease."""
        root = Path(session.installation.canonical_root)
        def boundary():
            check()
            session.check()
        boundary()
        if loader_version is None:
            try:
                loader_version = file_version(safe_path(root, 'MelonLoader/net6/MelonLoader.dll'))
            except (OSError, ValueError):
                loader_version = None
        state = self._generation_state(root, loader_version, check=check)
        if state['ready']:
            boundary()
            if commit:
                commit(lambda: None)
            return dict(repaired=False, files_written=0, generation_dependencies=state)
        if not state['can_repair']:
            raise LoaderError('LOADER_GENERATION_UNSUPPORTED', state['message'])
        self.paths.check_owned_paths()
        base = self.paths.root / 'loader'
        reject_reparse(base)
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='dependencies-', dir=base) as workspace:
            stage = Path(workspace) / 'files'
            stage.mkdir()
            try:
                files = self.offline.extract(stage, state['missing'] + state['damaged'], check)
            except OfflineError as exc:
                raise LoaderError(exc.code, str(exc)) from None
            cleanup_failed = self._publish(root, stage, files, boundary, progress, commit)
        state = self._generation_state(root, loader_version, check=check)
        result = dict(repaired=True, files_written=len(files), generation_dependencies=state)
        if cleanup_failed:
            result['warning'] = '依赖已恢复，但部分临时旧文件被占用，留在生成器目录中。'
        return result

    def repair(self, installation_id, *, check=lambda: None, progress=lambda *args: None, commit=None):
        with self._lock:
            with self.guards.operation(installation_id, 'apply') as session:
                result = self._restore(session, check=check, progress=progress, commit=commit)
            result['status'] = self.status(installation_id, check=check)
            return result

    def prepare_launch(self, session, *, check=lambda: None, progress=lambda *args: None, commit=None):
        """Called inside guards.operation('launch'); never acquire a second lease."""
        with self._lock:
            root = Path(session.installation.canonical_root)
            check()
            session.check()
            try:
                loader = safe_path(root, 'MelonLoader/net6/MelonLoader.dll')
            except FileNotFoundError:
                return dict(repaired=False, files_written=0, skipped='no_loader')
            try:
                safe_path(root, 'version.dll')
            except FileNotFoundError:
                # A leftover DLL is not an active loader installation. Keep
                # ordinary game/probe launches usable without installing it.
                return dict(repaired=False, files_written=0, skipped='no_loader_proxy')
            config, _, _, _ = self._configuration(root)
            if config.get('disable') is True:
                return dict(repaired=False, files_written=0, skipped='disabled')
            version = file_version(loader)
            state = self._generation_state(root, version, check=check)
            if state['ready']:
                if commit:
                    commit(lambda: None)
                return dict(repaired=False, files_written=0, generation_dependencies=state)
            if not state['supported'] or not state['can_repair'] and not state['ready']:
                if state['existing_tools']:
                    return dict(repaired=False, files_written=0, skipped='custom_toolchain',
                        generation_dependencies=state)
                raise LoaderError('LOADER_GENERATION_UNSUPPORTED', state['message'])
            return self._restore(session, check=check, progress=progress, commit=commit, loader_version=version)

    def grant(self, installation_id, path):
        if not path: return dict(source_handle=None)
        with self.guards._installation(installation_id):
            source = Path(path)
            if not source.is_absolute() or source.suffix.casefold() != '.zip':
                raise LoaderError('LOADER_ZIP_REQUIRED', '请选择官方 MelonLoader 0.7.3 x64 ZIP。')
            reject_reparse(source)
            with self._lock:
                now = time.monotonic()
                self._sources = {key:row for key,row in self._sources.items() if row[2] > now}
                handle = str(uuid4())
                self._sources[handle] = (str(installation_id), source, now + 600)
            return dict(source_handle=handle)

    def _download(self, destination, check, progress):
        request = Request(RELEASE_URL, headers={'User-Agent':'PSModManager/0.1.0', 'Accept':'application/octet-stream'})
        try:
            with urlopen(request, timeout=20) as response, destination.open('xb') as stream:
                total = int(response.headers.get('Content-Length', 0)) or None
                count = 0
                while block := response.read(256 * 1024):
                    check(); count += len(block)
                    if count > MAX_ARCHIVE: raise LoaderError('LOADER_PACKAGE_LIMIT', '下载包过大，请使用官方 x64 ZIP。')
                    stream.write(block)
                    progress(0, None, 'download', 'LOADER_DOWNLOADING',
                        {'bytes_received': count, 'bytes_total': max(total,count) if total else None})
        except LoaderError: raise
        except OSError:
            raise LoaderError('LOADER_DOWNLOAD_FAILED', '无法下载 MelonLoader，请检查网络后重试，或选择已下载的官方 ZIP。') from None

    def _extract(self, archive_path, stage, check):
        if archive_path.stat().st_size > MAX_ARCHIVE:
            raise LoaderError('LOADER_PACKAGE_LIMIT','压缩包过大，请使用官方 MelonLoader x64 ZIP。')
        digest = hashlib.sha256()
        with archive_path.open('rb') as stream:
            while block := stream.read(256 * 1024): check(); digest.update(block)
        if digest.hexdigest() != self.release_sha256:
            raise LoaderError('LOADER_PACKAGE_INVALID', '该文件不是官方 MelonLoader 0.7.3 x64 ZIP，或下载未完成。请重新下载。')
        output, seen, total = [], set(), 0
        try:
            with zipfile.ZipFile(archive_path) as archive:
                members = archive.infolist()
                if len(members) > 2048: raise ValueError()
                for member in members:
                    check()
                    name = canonical_name(member.filename.rstrip('/') if member.is_dir() else member.filename)
                    kind = stat.S_IFMT(member.external_attr >> 16)
                    if member.flag_bits & 1 or kind not in (0, stat.S_IFREG, stat.S_IFDIR): raise ValueError()
                    if name.casefold() != 'melonloader' and not name.casefold().startswith('melonloader/') and name != 'version.dll': raise ValueError()
                    key = name.casefold()
                    if key in seen: raise ValueError()
                    seen.add(key)
                    if member.is_dir(): continue
                    total += member.file_size
                    if member.file_size > MAX_ARCHIVE or total > MAX_EXPANDED: raise ValueError()
                    target = stage / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, target.open('xb') as destination:
                        count = 0
                        while block := source.read(256 * 1024):
                            check(); count += len(block)
                            if count > member.file_size: raise ValueError()
                            destination.write(block)
                        if count != member.file_size: raise ValueError()
                    output.append(name)
            if not all(name in output for name in CORE_FILES) or pe_machine(stage/'version.dll') != 0x8664:
                raise ValueError()
        except (ValueError, OSError, zipfile.BadZipFile):
            raise LoaderError('LOADER_PACKAGE_INVALID', '加载器压缩包内容不完整或路径无效，请重新下载官方 x64 ZIP。') from None
        return sorted(output, key=lambda name: (name == 'version.dll', name))

    @contextmanager
    def _parent(self, root, relative, create=True, created=None):
        parent = Path(relative).parent.as_posix()
        if parent == '.':
            with pinned_chain(root) as chain: yield chain
        else:
            if create and created is not None:
                missing = []
                path = root / parent
                while path != root and not path.exists():
                    missing.append(path)
                    path = path.parent
                created.extend(path for path in reversed(missing) if path not in created)
            with private_dir(root, parent, create=create) as chain: yield chain

    def _publish(self, root, stage, files, check, progress, commit=None):
        """Atomic files with temporary old copies for ordinary failure/cancel undo."""
        import win32file
        changes, temporary, created = [], [], []
        token = uuid4().hex
        try:
            for index, relative in enumerate(files):
                check()
                with self._parent(root, relative, created=created) as chain:
                    parent = chain.handles[-1]
                    leaf = Path(relative).name
                    temp_leaf, backup = '.psmm-loader-'+token+'-'+str(index)+'.tmp', None
                    target = open_handle(temp_leaf, parent=parent, create=True, delete=True)
                    temporary.append((relative, temp_leaf))
                    try:
                        with (stage/relative).open('rb') as source:
                            while block := source.read(256 * 1024):
                                check()
                                _, written = win32file.WriteFile(target, block)
                                if written != len(block): raise OSError('Incomplete loader file write')
                        win32file.FlushFileBuffers(target)
                        try: old = open_handle(leaf, parent=parent, delete=True)
                        except FileNotFoundError: old = None
                        if old is not None:
                            try:
                                backup = '.psmm-loader-'+token+'-'+str(index)+'.bak'
                                native_rename(old, parent, backup)
                            finally: old.Close()
                        changes.append((relative, backup))
                        native_rename(target, parent, leaf)
                        temporary.pop()
                        chain.check()
                    finally: target.Close()
                progress(index+1, len(files), 'installing', 'LOADER_INSTALLING')
            if commit: commit(lambda: None)
        except BaseException:
            # No automatic recovery history: restore this attempted install only.
            failed = False
            for relative, leaf in temporary:
                try:
                    with self._parent(root, relative, create=False) as chain: self._remove(chain, leaf)
                except (OSError, ValueError): failed = True
            for relative, backup in reversed(changes):
                try:
                    with self._parent(root, relative, create=False) as chain:
                        self._remove(chain, Path(relative).name)
                        if backup:
                            handle = open_handle(backup, parent=chain.handles[-1], delete=True)
                            try: native_rename(handle, chain.handles[-1], Path(relative).name)
                            finally: handle.Close()
                except (OSError, ValueError): failed = True
            for directory in reversed(created):
                try: directory.rmdir()
                except FileNotFoundError: pass
                except OSError: failed = True
            if failed:
                raise LoaderError('LOADER_REPAIR_REQUIRED', '部分加载器文件被占用，未能完成安装。关闭占用程序后重新安装；临时旧文件已保留。') from None
            raise
        cleanup_failed = False
        for relative, backup in changes:
            if backup:
                try:
                    with self._parent(root, relative, create=False) as chain: self._remove(chain, backup)
                except (OSError, ValueError): cleanup_failed = True
        return cleanup_failed

    @staticmethod
    def _remove(chain, leaf):
        import win32file
        try: handle = open_handle(leaf, parent=chain.handles[-1], delete=True)
        except FileNotFoundError: return
        try: win32file.SetFileInformationByHandle(handle, 4, True)
        finally: handle.Close()

    def install(self, installation_id, source_handle=None, *, check=lambda: None, progress=lambda *args: None, commit=None):
        with self._lock:
            source = None
            if source_handle:
                record = self._sources.get(str(source_handle))
                if not record or record[0] != str(installation_id) or record[2] < time.monotonic():
                    raise LoaderError('LOADER_SOURCE_EXPIRED', '所选 ZIP 已过期，请重新选择。')
                source = record[1]
            self.paths.check_owned_paths()
            base = self.paths.root/'loader'
            reject_reparse(base); base.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='install-', dir=base) as workspace:
                workspace = Path(workspace)
                stage = workspace/'files'; stage.mkdir()
                if source is None:
                    archive = workspace/'MelonLoader.x64.zip'
                    self._download(archive, check, progress)
                    files = self._extract(archive, stage, check)
                else:
                    with locked_path(source): files = self._extract(source, stage, check)
                with self.guards.operation(installation_id, 'apply') as session:
                    root = Path(session.installation.canonical_root)
                    if pe_machine(safe_path(root, EXE)) != 0x8664:
                        raise LoaderError('LOADER_ARCHITECTURE', '当前安装包适用于 Windows x64 游戏，请检查所选目录。')
                    # An unrelated proxy must not silently become MelonLoader.
                    if (root/'version.dll').exists() and not (root/'MelonLoader/net6/MelonLoader.dll').is_file():
                        raise LoaderError('LOADER_PROXY_CONFLICT', '游戏目录已有其他 version.dll，请先确认并移走该代理文件，再安装 MelonLoader。')
                    # The official loader ZIP intentionally omits its generated
                    # toolchain. Stage known, matching offline dependencies in
                    # this same publication so cancellation rolls back both.
                    generation = self._generation_state(root, VERSION, check=check)
                    if generation['can_repair']:
                        try:
                            dependencies = self.offline.extract(stage,
                                generation['missing'] + generation['damaged'], check)
                            files = sorted(files + dependencies,
                                key=lambda name: (name == 'version.dll', name))
                        except OfflineError as exc:
                            raise LoaderError(exc.code, str(exc)) from None
                    check(); session.check()
                    cleanup_failed = self._publish(root, stage, files, check, progress, commit)
                result = dict(installed=True, files_written=len(files), status=self.status(installation_id))
                warnings = []
                if not result['status']['generation_dependencies']['ready']:
                    warnings.append(result['status']['generation_dependencies']['message'])
                if cleanup_failed:
                    warnings.append('安装已完成，但部分临时旧文件被占用，留在对应加载器目录中。')
                if warnings:
                    result['warning'] = ' '.join(warnings)
                return result
