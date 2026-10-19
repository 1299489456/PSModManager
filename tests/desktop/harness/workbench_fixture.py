"""Self-authored, offline compiled installation; no real game bytes or execution."""
import hashlib
import json
from pathlib import Path
import shutil

from tests.integration.test_content_catalog import compile_fixture, manifest, BUILD
from tests.unit.test_installation_layout import make_installation


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob('*')) if p.is_file()}


def create(root):
    root = Path(root)
    game = make_installation(root / '合成安装 %')
    mods = game / 'Mods'
    mods.mkdir()
    source = root / 'Loader.cs'
    source.write_text('using System.Reflection; [assembly:AssemblyInformationalVersion("0.7.3")] public class SyntheticLoader {}', encoding='utf-8')
    BUILD.compile_source(source, game / 'MelonLoader/net6/MelonLoader.dll', library=True)
    for index in range(42):
        name = f'Unique{index:02}'
        resource = ('ItemForge.items.json', manifest('ItemForge', 5)) if index == 0 else None
        artifact = compile_fixture(root / 'sources' / name, name, resource=resource)
        shutil.copyfile(artifact, mods / (name + '.dll'))
    for version, name in [('1.0.0.0', 'ShadowLow'), ('2.0.0.0', 'ShadowHigh')]:
        artifact = compile_fixture(root / 'sources' / name, 'SharedAssembly', version)
        shutil.copyfile(artifact, mods / (name + '.dll'))
    artifact = compile_fixture(root / 'sources/ContentOnly', 'ContentOnly', melon=False,
                               resource=('ItemForge.items.json', manifest('ItemForge', 19, count=21)))
    shutil.copyfile(artifact, mods / 'ContentOnly.dll')
    logs = ('[2026-09-24T10:00:00Z] [MelonLoader] MelonLoader v0.7.3\n'
            '[2026-09-24T10:00:01Z] [MelonLoader.Bootstrap] Failed to initialize a .NET domain\n'
            '[2026-09-24T10:00:02Z] [MelonLoader] 43 Mods loaded.\n'
            '[2026-09-24T10:00:03Z] [Unique00] MOD 兼容性检测：共享方法=25，潜在冲突=21，确认冲突=0，停用模块=0；loadedMods=43。\n')
    (game / 'MelonLoader/Latest.log').write_text(logs, encoding='utf-8')
    (root / 'fixture-origin.json').write_text(json.dumps(dict(
        origin='Self-authored C# and synthetic historical text; offline Roslyn; never execute fixture DLLs',
        physical=45, melon_info=44, predicted=43, historical_date='2026-09-24',
        historical_status='synthetic_not_real_game', source_hashes=hashes(game)), ensure_ascii=False, indent=2), encoding='utf-8')
    return game
