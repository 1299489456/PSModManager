"""Representative owned core-management data, using real import/action APIs."""
from pathlib import Path
from uuid import uuid4

from tests.integration.harness.transaction_recovery import TransactionFixture, tree
from tests.integration.test_content_catalog import compile_fixture


SAMPLE_COUNT = 60


def prepare_basic_fixture(root):
    app = TransactionFixture(root)
    try:
        return _prepare(app, Path(root))
    except BaseException:
        app.close()
        raise


def _prepare(app, root):
    releases = []
    for index in range(SAMPLE_COUNT):
        name = f'基础样本 中文模组 {index:03d}'
        if index == 1:
            name += ' · ' + '用于检查长名称换行' * 6
        payload = (f'owned resource {index:03d}\n'.encode() * 64)
        releases.append(app.publish({f'Mods/sample-{index:03d}.png': payload}, name))
    for release in releases[:4]:
        result = app.call('mod.action', dict(installation_id=app.installation,
            release_id=release['release_id'], manifest_hash=release['manifest_hash'],
            action='install', idempotency_key=str(uuid4())))
        assert result.get('completed') and result['state']=='enabled', result
    source = Path(root)/'中文 Mod 空格'
    dll = compile_fixture(Path(root)/'compiler', 'BasicManagementMod')
    files = {'Mods/中文样本.dll': dll.read_bytes(),
             'UserLibs/中文资源/图片 空格.png': b'owned-basic-resource\n' * (1024*1024//21),
             'UserLibs/中文资源/说明.bundle': '自造配置资源，不执行任何脚本。'.encode('utf-8')}
    for relative,payload in files.items():
        path=source/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(payload)
    metadata=dict(sample_count=SAMPLE_COUNT,installation_id=app.installation,
        releases=releases,seeded_installed=releases[:4],import_source=str(source),
        imported_name='普通导入 中文长名称 · '+ '可用性演示' * 7,
        game=str(app.game),data=str(app.data),baseline=tree(app.game),source_baseline=tree(source))
    return app,metadata,files
