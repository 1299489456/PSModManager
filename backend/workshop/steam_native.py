"""Small Steamworks v021 ABI used only in the isolated, signed-DLL helper.

Never import a Mod, execute the game, call RestartAppIfNecessary, read Steam
credentials, or call GetItemInstallInfo (which marks content as used).
"""
from contextlib import ExitStack
import ctypes as C
import os
from pathlib import Path

from backend.app.paths import reject_reparse
from backend.deployments.paths import pinned_chain, pinned_file
from .errors import MESSAGES, WorkshopError
from .local import APP_ID
from .signature import trusted_dll

U32, U64, PTR, BOOL = C.c_uint32, C.c_uint64, C.c_void_p, C.c_bool


class Callback(C.Structure):
    _pack_ = 8
    _fields_ = [('user', C.c_int), ('identity', C.c_int), ('data', PTR), ('size', C.c_int)]


class CallCompleted(C.Structure):
    _pack_ = 8
    _fields_ = [('call', U64), ('identity', C.c_int), ('size', U32)]


class SubscriptionResult(C.Structure):
    _pack_ = 8
    _fields_ = [('result', C.c_int), ('item', U64)]


class DownloadResult(C.Structure):
    _pack_ = 8
    _fields_ = [('app', U32), ('item', U64), ('result', C.c_int)]


def fail(code, *, retryable=False):
    raise WorkshopError(code, MESSAGES[code], retryable=retryable)


