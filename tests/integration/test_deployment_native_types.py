"""Repeated real Windows calls must not retain a new ABI type per file step."""
import ctypes
from ctypes import wintypes
import sys

import pytest
import win32api

from backend.deployments.paths import (
    GuardRejected, _rename_handle, open_handle, pinned_chain, pinned_file, strong_hash,
)
from backend.deployments.process_guard import check_file_process_query, file_process_ids


pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Real Windows native calls required')


def handle_count():
    query = ctypes.WinDLL('kernel32', use_last_error=True).GetProcessHandleCount
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    query.restype = wintypes.BOOL
    count = wintypes.DWORD()
    if not query(int(win32api.GetCurrentProcess()), ctypes.byref(count)):
        raise ctypes.WinError(ctypes.get_last_error())
    return count.value


def test_repeated_native_file_and_process_calls_reuse_types_and_keep_no_replace(tmp_path_factory):
    owned = tmp_path_factory.mktemp('nt')
    source, moved, occupied = (owned/name for name in ('source.bin', 'moved.bin', 'occupied.bin'))
    payload = b'owned native type regression'
    source.write_bytes(payload)
    occupied.write_bytes(b'preserve existing target')

    def cycle():
        with pinned_file(source) as handle:
            assert strong_hash(handle)
            assert file_process_ids(handle) == []
        check_file_process_query()  # Positive control against this owned Python process.
        with pinned_chain(owned) as parent:
            handle = open_handle(source, parent=parent.handles[-1], update=True, delete=True)
            try:
                with pytest.raises(GuardRejected) as rejected:
                    _rename_handle(handle, parent.handles[-1], occupied.name)
                assert rejected.value.code == 'GUARD_IDENTITY_CHANGED'
                _rename_handle(handle, parent.handles[-1], moved.name)
                _rename_handle(handle, parent.handles[-1], source.name)
            finally:
                handle.Close()

    cycle()  # Warm the finite ctypes pointer/array signatures.
    handles = handle_count()
    cached = len(ctypes._pointer_type_cache)
    for _ in range(30):
        cycle()
    assert len(ctypes._pointer_type_cache) == cached
    assert handle_count() == handles
    assert source.read_bytes() == payload and occupied.read_bytes() == b'preserve existing target'
    assert not moved.exists()
