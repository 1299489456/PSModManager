"""Read-only generation hints on inert, isolated files; no game is executed."""
import hashlib
import os

import pytest

from backend.deployments.paths import GuardRejected
from backend.loaders.melonloader import generation as generation_module
from backend.loaders.melonloader.generation import AssemblyGeneration, BLOCK_SIZE, CONFIG


@pytest.fixture
def game(tmp_path):
    (tmp_path / 'GameAssembly.dll').write_bytes(b'current inert game assembly')
    (tmp_path / CONFIG).parent.mkdir(parents=True)
    return tmp_path


def record(game, value):
    (game / CONFIG).write_text('[Il2CppAssemblyGenerator]\nGameAssemblyHash = "' + value + '"\n', encoding='utf-8')


def digest(game):
    return hashlib.sha512((game / 'GameAssembly.dll').read_bytes()).hexdigest().upper()


def test_changed_game_reports_pending_without_writing_anything(game):
    record(game, 'A' * 128)
    before = (game / CONFIG).read_bytes()
    value = AssemblyGeneration().assess(game, '0.7.3')
    assert value['status'] == 'pending'
    assert '下次启动' in value['message'] and '较长时间' in value['message']
    assert (game / CONFIG).read_bytes() == before


def test_matching_full_sha512_record_is_matched(game):
    record(game, digest(game))
    assert AssemblyGeneration().assess(game, '0.7.3+official-build')['status'] == 'matched'


def test_lowercase_record_follows_official_case_sensitive_comparison(game):
    record(game, digest(game).lower())
    assert AssemblyGeneration().assess(game, '0.7.3')['status'] == 'pending'


@pytest.mark.parametrize('content', [None, '', '[Il2CppAssemblyGenerator]\n', '[Il2CppAssemblyGenerator]\nGameAssemblyHash = ""\n'])
def test_first_generation_without_saved_hash_does_not_hash_or_write(game, content, monkeypatch):
    if content is not None:
        (game / CONFIG).write_text(content, encoding='utf-8')
    state = AssemblyGeneration()
    monkeypatch.setattr(state, '_hash', lambda *args: pytest.fail('No saved digest to compare'))
    assert state.assess(game, '0.7.3')['status'] == 'not_generated'
    assert (game / CONFIG).exists() == (content is not None)


@pytest.mark.parametrize('content', ['[Il2CppAssemblyGenerator]\nGameAssemblyHash = "bad"',
    '[Il2CppAssemblyGenerator]\nGameAssemblyHash = 123',
    '[Il2CppAssemblyGenerator]\nGameAssemblyHash = [', 'Il2CppAssemblyGenerator = false', '\ufffd', 'x' * (1024 * 1024 + 1)],
    ids=['bad-hash', 'bad-type', 'bad-toml', 'bad-section', 'bad-text', 'oversized'])
def test_invalid_generator_record_stays_unknown(game, content):
    path = game / CONFIG
    path.write_text(content, encoding='utf-8')
    assert AssemblyGeneration().assess(game, '0.7.3')['status'] == 'unknown'
    assert path.read_text(encoding='utf-8') == content


def test_missing_assembly_and_unsupported_loader_stay_unknown(game):
    record(game, digest(game))
    state = AssemblyGeneration()
    assert state.assess(game, '0.7.4')['status'] == 'unknown'
    (game / 'GameAssembly.dll').unlink()
    assert state.assess(game, '0.7.3')['status'] == 'unknown'


def test_hardlinked_assembly_is_not_read(game):
    record(game, digest(game))
    os.link(game / 'GameAssembly.dll', game / 'other.dll')
    assert AssemblyGeneration().assess(game, '0.7.3')['status'] == 'unknown'


def test_cached_file_is_reused_but_size_mtime_and_identity_changes_invalidate(game, monkeypatch):
    state = AssemblyGeneration()
    path = game / 'GameAssembly.dll'
    record(game, digest(game))
    original = generation_module.hashlib.sha512
    hashes = []
    def count_hash():
        hashes.append(True)
        return original()
    monkeypatch.setattr(generation_module.hashlib, 'sha512', count_hash)
    assert state.assess(game, '0.7.3')['status'] == 'matched'
    assert state.assess(game, '0.7.3')['status'] == 'matched'
    assert len(hashes) == 1
    # A new generator record must be read even while the DLL digest is cached.
    record(game, 'A' * 128)
    assert state.assess(game, '0.7.3')['status'] == 'pending'
    assert len(hashes) == 1
    stamp = path.stat()
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 2_000_000_000))
    assert state.assess(game, '0.7.3')['status'] == 'pending'
    assert len(hashes) == 2
    stamp = path.stat()
    path.write_bytes(b'longer updated game assembly bytes')
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert state.assess(game, '0.7.3')['status'] == 'pending'
    assert len(hashes) == 3
    stamp = path.stat()
    replacement = game / 'replacement.dll'
    replacement.write_bytes(b'x' * stamp.st_size)
    os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    replacement.replace(path)
    assert state.assess(game, '0.7.3')['status'] == 'pending'
    assert len(hashes) == 4


@pytest.mark.parametrize('error', [RuntimeError('cancelled'), GuardRejected('GUARD_PROCESS_RUNNING')])
def test_cancellation_during_hash_is_propagated_and_not_cached(game, error):
    path = game / 'GameAssembly.dll'
    path.write_bytes(b'x' * (BLOCK_SIZE + 1))
    record(game, digest(game))
    state = AssemblyGeneration()
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        if calls == 6:  # After the first GameAssembly block, not Config.cfg.
            raise error
    with pytest.raises(type(error)) as rejected:
        state.assess(game, '0.7.3', check=cancel)
    assert rejected.value is error and not state._cache
    assert state.assess(game, '0.7.3')['status'] == 'matched'


def test_changed_metadata_during_read_is_unknown_and_never_cached(game, monkeypatch):
    record(game, digest(game))
    state = AssemblyGeneration()
    original = generation_module._signature
    calls = 0
    def changed(handle):
        nonlocal calls
        calls += 1
        value = original(handle)
        return (*value[:3], value[3] + 1, *value[4:]) if calls == 4 else value
    monkeypatch.setattr(generation_module, '_signature', changed)
    assert state.assess(game, '0.7.3')['status'] == 'unknown'
    assert not state._cache
    monkeypatch.setattr(generation_module, '_signature', original)
    assert state.assess(game, '0.7.3')['status'] == 'matched'


def test_cache_has_a_fixed_installation_limit(game):
    state = AssemblyGeneration()
    for index in range(10):
        root = game / str(index)
        root.mkdir()
        (root / 'GameAssembly.dll').write_bytes(b'inert')
        state._hash(root, lambda: None)
    assert len(state._cache) == 8


def test_native_file_lock_is_reported_as_unknown(game):
    import win32con
    import win32file
    record(game, digest(game))
    handle = win32file.CreateFile(str(game / 'GameAssembly.dll'), win32con.GENERIC_READ,
        0, None, win32con.OPEN_EXISTING, 0, None)
    try:
        assert AssemblyGeneration().assess(game, '0.7.3')['status'] == 'unknown'
    finally:
        handle.Close()
