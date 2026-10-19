"""Real Windows handle rename primitives for PS-03-03 synthetic installations.

These tests exercise the filesystem boundary. The executor/recovery consumer
and process-kill transaction acceptance are tested separately.
"""
from contextlib import contextmanager
import hashlib
import os
import struct
import sys
from uuid import uuid4

import pytest
import pywintypes
import win32con
import win32file

from backend.deployments import paths
from backend.deployments.paths import (GuardRejected, capture_target, file_identity,
    flush_file, guarded_target, native_rename, open_handle, open_recovery_handle,
    pinned_chain, quarantine_file, strong_hash)


pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Real Windows handle rename required')
OLD = b'OLD complete bytes'
NEW = b'NEW complete bytes'


@pytest.fixture
def installation(tmp_path):
    root, private = tmp_path/'synthetic-installation', tmp_path/'private-transaction'
    root.mkdir()
    private.mkdir()
    for name in paths.ROOTS.values():
        (root/name).mkdir()
    with pinned_chain(root) as chain:
        installation_identity = chain[-1][1]
    return root, private, installation_identity


@contextmanager
def staged_file(private):
    (private/'staged.bin').write_bytes(NEW)
    with pinned_chain(private) as chain:
        handle = open_handle(private/'staged.bin', parent=chain.handles[-1], update=True, delete=True)
        try:
            flush_file(handle)
            yield handle, chain
        finally:
            handle.Close()


def read_live(path):
    handle = win32file.CreateFile(str(path), win32con.GENERIC_READ, 7, None,
                                win32con.OPEN_EXISTING, 0, None)
    try:
        return win32file.ReadFile(handle, 4096)[1]
    finally:
        handle.Close()


def reparse(directory, destination=None):
    handle = win32file.CreateFile(str(directory), 0x100, 7, None,
        win32con.OPEN_EXISTING, win32con.FILE_FLAG_BACKUP_SEMANTICS | 0x00200000, None)
    try:
        if destination is None:
            # Removes only the reparse metadata, not the synthetic directory.
            win32file.DeviceIoControl(handle, 0x900AC, struct.pack('<LHH', 0xa0000003, 0, 0), None)
            return
        substitute = ('\\??\\' + str(destination)).encode('utf-16-le')
        display = str(destination).encode('utf-16-le')
        payload = substitute + b'\0\0' + display + b'\0\0'
        buffer = struct.pack('<LHHHHHH', 0xa0000003, len(payload) + 8, 0,
            0, len(substitute), len(substitute)+2, len(display)) + payload
        win32file.DeviceIoControl(handle, 0x900A4, buffer, None)
    finally:
        handle.Close()


def custom_reparse(path, guid=None):
    """Own synthetic third-party tag; requires no symbolic-link privilege."""
    handle = win32file.CreateFile(str(path), 0x100, 7, None,
        win32con.OPEN_EXISTING, 0x00200000, None)
    try:
        if guid is not None:
            win32file.DeviceIoControl(handle, 0x900AC, struct.pack('<LHH', 0x42, 0, 0)+guid, None)
            return
        generated = uuid4().bytes_le
        payload = b'SYNTHETIC'
        win32file.DeviceIoControl(handle, 0x900A4,
            struct.pack('<LHH', 0x42, len(payload), 0)+generated+payload, None)
        return generated
    finally:
        handle.Close()


@pytest.mark.parametrize('root_kind', list(paths.ROOTS))
def test_two_no_replace_renames_consume_fixed_handles_and_keep_old_bytes(installation, root_kind):
    root, private, identity = installation
    target = root/paths.ROOTS[root_kind]/'mod.dll'
    target.write_bytes(OLD)
    snapshot = capture_target(root, identity, root_kind, 'mod.dll')
    checks = []
    with staged_file(private) as (staged, private_chain):
        staged_identity = file_identity(paths._information(staged, False))
        with guarded_target(snapshot, lambda: checks.append('checked'), delete=True) as pinned:
            old_identity = file_identity(paths._information(pinned.handle, False))
            pinned.rename_to(private_chain.handles[-1], 'old.bin')
            assert not target.exists()
            assert file_identity(paths._information(pinned.handle, False)) == old_identity
            assert read_live(private/'old.bin') == OLD
            native_rename(staged, pinned.parent_handle, pinned.leaf)
            flush_file(staged)
            assert file_identity(paths._information(staged, False)) == staged_identity
            assert read_live(target) == NEW
            assert read_live(private/'old.bin') == OLD
    assert checks and target.read_bytes() == NEW and (private/'old.bin').read_bytes() == OLD
    assert (private/'old.bin').parent != target.parent


