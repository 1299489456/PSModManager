"""Minimal local-only desktop composition; no legacy module in the startup graph."""
import ctypes
import importlib.util
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
import webbrowser

from .identity import APP_NAME, APP_VERSION, APP_USER_MODEL_ID, KEYRING_SERVICE, UPDATE_SOURCE, DATABASE_APPLICATION_ID
from .paths import resolve_paths
from .api import DesktopApi


def webview_runtime_version():
    if sys.platform != 'win32':
        raise RuntimeError('当前桌面入口需要 Windows 和 WebView2 Runtime')
    spec = importlib.util.find_spec('webview')
    if spec is None:
        raise RuntimeError('缺少 pywebview；请先准备项目 .venv，不会自动安装')
    loader = Path(spec.origin).parent / 'lib/runtimes/win-x64/native/WebView2Loader.dll'
    try:
        library = ctypes.WinDLL(str(loader))
        query = library.GetAvailableCoreWebView2BrowserVersionString
        query.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
        query.restype = ctypes.c_long
        value = ctypes.c_void_p()
        result = query(None, ctypes.byref(value))
        if result != 0 or not value.value:
            raise OSError('runtime unavailable')
        try:
            return ctypes.wstring_at(value)
        finally:
            free = ctypes.windll.ole32.CoTaskMemFree
            free.argtypes = [ctypes.c_void_p]
            free(value)
    except OSError as exc:
        raise RuntimeError('缺少可用的 WebView2 Runtime；请准备运行时后重试，不会自动安装') from exc


def desktop_html():
    """Inline trusted local assets, avoiding a localhost HTTP/RPC server."""
    dist = Path(__file__).resolve().parents[2] / 'frontend/dist'
    index = dist / 'index.html'
    if not index.is_file():
        raise RuntimeError('缺少前端构建；请在 frontend 执行 npm run build（不会自动安装依赖）')
    html = index.read_text(encoding='utf-8')

    def asset(relative):
        path = (dist / relative.removeprefix('./')).resolve()
        if not path.is_relative_to(dist.resolve()) or not path.is_file():
            raise RuntimeError('前端构建引用无效或越界资源')
        return path.read_text(encoding='utf-8')

    html = re.sub(r'<script\b[^>]*\bsrc="([^"]+)"[^>]*></script>',
                  lambda m: '<script type="module">' + asset(m[1]).replace('</script', '<\\/script') + '</script>', html)
    html = re.sub(r'<link\b(?=[^>]*rel="stylesheet")[^>]*href="([^"]+)"[^>]*>',
                  lambda m: '<style>' + asset(m[1]) + '</style>', html)
    if re.search(r'<(?:script|link)\b[^>]*(?:src|href)=', html):
        raise RuntimeError('前端构建必须自包含，禁止外部脚本或样式')
    return html


