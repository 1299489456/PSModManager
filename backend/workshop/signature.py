"""OS Authenticode verification of the pinned Steam API binary, no shell."""
import ctypes as C
from ctypes import wintypes as W
from uuid import UUID


class _File(C.Structure):
    _fields_ = [('size', W.DWORD), ('path', W.LPCWSTR), ('file', W.HANDLE), ('subject', C.c_void_p)]


class _Trust(C.Structure):
    _fields_ = [('size', W.DWORD), ('policy', C.c_void_p), ('sip', C.c_void_p),
                ('ui', W.DWORD), ('revocation', W.DWORD), ('choice', W.DWORD),
                ('file', C.POINTER(_File)), ('action', W.DWORD), ('state', W.HANDLE),
                ('url', W.LPCWSTR), ('flags', W.DWORD), ('context', W.DWORD), ('settings', C.c_void_p)]


class _Signer(C.Structure):
    # The prefix through the signer certificate array is sufficient.
    _fields_ = [('size', W.DWORD), ('time', W.FILETIME), ('count', W.DWORD), ('certs', C.c_void_p)]


class _Certificate(C.Structure):
    _fields_ = [('size', W.DWORD), ('context', C.c_void_p)]


def trusted_dll(path):
    try:
        trust = C.WinDLL('wintrust', winmode=0x800)
        crypt = C.WinDLL('crypt32', winmode=0x800)
        verify = trust.WinVerifyTrust
        verify.argtypes, verify.restype = [W.HWND, C.c_void_p, C.c_void_p], W.LONG
        data_from = trust.WTHelperProvDataFromStateData
        data_from.argtypes, data_from.restype = [W.HANDLE], C.c_void_p
        signer_from = trust.WTHelperGetProvSignerFromChain
        signer_from.argtypes, signer_from.restype = [C.c_void_p, W.DWORD, W.BOOL, W.DWORD], C.POINTER(_Signer)
        name = crypt.CertGetNameStringW
        name.argtypes, name.restype = [C.c_void_p, W.DWORD, W.DWORD, C.c_void_p, W.LPWSTR, W.DWORD], W.DWORD
        action = C.create_string_buffer(UUID('00aac56b-cd44-11d0-8cc2-00c04fc295ee').bytes_le)
        file = _File(C.sizeof(_File), str(path), None, None)
        # Verify Authenticode locally; no UI, downloading certificates, or online
        # revocation request in a passive inventory read. OS trust must succeed.
        data = _Trust(C.sizeof(_Trust), None, None, 2, 0, 1, C.pointer(file), 1, None, None, 0x1010, 0, None)
        try:
            if verify(W.HWND(-1), action, C.byref(data)) != 0:
                return False
            provider = data_from(data.state)
            signer = signer_from(provider, 0, False, 0) if provider else None
            if not signer or not signer.contents.count or not signer.contents.certs:
                return False
            cert = C.cast(signer.contents.certs, C.POINTER(_Certificate)).contents.context
            output = C.create_unicode_buffer(256)
            # Ask specifically for the Organization attribute, not a friendly
            # display name that another subject attribute could replace.
            organization = C.c_char_p(b'2.5.4.10')
            if name(cert, 3, 0, organization, output, len(output)) <= 1:
                return False
            return output.value == 'Valve Corp.'
        finally:
            data.action = 2  # WTD_STATEACTION_CLOSE frees provider state.
            verify(W.HWND(-1), action, C.byref(data))
    except (OSError, ValueError, AttributeError):
        return False