def test_initial_delete_access_is_required_and_no_raw_path_reopen(installation):
    root, private, identity = installation
    target = root/'Mods/mod.dll'
    target.write_bytes(OLD)
    snapshot = capture_target(root, identity, 'mods', 'mod.dll')
    with pinned_chain(private) as private_chain:
        with guarded_target(snapshot, lambda: None) as pinned:
            with pytest.raises(GuardRejected, match='GUARD_PATH_UNAVAILABLE'):
                pinned.rename_to(private_chain.handles[-1], 'old.bin')
            with pytest.raises(pywintypes.error) as denied:
                reopened = win32file.CreateFile(str(target), win32con.GENERIC_READ | win32con.GENERIC_WRITE | win32con.DELETE,
                                               7, None, win32con.OPEN_EXISTING, 0, None)
                reopened.Close()
            assert denied.value.winerror in (5, 32)
    assert target.read_bytes() == OLD and not (private/'old.bin').exists()


@pytest.mark.parametrize('kind', ['file', 'hardlink', 'directory', 'junction'])
def test_late_destination_collision_never_replaces_foreign_entry(installation, tmp_path, kind):
    root, private, identity = installation
    target = root/'Mods/mod.dll'
    target.write_bytes(OLD)
    external = tmp_path/'foreign-sentinel.dll'
    external.write_bytes(b'FOREIGN')
    snapshot = capture_target(root, identity, 'mods', 'mod.dll')
    with staged_file(private) as (staged, private_chain):
        with guarded_target(snapshot, lambda: None, delete=True) as pinned:
            pinned.rename_to(private_chain.handles[-1], 'old.bin')
            if kind == 'file':
                target.write_bytes(b'FOREIGN')
            elif kind == 'hardlink':
                os.link(external, target)
            else:
                target.mkdir()
                if kind == 'junction':
                    outside = tmp_path/'foreign-directory'
                    outside.mkdir()
                    (outside/'sentinel.dll').write_bytes(b'FOREIGN')
                    reparse(target, outside)
            try:
                with pytest.raises(GuardRejected) as refused:
                    native_rename(staged, pinned.parent_handle, pinned.leaf)
                assert refused.value.code in ('GUARD_IDENTITY_CHANGED', 'GUARD_PATH_UNAVAILABLE', 'GUARD_REPARSE_POINT')
                assert read_live(private/'old.bin') == OLD
                assert read_live(private/'staged.bin') == NEW
                if kind == 'junction':
                    assert target.lstat().st_file_attributes & 0x400
                    assert (outside/'sentinel.dll').read_bytes() == b'FOREIGN'
            finally:
                if kind == 'junction' and target.lstat().st_file_attributes & 0x400:
                    reparse(target)
    assert external.read_bytes() == b'FOREIGN'
    assert target.is_dir() if kind in ('directory', 'junction') else target.read_bytes() == b'FOREIGN'


@pytest.mark.parametrize('leaf', ['../escape.dll', 'nested/new.dll', 'nested\\new.dll',
                                 '/absolute.dll', 'C:/escape.dll', 'mod.dll:stream', 'CON', 'mod.dll.'])
def test_native_rename_rejects_nonleaf_and_unsafe_names_before_native_call(installation, leaf):
    _, private, _ = installation
    with staged_file(private) as (staged, chain):
        with pytest.raises(GuardRejected, match='GUARD_PATH_INVALID'):
            native_rename(staged, chain.handles[-1], leaf)
        assert read_live(private/'staged.bin') == NEW


@pytest.mark.parametrize('which', ['installation', 'target-parent', 'private-parent'])
def test_pinned_directories_deny_rename_rebinding_while_permitting_required_write_sharing(installation, which):
    root, private, _ = installation
    selected = {'installation': root, 'target-parent': root/'Mods', 'private-parent': private}[which]
    with pinned_chain(root/'Mods'), pinned_chain(private):
        with pytest.raises(OSError) as refused:
            os.rename(selected, selected.with_name(selected.name+'-rebound'))
        assert refused.value.winerror in (5, 32)
        assert selected.is_dir()
        # A directory writer can coexist; it still cannot replace the directory.
        writer = win32file.CreateFile(str(selected), win32con.GENERIC_WRITE, 7, None,
            win32con.OPEN_EXISTING, win32con.FILE_FLAG_BACKUP_SEMANTICS | 0x00200000, None)
        writer.Close()