class DesktopContext:
    def __init__(self, paths):
        self.paths = paths
        self.logger = None
        self.store = None
        self.jobs = None
        self.api = None
        self.installations = None
        self.metadata = None
        self.inventory = None
        self.catalog = None
        self.logs = None
        self.diagnostics = None
        self.imports = None
        self.profiles = None
        self.compatibility = None
        self.preferences = None
        self.deployment_guards = None
        self.native_mods = None
        self.native_commands = None
        self.workshop = None
        self.workshop_updates = None
        self.plans = None
        self.plan_proposals = None
        self.deployments = None
        self.mods = None
        self.mod_configs = None
        self.launcher = None
        self.recovery = None
        self.ai = None
        self.ai_credentials_backend = None
        self.deployment_fault = None
        self.window_frame = None
        self.profile_sharing = None
        self.mod_updates = None
        self.confirm_plan = lambda preview: False
        self.choose_profile_source = lambda: None
        self.choose_profile_destination = lambda: None
        self.choose_profile_share_source = lambda: None
        self.choose_profile_share_destination = lambda kind: None
        self.choose_import_source = lambda kind: None
        self.choose_installation_folder = lambda kind: None
        self.choose_storage_folder = lambda: None
        self.request_restart = lambda root: None
        self.storage = None
        self._services_closed = False
        self.restart_root = None

    def initialize(self):
        paths = self.paths
        paths.check_owned_paths()
        from .preferences import Preferences
        self.preferences = Preferences(paths)
        config = self.preferences.read_config()
        from .storage_location import StorageLocation
        self.storage = StorageLocation(self, first_run=(not paths.config.exists() or
            config.get('storage_setup_complete') is False))
        if not paths.config.exists():
            config['storage_setup_complete'] = False
        paths.root.mkdir(parents=True, exist_ok=True)
        # The same typed repositories back the desktop and direct verification.
        # Imported lazily so importing the entry point never opens a database.
        from backend.storage import Store
        self.store = Store(paths.database, library_root=paths.root / 'library')
        from backend.runtime.jobs import JobManager
        self.jobs = JobManager()
        from backend.ai.desktop_runtime import DesktopAssistant
        self.ai = DesktopAssistant(paths, self.preferences, self.jobs, self.ai_credentials_backend)
        from .installations import InstallationService
        self.installations = InstallationService(paths)
        from backend.scanning.service import MetadataInspectionService
        self.metadata = MetadataInspectionService(paths)
        from backend.scanning.inventory import InventoryService
        self.inventory = InventoryService(paths)
        from backend.catalog.service import CatalogService
        self.catalog = CatalogService(paths)
        from backend.catalog.packages import PackageService
        self.packages = PackageService(paths)
        from backend.catalog.imports import ImportService
        self.imports = ImportService(paths)
        from backend.profiles.service import ProfileService
        self.profiles = ProfileService(paths)
        from backend.profiles.compatibility import CompatibilityService
        self.compatibility = CompatibilityService(paths)
        from backend.deployments.process_guard import DeploymentGuards
        self.deployment_guards = DeploymentGuards(paths)
        from backend.deployments.config import ConfigService
        self.mod_configs = ConfigService(paths, self.deployment_guards)
        from backend.games.probably_stolen.launch import LaunchService
        self.launcher = LaunchService(self.deployment_guards,
            prepare_launch=lambda session: self.workshop.release(session.installation.model_dump(mode='json')))
        from backend.native_mods import NativeModService, locations
        from .native_api import NativeCommands, installation, process_status, write_guard
        self.native_mods = NativeModService(paths,
            write_guard=lambda value: write_guard(self, value),
            process_status=lambda value: process_status(self, value))
        self.native_commands = NativeCommands(self)
        from backend.workshop import WorkshopService
        self.workshop = WorkshopService(paths,
            write_guard=lambda value: write_guard(self, value),
            process_status=lambda value: process_status(self, value))
        from .workshop_update_policy import WorkshopUpdatePolicy
        self.workshop_updates = WorkshopUpdatePolicy(self)
        from backend.deployments.planner import PlanService, PlanProposalConsumer
        self.plans = PlanService(paths, self.deployment_guards)
        self.plan_proposals = PlanProposalConsumer(self.plans)
        from backend.deployments.executor import DeploymentExecutor
        from backend.deployments.recovery import RecoveryService
        self.deployments = DeploymentExecutor(paths,self.deployment_guards,self.plans,fault=self.deployment_fault)
        from backend.deployments.mods import ModService
        self.mods = ModService(self.plans, self.deployments)
        from backend.deployments.mod_workspace import ModWorkspaceService
        self.mod_workspace = ModWorkspaceService(self.mods)
        from backend.profiles.sharing import ProfileSharingService
        from backend.catalog.updates import ModUpdateService
        self.profile_sharing = ProfileSharingService(paths, self.profiles, self.mod_workspace)
        self.mod_updates = ModUpdateService(self.mod_workspace)
        from backend.deployments.history import HistoryService
        from backend.deployments.rollback import RollbackService
        self.history = HistoryService(paths,self.deployment_guards,self.deployments,self.plans)
        self.rollbacks = RollbackService(paths,self.deployment_guards,self.plans,self.deployments,self.history)
        self.plans.rollback = self.rollbacks
        self.plans.history = self.history
        self.deployments.history = self.history
        self.history.rollback = self.rollbacks
        def recovery_check(installation, permit=None):
            self.deployments.probe(installation,permit)
        self.deployment_guards.recovery_check = recovery_check
        self.recovery = RecoveryService(self.deployments)
        # Retained legacy transactions are recovery evidence, not permission to
        # replay a MelonLoader file deployment against the native game at startup.
        from backend.diagnostics.service import LogService
        self.logs = LogService(paths, user_data_resolver=lambda value: locations(value)['persistent_root'])
        from backend.diagnostics.explain import DiagnosticService
        self.diagnostics = DiagnosticService(paths)
        from backend.ai.tools import ReadOnlyTools
        self.ai.tools = ReadOnlyTools(self.installations,self.packages,self.mods,self.logs,self.mod_configs,
                                     workspace=self.mod_workspace, native=self.native_mods,
                                     installation_resolver=lambda identity: installation(self, identity))
        from backend.ai.plan_proposals import PlanProposals
        self.ai.proposals = PlanProposals(self.ai.tools)
        if not paths.config.exists():
            with paths.config.open('x', encoding='utf-8') as file:
                json.dump(config, file, ensure_ascii=False, indent=2)
        paths.log.parent.mkdir(parents=True, exist_ok=True)
        paths.webview.mkdir(parents=True, exist_ok=True)
        self.logger = logging.Logger(APP_NAME, logging.INFO)
        handler = RotatingFileHandler(paths.log, maxBytes=1024 * 1024, backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        self.logger.addHandler(handler)
        self.logger.info('startup product=%s version=%s keyring=%s updater=disabled', APP_NAME, APP_VERSION, KEYRING_SERVICE)
        self.workshop_updates.start()

    def close(self, *, keep_api=False, keep_store=False):
        failure = None
        if self.api is not None and not keep_api:
            self.api._close()
        if self._services_closed:
            if self.jobs is not None:
                self.jobs.close()
                self.jobs = None
            if self.store is not None and not keep_store:
                self.store.close()
                self.store = None
            return
        if self.workshop_updates is not None:
            self.workshop_updates.close()
        if self.ai is not None:
            self.ai.close()
        if self.launcher is not None:
            self.launcher.close()
            self.launcher = None
        if self.logs is not None:
            self.logs.close()
        if self.jobs is not None:
            try:
                self.jobs.close()
            except Exception:
                failure = RuntimeError('后台任务未能在关闭期限内停止')
            else:
                self.jobs = None
        if self.workshop is not None:
            close = getattr(self.workshop, 'close', None)
            if close:
                close()
        if self.native_mods is not None:
            close = getattr(self.native_mods, 'close', None)
            if close:
                close()
        if self.profile_sharing is not None and failure is None:
            self.profile_sharing.close()
        if self.imports is not None and failure is None:
            try:
                self.imports.close()
            except Exception:
                failure = RuntimeError('导入暂存清理未完成；已保留文件，请查看本地记录')
            else:
                self.imports = None
        if self.profiles is not None:
            self.profiles.close()
            self.profiles = None
        if self.store is not None and not keep_store:
            self.store.close()
            self.store = None
        if self.logger:
            self.logger.info('shutdown')
            for handler in self.logger.handlers[:]:
                handler.close()
                self.logger.removeHandler(handler)
        if failure is not None:
            raise failure
        self._services_closed = True


def install_navigation_guard(window, logger):
    """Attach before first navigation; untrusted pages never inherit the bridge."""
    native = window.native.browser.webview
    initial_navigation = True
    trusted_document_uri = None

    def navigating(sender, event):
        nonlocal initial_navigation, trusted_document_uri
        url = str(event.Uri)
        # WebView2 reports NavigateToString's first load as a data: navigation,
        # although the document origin becomes about:blank. Pin that exact URI
        # before any script runs; only the identical host document can reload.
        initial_document = initial_navigation and urlsplit(url).scheme == 'data'
        initial_navigation = False
        if initial_document:
            trusted_document_uri = url
            return
        if url == trusted_document_uri:
            return
        if url != 'about:blank':
            event.Cancel = True
            logger.info('navigation_blocked scheme=%s', urlsplit(url).scheme)
            if urlsplit(url).scheme in ('https', 'http'):
                webbrowser.open(url)

    def initialized(sender, event):
        if not event.IsSuccess:
            logger.error('WebView2 initialization failed')
            window._psmm_startup_error = 'WebView2 初始化失败，请检查运行时'
            window.destroy()
            return
        core = sender.CoreWebView2
        from Microsoft.Web.WebView2.Core import CoreWebView2WebResourceContext
        core.AddWebResourceRequestedFilter('*', CoreWebView2WebResourceContext.All)

        def requested(sender, event):
            image = event.ResourceContext == CoreWebView2WebResourceContext.Image
            if not page_resource_allowed(str(event.Request.Uri), image, str(event.Request.Method)):
                event.Response = core.Environment.CreateWebResourceResponse(None, 403, 'Local only', '')
                logger.warning('web_resource_blocked')

        core.WebResourceRequested += requested
        core.Settings.AreDefaultContextMenusEnabled = False
        core.Settings.AreDevToolsEnabled = False
        window._psmm_resource_handler = requested

    native.NavigationStarting += navigating
    native.CoreWebView2InitializationCompleted += initialized
    window._psmm_native_handlers = (navigating, initialized)


def page_resource_allowed(url, is_image, method):
    """The page loads nothing remote except pictures from Valve's image hosts."""
    from backend.workshop import IMAGE_HOSTS
    uri = urlsplit(url)
    if uri.scheme in ('about', 'data'):
        return True
    try:
        port = uri.port
    except ValueError:
        return False
    return (is_image and method == 'GET' and uri.scheme == 'https' and uri.hostname in IMAGE_HOSTS
            and port in (None, 443) and not uri.username and not uri.password)


def run_desktop(data_root=None, protected_roots=(), *, storage_locator=None):
    paths = resolve_paths(data_root, protected_roots)
    version = webview_runtime_version()
    html = desktop_html()
    import webview
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    context = DesktopContext(paths)
    callback_lifecycle = None
    try:
        context.initialize()
        context.storage.locator = storage_locator
        context.storage.protected_roots = tuple(protected_roots)
        context.logger.info('webview2=%s', version)
        webview.settings.update(ALLOW_DOWNLOADS=False, ALLOW_FILE_URLS=False,
                                OPEN_EXTERNAL_LINKS_IN_BROWSER=True, REMOTE_DEBUGGING_PORT=None)
        context.api = DesktopApi(context)
        # Frameless: the page draws the title bar; window_frame hands moves and
        # resizes back to Windows. The native title stays APP_NAME for the taskbar.
        window = webview.create_window(APP_NAME, html=html, js_api=context.api,
                                       width=1366, height=860, min_size=(1100, 720), background_color='#f0fdf4',
                                       frameless=True, easy_drag=False)
        from .window_frame import WindowFrame
        context.window_frame = WindowFrame(window)
        def request_restart(root):
            context.restart_root = root
            # Return the bridge acknowledgement before closing the WebView.
            import threading
            timer = threading.Timer(.4, lambda: context.window_frame.control('close'))
            timer.start()
        context.request_restart = request_restart
        window.events.shown += context.window_frame.initialize
        from .native_drop import install_native_drop
        install_native_drop(window, context)
        from .webview_lifecycle import install_callback_lifecycle_guard
        def can_close_storage():
            if (context.storage.status()['phase'] == 'copying' or
                    context.storage.blocked and context.jobs is not None and not context.jobs.is_idle()):
                return False
        def closing():
            context.api._close()
        callback_lifecycle = install_callback_lifecycle_guard(window, closing, can_close=can_close_storage)
        def choose_folder(kind):
            selected = window.create_file_dialog(webview.FileDialog.FOLDER)
            return selected[0] if selected else None
        context.choose_installation_folder = choose_folder
        context.choose_storage_folder = lambda: choose_folder('storage')
        def choose_import(kind):
            if kind == 'directory':
                selected = window.create_file_dialog(webview.FileDialog.FOLDER)
                return selected[0] if selected else None
            else:
                from .file_dialogs import choose_file
                return choose_file(window, kind)
        context.choose_import_source = choose_import
        def choose_profile():
            from .file_dialogs import choose_file
            return choose_file(window, 'json')
        def save_profile():
            from .file_dialogs import save_profile as choose_destination
            return choose_destination(window)
        context.choose_profile_source = choose_profile
        context.choose_profile_destination = save_profile
        def choose_share_source():
            selected = window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False,
                file_types=('Mod 方案与整合包 (*.json;*.zip)',))
            return selected[0] if selected else None
        def choose_share_destination(kind):
            extension = 'zip' if kind == 'bundle' else 'json'
            selected = window.create_file_dialog(webview.FileDialog.SAVE,
                save_filename='PSMM-Mod方案.' + extension,
                file_types=(f'Mod 方案 (*.{extension})',))
            return selected[0] if selected else None
        context.choose_profile_share_source = choose_share_source
        context.choose_profile_share_destination = choose_share_destination
        def confirm_plan(preview):
            plan = preview['plan']
            action = '回退' if preview.get('intent') == 'rollback' else '部署'
            source = ('\n回退事务：' + preview['source_transaction_id'] + '\n只回退此事务的改动；当前方案不会随回退修改。') if action == '回退' else ''
            message = ('批准此固定' + action + '计划？\n\n计划：' + plan['plan_hash'] + source +
                '\n文件动作范围：' + ', '.join(plan['approval_scope']) +
                '\n备份文件数：' + str(plan['backup_count']) +
                '\n有效期（UTC）：' + plan['expires_at'] +
                '\n\n批准只登记此固定计划；点击应用后执行，运行验证仍需独立完成。')
            return window.create_confirmation_dialog('批准部署计划', message)
        context.confirm_plan = confirm_plan
        window.events.before_show += lambda: install_navigation_guard(window, context.logger)
        window.events.before_load += context.api._page_loading
        window.events.closed += context.api._close
        # Window and taskbar icon; the packaged EXE carries the same icon.
        icon = Path(__file__).resolve().parent / 'resources/psmm-icon.ico'
        webview.start(gui='edgechromium', debug=False, http_server=False, private_mode=False,
                      storage_path=str(paths.webview), icon=str(icon) if icon.is_file() else None)
        if getattr(window, '_psmm_startup_error', None):
            raise RuntimeError(window._psmm_startup_error)
        return {'restart_root': context.restart_root} if context.restart_root else 0
    finally:
        try:
            if callback_lifecycle is not None:
                callback_lifecycle.join()
        finally:
            if context.storage is not None:
                context.storage.wait()
            context.close()
