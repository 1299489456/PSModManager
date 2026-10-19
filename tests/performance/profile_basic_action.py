"""Optional diagnostic for a measured slow action, through actual DesktopApi."""
import cProfile
import json
from pathlib import Path
import pstats
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.integration.harness.transaction_recovery import TransactionFixture


def main(root):
    root.mkdir(parents=True)
    app = TransactionFixture(root)
    try:
        for index in range(4):
            release = app.publish({f'Mods/seed-{index}.png':b'owned-resource'}, f'Seed {index}')
            result = app.call('mod.action', dict(installation_id=app.installation,
                release_id=release['release_id'], manifest_hash=release['manifest_hash'],
                action='install', idempotency_key=str(uuid4())))
            assert result.get('completed'), result
        release = app.publish({'UserLibs/中文资源/图片 空格.png':b'x'*(1024*1024)}, 'Profile owned action')
        original = app.context.mods.action
        profiler = cProfile.Profile()
        def profile_action(*args, **kwargs):
            return profiler.runcall(original, *args, **kwargs)
        app.context.mods.action = profile_action
        result = app.call('mod.action', dict(installation_id=app.installation,
            release_id=release['release_id'], manifest_hash=release['manifest_hash'],
            action='install', idempotency_key=str(uuid4())))
        assert result.get('completed') and result['state']=='enabled', result
        profiler.dump_stats(str(root/'action.prof'))
        with (root/'profile.txt').open('w', encoding='utf-8') as stream:
            pstats.Stats(profiler, stream=stream).sort_stats('cumulative').print_stats(40)
        (root/'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    finally:
        app.close()


if __name__ == '__main__':
    main(Path(sys.argv[1]))