def test_files_keep_read_only_sharing_even_when_owner_has_delete_access(installation):
    _, private, _ = installation
    with staged_file(private) as (staged, _):
        for access in (win32con.GENERIC_WRITE, win32con.DELETE, win32con.GENERIC_READ | win32con.DELETE):
            with pytest.raises(pywintypes.error) as refused:
                writer = win32file.CreateFile(str(private/'staged.bin'), access, 7, None,
                                              win32con.OPEN_EXISTING, 0, None)
                writer.Close()
            assert refused.value.winerror in (5, 32)
        assert strong_hash(staged) == hashlib.sha256(NEW).hexdigest()


def test_actual_kernel_rename_refuses_parent_reparse_installed_after_real_preflight(installation, tmp_path, monkeypatch):
    root, private, _ = installation
    parent, outside = root/'Mods', tmp_path/'outside-sentinel'
    outside.mkdir()
    (outside/'sentinel.dll').write_bytes(OLD)
    trace = []
    with staged_file(private) as (staged, _), pinned_chain(parent) as target_chain:
        target_handle = target_chain.handles[-1]
        actual_information = paths._information

        def attack_after_actual_check(handle, directory):
            information = actual_information(handle, directory)
            if directory and int(handle) == int(target_handle) and not trace:
                # Deterministic race scheduling only: the original OS query and
                # the following NtSetInformationFile are both real operations.
                reparse(parent, outside)
                trace.append('junction-installed-after-preflight')
            return information

        monkeypatch.setattr(paths, '_information', attack_after_actual_check)
        try:
            with pytest.raises(GuardRejected, match='GUARD_REPARSE_POINT'):
                native_rename(staged, target_handle, 'new.dll')
            assert trace and parent.lstat().st_file_attributes & 0x400
            assert not (outside/'new.dll').exists()
            assert (outside/'sentinel.dll').read_bytes() == OLD
            assert read_live(private/'staged.bin') == NEW
        finally:
            monkeypatch.setattr(paths, '_information', actual_information)
            if parent.lstat().st_file_attributes & 0x400:
                reparse(parent)
    assert not (parent/'new.dll').exists()


def test_file_flush_is_real_and_directory_flush_is_not_promised(installation):
    _, private, _ = installation
    with staged_file(private) as (staged, chain):
        win32file.WriteFile(staged, b'NEW')
        flush_file(staged)
        with pytest.raises(GuardRejected, match='GUARD_PATH_UNAVAILABLE'):
            flush_file(chain.handles[-1])
    assert (private/'staged.bin').read_bytes().startswith(b'NEW')


def test_source_metadata_race_keeps_fixed_handle_available_for_identity_bound_quarantine(installation, tmp_path, monkeypatch):
    root, private, _ = installation
    parent = root/'Mods'
    external = tmp_path/'external-sentinel.dll'
    external.write_bytes(OLD)
    attack = []
    with staged_file(private) as (staged, private_chain), pinned_chain(parent) as target_chain:
        expected = file_identity(paths._information(staged, False))
        actual_information = paths._information

        def attack_after_real_source_check(handle, directory):
            information = actual_information(handle, directory)
            if not directory and int(handle) == int(staged) and not attack:
                attack.append(custom_reparse(private/'staged.bin'))
            return information

        monkeypatch.setattr(paths, '_information', attack_after_real_source_check)
        try:
            with pytest.raises(GuardRejected, match='GUARD_REPARSE_POINT'):
                native_rename(staged, target_chain.handles[-1], 'new.dll')
            assert attack and file_identity(win32file.GetFileInformationByHandle(staged)) == expected
            assert (parent/'new.dll').lstat().st_file_attributes & 0x400
            # No reopen of the checked path: quarantine consumes the same
            # handle even though the native move completed before rejection.
            quarantine_file(staged, private_chain.handles[-1], 'quarantined.bin', expected)
            assert not (parent/'new.dll').exists()
            assert (private/'quarantined.bin').lstat().st_file_attributes & 0x400
            assert external.read_bytes() == OLD
        finally:
            monkeypatch.setattr(paths, '_information', actual_information)
            for candidate in (private/'staged.bin', private/'quarantined.bin', parent/'new.dll'):
                if candidate.exists() and candidate.lstat().st_file_attributes & 0x400 and attack:
                    custom_reparse(candidate, attack[0])
        assert strong_hash(staged) == hashlib.sha256(NEW).hexdigest()
    assert (private/'quarantined.bin').read_bytes() == NEW


