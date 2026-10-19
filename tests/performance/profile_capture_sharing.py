"""Count real work and elapsed time on an anonymized 45-Mod/1912-file layout."""
import argparse
import cProfile
import io
import json
from pathlib import Path
import sys
import time
import pstats
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.storage.package_repository import PackageRepository
from tests.integration.harness.transaction_recovery import TransactionFixture

parser = argparse.ArgumentParser()
parser.add_argument('--out', required=True, type=Path)
parser.add_argument('--label', required=True)
parser.add_argument('--profile', action='store_true')
args = parser.parse_args()
out = args.out.resolve(); out.mkdir(parents=True, exist_ok=True)
owned = out/(args.label+'-owned'); owned.mkdir()
fixture = TransactionFixture(owned)


def write(name, size):
    path = fixture.game/name; path.parent.mkdir(parents=True, exist_ok=True)
    chunk = (name.encode()*8192)[:65536]
    with path.open('wb') as stream:
        while size:
            block = chunk[:min(size, len(chunk))]; stream.write(block); size -= len(block)


try:
    for index in range(45):
        name = 'MoreStorageColors' if index == 0 else 'BrewingExpansion' if index == 1 else 'SyntheticMod'+str(index)
        size = 40458240 if index == 43 else 42641408 if index == 44 else 5997266 if index == 42 else 4096
        write('Mods/'+name+'.dll', size)
    for index in range(1862):
        leaf = 'secret_admirer.png' if index == 0 else 'asset-'+str(index)+'.png'
        write('Mods/MoreStorageColors/Reference/'+leaf, 800)
    for index in range(5):
        write('Mods/BrewingExpansion/notes-'+str(index)+'.txt', 256)
    counters = {}
    verify, capture = PackageRepository.verify, PackageRepository.capture_ordinary
    def measured_verify(repository, digest, size=None, **kwargs):
        counters['verify_calls'] += 1
        counters['verified_bytes'] += size or repository.path(digest).stat().st_size
        return verify(repository, digest, size, **kwargs)
    def measured_capture(repository, source, check):
        counters['capture_calls'] += 1; counters['captured_bytes'] += source.stat().st_size
        return capture(repository, source, check)
    results = []
    with patch.object(PackageRepository, 'verify', measured_verify), patch.object(PackageRepository, 'capture_ordinary', measured_capture):
        for label in ('first_capture', 'repeat_capture'):
            counters.update(verify_calls=0, verified_bytes=0, capture_calls=0, captured_bytes=0)
            start = time.perf_counter()
            profiler = cProfile.Profile() if args.profile and label == 'first_capture' else None
            if profiler: profiler.enable()
            profile = fixture.context.profile_sharing.capture(fixture.installation, label)
            if profiler:
                profiler.disable()
                text = io.StringIO(); pstats.Stats(profiler, stream=text).strip_dirs().sort_stats('cumtime').print_stats(32)
                (out/(args.label+'-profile.txt')).write_text(text.getvalue(), encoding='utf8')
            results.append(dict(operation=label, elapsed_seconds=time.perf_counter()-start, **counters,
                                selections=len(profile['selections'])))
        counters.update(verify_calls=0, verified_bytes=0, capture_calls=0, captured_bytes=0)
        start = time.perf_counter()
        try:
            result = fixture.context.profile_sharing.export_bundle(profile['profile_id'], str(out/(args.label+'-bundle.zip')))
            export = dict(saved=result['saved'], file_count=result['file_count'])
        except ValueError as error:
            export = dict(saved=False, error_code=getattr(error, 'code', str(error)))
        results.append(dict(operation='export_bundle', elapsed_seconds=time.perf_counter()-start, **counters, **export))
    report = dict(label=args.label, synthetic=True, target_files=1912, target_bytes=90759826,
        actual_bytes=sum(p.stat().st_size for p in (fixture.game/'Mods').rglob('*') if p.is_file())-len(b'{"unknown":"keep"}'),
        results=results)
    (out/(args.label+'.json')).write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report, indent=2))
finally:
    fixture.close()