class SteamNative:
    def __init__(self, root):
        self.stack, self.initialized = ExitStack(), False
        try:
            if os.name != 'nt' or C.sizeof(PTR) != 8:
                fail('WORKSHOP_SESSION_UNSUPPORTED')
            root = Path(root)
            dll = root / 'Probably Stolen_Data/Plugins/x86_64/steam_api64.dll'
            reject_reparse(dll)
            if not dll.is_file():
                fail('WORKSHOP_STEAM_DLL_MISSING')
            # Pin the selected official file before trust validation/loading.
            self.stack.enter_context(pinned_chain(dll.parent))
            self.stack.enter_context(pinned_file(dll, execute=True))
            if not trusted_dll(dll):
                fail('WORKSHOP_STEAM_DLL_UNTRUSTED')
            self.stack.enter_context(os.add_dll_directory(str(dll.parent)))
            self.dll = C.CDLL(str(dll), winmode=0x1100)  # DLL directory + system32.
            self._bind()
            os.environ['SteamAppId'] = APP_ID
            os.environ['SteamGameId'] = APP_ID
            message = C.create_string_buffer(1024)
            result = self.init(message)
            if result:
                fail({2: 'WORKSHOP_STEAM_CLIENT_MISSING', 3: 'WORKSHOP_STEAM_INTERFACE_UNSUPPORTED'}.get(result, 'WORKSHOP_STEAM_INIT_FAILED'), retryable=True)
            self.initialized = True
            self.ugc, self.user, self.utils, self.apps = self.get_ugc(), self.get_user(), self.get_utils(), self.get_apps()
            if not all((self.ugc, self.user, self.utils, self.apps)):
                fail('WORKSHOP_STEAM_INTERFACE_UNSUPPORTED')
            if self.get_app(self.utils) != int(APP_ID):
                fail('WORKSHOP_STEAM_APP_MISMATCH')
            if not self.licensed(self.apps, int(APP_ID)):
                fail('WORKSHOP_STEAM_LICENSE_MISSING')
            if not self.logged_on(self.user):
                fail('WORKSHOP_STEAM_OFFLINE', retryable=True)
            self.manual_init()
            self.pipe = self.get_pipe()
            if not self.pipe:
                fail('WORKSHOP_STEAM_INIT_FAILED')
        except WorkshopError:
            self.close()
            raise
        except (OSError, ValueError, AttributeError):
            self.close()
            fail('WORKSHOP_STEAM_INTERFACE_UNSUPPORTED')

    def _function(self, name, result, *args):
        function = getattr(self.dll, name)
        function.restype, function.argtypes = result, list(args)
        return function

    def _bind(self):
        f = self._function
        self.init = f('SteamAPI_InitFlat', C.c_int, PTR)
        self.shutdown = f('SteamAPI_Shutdown', None)
        self.get_ugc = f('SteamAPI_SteamUGC_v021', PTR)
        self.get_user = f('SteamAPI_SteamUser_v023', PTR)
        self.get_utils = f('SteamAPI_SteamUtils_v010', PTR)
        self.get_apps = f('SteamAPI_SteamApps_v008', PTR)
        self.get_app = f('SteamAPI_ISteamUtils_GetAppID', U32, PTR)
        self.logged_on = f('SteamAPI_ISteamUser_BLoggedOn', BOOL, PTR)
        self.licensed = f('SteamAPI_ISteamApps_BIsSubscribedApp', BOOL, PTR, U32)
        self.count = f('SteamAPI_ISteamUGC_GetNumSubscribedItems', U32, PTR, BOOL)
        self.subscribed = f('SteamAPI_ISteamUGC_GetSubscribedItems', U32, PTR, C.POINTER(U64), U32, BOOL)
        self.state = f('SteamAPI_ISteamUGC_GetItemState', U32, PTR, U64)
        self.download_info = f('SteamAPI_ISteamUGC_GetItemDownloadInfo', BOOL, PTR, U64, C.POINTER(U64), C.POINTER(U64))
        self.subscribe = f('SteamAPI_ISteamUGC_SubscribeItem', U64, PTR, U64)
        self.unsubscribe = f('SteamAPI_ISteamUGC_UnsubscribeItem', U64, PTR, U64)
        self.download = f('SteamAPI_ISteamUGC_DownloadItem', BOOL, PTR, U64, BOOL)
        self.get_pipe = f('SteamAPI_GetHSteamPipe', C.c_int)
        self.manual_init = f('SteamAPI_ManualDispatch_Init', None)
        self.run_frame = f('SteamAPI_ManualDispatch_RunFrame', None, C.c_int)
        self.next_callback = f('SteamAPI_ManualDispatch_GetNextCallback', BOOL, C.c_int, C.POINTER(Callback))
        self.free_callback = f('SteamAPI_ManualDispatch_FreeLastCallback', None, C.c_int)
        self.call_result = f('SteamAPI_ManualDispatch_GetAPICallResult', BOOL, C.c_int, U64, PTR, C.c_int, C.c_int, C.POINTER(BOOL))

    def ensure_online(self):
        if not self.logged_on(self.user):
            fail('WORKSHOP_STEAM_OFFLINE', retryable=True)

    def subscription_ids(self):
        self.ensure_online()
        count = self.count(self.ugc, True)
        if count > 1024:
            fail('WORKSHOP_SESSION_LIMIT')
        items = (U64 * max(count, 1))()
        received = self.subscribed(self.ugc, items, count, True)
        if received > count:
            fail('WORKSHOP_SESSION_LIMIT')
        return [str(items[index]) for index in range(received)]

    def item(self, identity):
        flags = self.state(self.ugc, int(identity))
        downloaded, total = U64(), U64()
        known = self.download_info(self.ugc, int(identity), C.byref(downloaded), C.byref(total))
        return dict(workshop_id=identity, flags=flags, subscribed=bool(flags & 1), steam_installed=bool(flags & 4),
                    needs_update=bool(flags & 8), downloading=bool(flags & 16), download_pending=bool(flags & 32),
                    disabled_locally=bool(flags & 64), bytes_downloaded=downloaded.value if known else None,
                    bytes_total=total.value if known else None,
                    progress=min(1.0, downloaded.value / total.value) if known and total.value else None)

    def begin(self, action, identity):
        self.ensure_online()
        if action == 'subscribe':
            return int(self.subscribe(self.ugc, int(identity)))
        if action == 'unsubscribe':
            return int(self.unsubscribe(self.ugc, int(identity)))
        # Never high-priority: it would pause unrelated Steam downloads.
        return bool(self.download(self.ugc, int(identity), False))

    def callbacks(self):
        self.run_frame(self.pipe)
        message = Callback()
        events = []
        for _ in range(1024):
            if not self.next_callback(self.pipe, C.byref(message)):
                break
            try:
                if message.identity == 703 and message.size == C.sizeof(CallCompleted):
                    completed = CallCompleted.from_buffer_copy(C.string_at(message.data, message.size))
                    if completed.identity in (1313, 1315) and completed.size == C.sizeof(SubscriptionResult):
                        result, failed = SubscriptionResult(), BOOL()
                        valid = self.call_result(self.pipe, completed.call, C.byref(result), C.sizeof(result), completed.identity, C.byref(failed))
                        events.append(dict(kind='call', call=int(completed.call), callback=completed.identity,
                                           result=int(result.result) if valid and not failed.value else None,
                                           workshop_id=str(result.item), io_failure=not valid or failed.value))
                elif message.identity == 3406 and message.size == C.sizeof(DownloadResult):
                    result = DownloadResult.from_buffer_copy(C.string_at(message.data, message.size))
                    if result.app == int(APP_ID):
                        events.append(dict(kind='download', workshop_id=str(result.item), result=int(result.result)))
            finally:
                self.free_callback(self.pipe)
        return events

    def close(self):
        if self.initialized:
            self.shutdown()
            self.initialized = False
        self.stack.close()