@pytest.mark.parametrize('changed_to_reparse', [False, True])
def test_recovery_reopens_only_expected_final_object_and_has_no_data_write_access(installation, changed_to_reparse):
    root, private, _ = installation
    parent, target = root/'Mods', root/'Mods/new.dll'
    with staged_file(private) as (staged, _), pinned_chain(parent) as target_chain:
        expected = file_identity(paths._information(staged, False))
        native_rename(staged, target_chain.handles[-1], target.name)
        guid = custom_reparse(target) if changed_to_reparse else None
    with pinned_chain(parent) as target_chain, pinned_chain(private) as private_chain:
        handle = open_recovery_handle(target_chain.handles[-1], target.name, expected)
        try:
            assert file_identity(win32file.GetFileInformationByHandle(handle)) == expected
            with pytest.raises(pywintypes.error) as denied:
                win32file.WriteFile(handle, b'FORBIDDEN')
            assert denied.value.winerror == 5
            with pytest.raises(pywintypes.error) as denied_read:
                win32file.ReadFile(handle, 1)
            assert denied_read.value.winerror == 5
            quarantine_file(handle, private_chain.handles[-1], 'quarantined.bin', expected)
            assert not target.exists()
        finally:
            handle.Close()
    if guid is not None:
        custom_reparse(private/'quarantined.bin', guid)
    assert (private/'quarantined.bin').read_bytes() == NEW


def test_recovery_rejects_wrong_file_identity_and_parent_reparse_without_touching_target(installation, tmp_path):
    root, _, _ = installation
    parent, target = root/'Mods', root/'Mods/new.dll'
    target.write_bytes(NEW)
    guid = custom_reparse(target)
    with pinned_chain(parent) as chain:
        with pytest.raises(GuardRejected, match='GUARD_IDENTITY_CHANGED'):
            open_recovery_handle(chain.handles[-1], target.name, '0:0')
    custom_reparse(target, guid)
    expected = capture_target(root, installation[2], 'mods', target.name).target_identity
    outside = tmp_path/'outside-recovery'
    outside.mkdir()
    (outside/'new.dll').write_bytes(OLD)
    empty_parent = parent/'empty'
    empty_parent.mkdir()
    with pinned_chain(empty_parent) as chain:
        reparse(empty_parent, outside)
        try:
            with pytest.raises(GuardRejected, match='GUARD_REPARSE_POINT'):
                open_recovery_handle(chain.handles[-1], target.name, expected)
            assert (outside/'new.dll').read_bytes() == OLD
        finally:
            reparse(empty_parent)
    assert target.read_bytes() == NEW


def test_quarantine_never_overwrites_existing_private_evidence(installation):
    root, private, _ = installation
    target = root/'Mods/new.dll'
    target.write_bytes(NEW)
    (private/'quarantined.bin').write_bytes(OLD)
    snapshot = capture_target(root, installation[2], 'mods', target.name)
    with guarded_target(snapshot, lambda: None, delete=True) as pinned, pinned_chain(private) as chain:
        with pytest.raises(GuardRejected, match='GUARD_IDENTITY_CHANGED'):
            quarantine_file(pinned.handle, chain.handles[-1], 'quarantined.bin', snapshot.target_identity)
    assert target.read_bytes() == NEW and (private/'quarantined.bin').read_bytes() == OLD


def test_recovery_parent_reparse_race_after_actual_preflight_never_returns_an_external_object(installation, tmp_path, monkeypatch):
    root, _, identity = installation
    target = root/'Mods/owned.dll'
    target.write_bytes(NEW)
    expected = capture_target(root, identity, 'mods', target.name).target_identity
    parent, outside = root/'Mods/empty', tmp_path/'outside-race'
    parent.mkdir()
    outside.mkdir()
    (outside/'owned.dll').write_bytes(OLD)
    actual_information = paths._information
    attack = []
    with pinned_chain(parent) as chain:
        parent_handle = chain.handles[-1]

        def attack_after_actual_check(handle, directory):
            information = actual_information(handle, directory)
            if directory and int(handle) == int(parent_handle) and not attack:
                reparse(parent, outside)
                attack.append('converted-after-parent-check')
            return information

        monkeypatch.setattr(paths, '_information', attack_after_actual_check)
        try:
            with pytest.raises(GuardRejected) as refused:
                handle = open_recovery_handle(parent_handle, target.name, expected)
                handle.Close()
            assert refused.value.code in ('GUARD_REPARSE_POINT', 'GUARD_IDENTITY_CHANGED', 'GUARD_PATH_UNAVAILABLE')
            assert attack and (outside/'owned.dll').read_bytes() == OLD
        finally:
            monkeypatch.setattr(paths, '_information', actual_information)
            if parent.lstat().st_file_attributes & 0x400:
                reparse(parent)
    assert target.read_bytes() == NEW


