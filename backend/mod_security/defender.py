"""Optional local Defender custom scan, with remediation explicitly disabled.

CLI semantics: https://learn.microsoft.com/en-us/defender-endpoint/
command-line-arguments-microsoft-defender-antivirus . Exit 2 also means scan
errors, so it is NEVER alone a malware verdict. No elevation or policy changes.
"""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from backend.native_mods.paths import safe


def _trusted_microsoft(path):
    # Reuse the WinTrust ABI definitions, not the Steam-specific signer policy.
    from backend.workshop.signature import _File, _Trust, _Signer, _Certificate
    from uuid import UUID
    try:
        trust = C.WinDLL('wintrust', winmode=0x800)
        crypt = C.WinDLL('crypt32', winmode=0x800)
        verify = trust.WinVerifyTrust
        verify.argtypes, verify.restype = [W.HWND, C.c_void_p, C.c_void_p], W.LONG
        provider = trust.WTHelperProvDataFromStateData
        provider.argtypes, provider.restype = [W.HANDLE], C.c_void_p
        signer = trust.WTHelperGetProvSignerFromChain
        signer.argtypes, signer.restype = [C.c_void_p, W.DWORD, W.BOOL, W.DWORD], C.POINTER(_Signer)
        name = crypt.CertGetNameStringW
        name.argtypes, name.restype = [C.c_void_p, W.DWORD, W.DWORD, C.c_void_p, W.LPWSTR, W.DWORD], W.DWORD
        action = C.create_string_buffer(UUID('00aac56b-cd44-11d0-8cc2-00c04fc295ee').bytes_le)
        file = _File(C.sizeof(_File), str(path), None, None)
        data = _Trust(C.sizeof(_Trust), None, None, 2, 0, 1, C.pointer(file), 1, None, None, 0x1010, 0, None)
        try:
            if verify(W.HWND(-1), action, C.byref(data)) != 0:
                return False
            info = provider(data.state)
            who = signer(info, 0, False, 0) if info else None
            if not who or not who.contents.count or not who.contents.certs:
                return False
            cert = C.cast(who.contents.certs, C.POINTER(_Certificate)).contents.context
            output = C.create_unicode_buffer(256)
            return (name(cert, 3, 0, C.c_char_p(b'2.5.4.10'), output, len(output)) > 1
                    and output.value == 'Microsoft Corporation')
        finally:
            data.action = 2
            verify(W.HWND(-1), action, C.byref(data))
    except (OSError, AttributeError, ValueError):
        return False


def _known_folder(csidl):
    output = C.create_unicode_buffer(32768)
    shell = C.WinDLL('shell32', winmode=0x800)
    call = shell.SHGetFolderPathW
    call.argtypes, call.restype = [W.HWND, C.c_int, W.HANDLE, W.DWORD, W.LPWSTR], W.LONG
    return Path(output.value) if call(None, csidl, None, 0, output) == 0 else None


def discover():
    if os.name != 'nt':
        return None
    try:
        common, program = _known_folder(0x23), _known_folder(0x26)
        candidates = []
        platform = safe(common / 'Microsoft/Windows Defender/Platform') if common else None
        if platform and platform.is_dir():
            candidates += [p / 'MpCmdRun.exe' for p in sorted(platform.iterdir(), reverse=True)
                           if re.fullmatch(r'[0-9.]+-?[0-9]*', p.name)]
        if program:
            candidates.append(program / 'Windows Defender/MpCmdRun.exe')
        for path in candidates:
            if safe(path).is_file() and _trusted_microsoft(path):
                return path
    except Exception:
        pass
    return None


def classify(returncode, output):
    # Localized/unknown output is deliberately inconclusive. Do not store the
    # complete console text (it can contain arbitrary filenames/user paths).
    threats = re.findall(r'(?im)^\s*(?:Threat\s+Name|威胁名称)\s*:\s*([A-Za-z][A-Za-z0-9_.:/!\-]{1,180})\s*$', output)
    if threats and returncode in (0, 2):
        return dict(state='detected', reason='engine_detection', detections=list(dict.fromkeys(threats))[:24])
    if returncode == 0 and re.search(r'(?im)^\s*(?:No (?:threats|malware) (?:found|detected)\.?|未(?:发现|检测到)(?:任何)?威胁[。.]?)\s*$', output):
        return dict(state='clean', reason='custom_scan_completed_no_detection', detections=[])
    return dict(state='incomplete', reason='scan_error_or_unrecognized_detection', detections=[])


class DefenderScanner:
    def scan(self, root, *, cancelled=lambda: False):
        from .service import antivirus_state, now
        base = antivirus_state()
        executable = discover()
        if executable is None:
            return {**base, 'state': 'unavailable', 'reason': 'trusted_engine_unavailable'}
        process = None
        try:
            root = safe(root)
            if cancelled():
                return {**base, 'state': 'incomplete', 'reason': 'cancelled'}
            # The trusted system binary is held without write/delete sharing
            # across verification and CreateProcess to close replacement races.
            kernel = C.WinDLL('kernel32', winmode=0x800)
            create = kernel.CreateFileW
            create.argtypes, create.restype = [W.LPCWSTR,W.DWORD,W.DWORD,C.c_void_p,W.DWORD,W.DWORD,W.HANDLE], W.HANDLE
            close = kernel.CloseHandle
            close.argtypes, close.restype = [W.HANDLE], W.BOOL
            handle = create(str(executable), 0x80000000, 1, None, 3, 0, None)
            if handle == C.c_void_p(-1).value:
                return {**base, 'state': 'unavailable', 'reason': 'engine_pin_failed'}
            try:
                if not _trusted_microsoft(executable):
                    return {**base, 'state': 'unavailable', 'reason': 'engine_signature_invalid'}
                with tempfile.TemporaryFile() as output:
                    process = subprocess.Popen([str(executable), '-Scan', '-ScanType', '3', '-File', str(root), '-DisableRemediation'],
                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                        cwd=str(executable.parent), creationflags=subprocess.CREATE_NO_WINDOW)
                    deadline = time.monotonic() + 180
                    while process.poll() is None:
                        if cancelled() or time.monotonic() >= deadline or output.tell() > 1024 * 1024:
                            process.kill(); process.wait()
                            return {**base, 'state': 'incomplete', 'reason': 'cancelled_or_limit', 'checked_at': now()}
                        time.sleep(.1)
                    output.seek(0); raw = output.read(1024 * 1024 + 1)
                    if len(raw) > 1024 * 1024:
                        result = dict(state='incomplete', reason='output_limit', detections=[])
                    else:
                        encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8'
                        result = classify(process.returncode, raw.decode(encoding, errors='replace'))
                    return {**base, **result, 'checked_at': now(), 'return_code': process.returncode}
            finally:
                close(handle)
        except Exception:
            return {**base, 'state': 'incomplete', 'reason': 'engine_invocation_failed', 'checked_at': now()}
        finally:
            if process is not None and process.poll() is None:
                process.kill(); process.wait()
