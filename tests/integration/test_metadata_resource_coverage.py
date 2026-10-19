"""Optional resource caps retain identity through real LPAC, SQLite and catalog APIs."""
import json
from pathlib import Path

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from tests.integration.test_content_catalog import BUILD, call, wait_scan, manifest
from tests.unit.test_installation_discovery import discover, hashes, wait
from tests.unit.test_installation_layout import make_installation


def test_resource_limits_keep_identity_and_mark_real_catalog_incomplete(tmp_path):
    game = make_installation(tmp_path / 'synthetic-game')
    (game / 'Mods').mkdir()
    marker = tmp_path / 'must-not-execute.txt'
    source = tmp_path / 'Synthetic.cs'
    source.write_text('''using System; using System.IO;
[assembly:MelonLoader.MelonInfo(typeof(Payload),"Bounded resource fixture","1.0","Synthetic")]
public class Payload {}
namespace MelonLoader { [AttributeUsage(AttributeTargets.Assembly)] public class MelonInfoAttribute:Attribute {
public MelonInfoAttribute(Type t,string n,string v,string a){File.WriteAllText(@"MARKER","BAD");} } }
'''.replace('MARKER', str(marker)), encoding='utf-8')
    huge = tmp_path / 'huge.bin'; huge.write_bytes(b'x' * (1024 * 1024 + 1))
    small = tmp_path / 'manifest.json'
    small.write_text(json.dumps(manifest('ItemForge', 19)), encoding='utf-8')
    BUILD.compile_source(source, game / 'Mods/LargeResource.dll', library=True,
                         resources=[(huge, 'huge.bin'), (small, 'ItemForge.items.json')])
    BUILD.compile_source(source, game / 'Mods/ManyResources.dll', library=True,
                         resources=[(small, f'part-{i}.json') for i in range(64)] +
                                   [(small, 'ItemForge.items.json')])
    before = hashes(game)
    context = DesktopContext(resolve_paths(str(tmp_path / 'isolated-data')))
    context.initialize(); context.choose_installation_folder = lambda kind: str(game)
    api = DesktopApi(context); context.api = api
    try:
        candidate = discover(api)
        selected = wait(api, call(api, 'installation.select', candidate_id=candidate['candidate_id']))
        identity = selected['data']['installation']['installation_id']
        scan = wait_scan(api, call(api, 'inventory.scan', installation_id=identity, force=False))
        assert scan['status'] == 'complete', scan
        page = call(api, 'inventory.list', installation_id=identity, limit=20)
        rows = [r for r in page['entries'] if r['relative_path'].startswith('Mods/')]
        assert len(rows) == 2
        for row in rows:
            assert row['status'] == 'managed', row
            detail = call(api, 'inventory.detail', installation_id=identity,
                          generation=scan['generation'], entry_id=row['entry_id'])
            analysis = detail['analysis']
            assert analysis['melon_info']['name'] == 'Bounded resource fixture'
            assert analysis['resource_coverage']['status'] == 'partial'
            assert 'RESOURCE_COVERAGE_PARTIAL' in [d['code'] for d in analysis['diagnostics']]
            if row['relative_path'].endswith('LargeResource.dll'):
                assert analysis['manifests'][0]['status'] == 'valid'
            else:
                assert analysis['resource_coverage']['omitted_count'] == 1
                assert analysis['manifests'] == []
        graph = call(api, 'inventory.graph', installation_id=identity, generation=scan['generation'])
        assert graph['coverage'] == 'partial', graph
        assert graph['relations']
        assert not any(r.get('resolution') == 'missing' for r in graph['relations'])
        assert any(r.get('resolution') == 'unknown' for r in graph['relations'])
        inspected = wait(api, call(api, 'inventory.inspect', installation_id=identity,
                                  files=[r['relative_path'] for r in rows]))
        assert inspected['state'] == 'succeeded'
        assert all(r['status'] == 'managed' for r in inspected['data']['files'])
        reports = list((context.paths.root / 'metadata/reports').glob('*.json'))
        assert len(reports) == 2
        for path in reports:
            process = json.loads(path.read_text(encoding='utf-8'))['process']
            assert process['appcontainer'] and process['capabilities'] == 0
            assert process['cleanup_remaining'] == 0 and process['profile_deleted']
        assert not list((context.paths.root / 'metadata/staging').iterdir())
        assert not marker.exists()
    finally:
        context.close()
    assert hashes(game) == before