def test_quarantine_rejects_identical_bytes_with_a_different_physical_identity(installation):
    root, private, identity = installation
    owned, unrelated = root/'Mods/owned.dll', root/'Mods/unrelated.dll'
    owned.write_bytes(NEW)
    unrelated.write_bytes(NEW)
    expected = capture_target(root, identity, 'mods', owned.name).target_identity
    other_snapshot = capture_target(root, identity, 'mods', unrelated.name)
    assert other_snapshot.before_hash == hashlib.sha256(NEW).hexdigest()
    assert other_snapshot.target_identity != expected
    with guarded_target(other_snapshot, lambda: None, delete=True) as other, pinned_chain(private) as chain:
        with pytest.raises(GuardRejected, match='GUARD_IDENTITY_CHANGED'):
            quarantine_file(other.handle, chain.handles[-1], 'quarantined.bin', expected)
    assert owned.read_bytes() == unrelated.read_bytes() == NEW
    assert not (private/'quarantined.bin').exists()


def test_hardlink_race_after_actual_source_check_isolates_primary_and_preserves_foreign_alias(installation, tmp_path, monkeypatch):
    root, private, _ = installation
    parent, foreign = root/'Mods', tmp_path/'foreign-alias.dll'
    attack = []
    with staged_file(private) as (staged, private_chain), pinned_chain(parent) as target_chain:
        expected = file_identity(paths._information(staged, False))
        actual_information = paths._information

        def create_alias_after_actual_check(handle, directory):
            information = actual_information(handle, directory)
            if not directory and int(handle) == int(staged) and not attack:
                os.link(private/'staged.bin', foreign)
                attack.append('hardlink-after-source-preflight')
            return information

        monkeypatch.setattr(paths, '_information', create_alias_after_actual_check)
        try:
            with pytest.raises(GuardRejected, match='GUARD_HARDLINK'):
                native_rename(staged, target_chain.handles[-1], 'new.dll')
            assert attack and win32file.GetFileInformationByHandle(staged)[7] == 2
            assert (parent/'new.dll').exists()
            quarantine_file(staged, private_chain.handles[-1], 'quarantined.bin', expected)
            assert not (parent/'new.dll').exists()
            assert file_identity(win32file.GetFileInformationByHandle(staged)) == expected
            assert read_live(foreign) == read_live(private/'quarantined.bin') == NEW
            # Quarantine's narrow exception must not leak into normal apply.
            with pytest.raises(GuardRejected, match='GUARD_HARDLINK'):
                native_rename(staged, target_chain.handles[-1], 'new.dll')
        finally:
            monkeypatch.setattr(paths, '_information', actual_information)
    assert foreign.read_bytes() == (private/'quarantined.bin').read_bytes() == NEW


def test_recovery_isolates_expected_primary_with_multiple_links_and_leaves_unknown_alias(installation, tmp_path):
    root, private, _ = installation
    parent, target, foreign = root/'Mods', root/'Mods/new.dll', tmp_path/'foreign-alias.dll'
    with staged_file(private) as (staged, _), pinned_chain(parent) as target_chain:
        expected = file_identity(paths._information(staged, False))
        native_rename(staged, target_chain.handles[-1], target.name)
        os.link(target, foreign)
        assert win32file.GetFileInformationByHandle(staged)[7] == 2
    with pinned_chain(parent) as target_chain, pinned_chain(private) as private_chain:
        handle = open_recovery_handle(target_chain.handles[-1], target.name, expected)
        try:
            quarantine_file(handle, private_chain.handles[-1], 'quarantined.bin', expected)
            assert not target.exists()
            assert read_live(foreign) == read_live(private/'quarantined.bin') == NEW
        finally:
            handle.Close()
    assert foreign.read_bytes() == (private/'quarantined.bin').read_bytes() == NEW
