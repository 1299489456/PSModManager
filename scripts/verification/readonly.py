"""Explicit read-only resource contracts and historical comparisons; no discovery."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import time
import zipfile

TREES = ('Mods', 'Plugins', 'UserLibs')
REQUIRED_FILES = ('Probably Stolen.exe', 'GameAssembly.dll',
                  'Probably Stolen_Data/il2cpp_data/Metadata/global-metadata.dat',
                  'MelonLoader/net6/MelonLoader.dll', 'MelonLoader/MelonLoader.dll',
                  'UserData/Loader.cfg', 'MelonLoader/Latest.log')
EVIDENCE_FIELDS = ('relative_path', 'sha256', 'size', 'metadata', 'prediction', 'diagnostics')
HISTORICAL_DATE = '2026-09-24'
HISTORICAL_LOG_HASH = '1eb5a7730f09c33342d18521dd7c50b09ca80c56fe3cd50e4a36cb7d3ec53f1a'


def relative(value):
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError('READONLY_INVALID_RELATIVE_PATH')
    parts = value.replace('\\', '/').split('/')
    if any(not p or p in ('.','..') or p.endswith((' ','.')) or
           any(ord(c) < 32 or c in ':*?"<>|' for c in p) for p in parts):
        raise ValueError('READONLY_INVALID_RELATIVE_PATH')
    return '/'.join(parts)


def validate_authorization(value, *, now=None):
    """Pure validation. A configuration documents, but cannot create, user consent."""
    required = {'task_id','profile','resources','operations','network','upload','budget','valid_until','excluded_data'}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError('READONLY_AUTHORIZATION_FIELDS')
    if value['task_id'] != 'PS-01-07' or value['profile'] != 'real-readonly':
        raise ValueError('READONLY_AUTHORIZATION_SCOPE')
    if value['operations'] != ['read'] or value['network'] is not False or value['upload'] is not False:
        raise ValueError('READONLY_OPERATION_NOT_ALLOWED')
    if not isinstance(value['excluded_data'], list) or not {'saves','credentials','personal_data'} <= set(value['excluded_data']):
        raise ValueError('READONLY_EXCLUSIONS_REQUIRED')
    expiry = datetime.fromisoformat(value['valid_until'].replace('Z', '+00:00'))
    if expiry.tzinfo is None or expiry <= (now or datetime.now(timezone.utc)):
        raise ValueError('READONLY_AUTHORIZATION_EXPIRED')
    resource = value['resources']
    fields = {'installation_root','trees','files','logs','evidence_fields'}
    if not isinstance(resource, dict) or not fields <= set(resource) or set(resource) - fields - {'steam_manifests','log_enumeration'}:
        raise ValueError('READONLY_RESOURCE_FIELDS')
    root = resource['installation_root']
    if not isinstance(root, str) or not PureWindowsPath(root).is_absolute() or root.startswith(('\\\\','//')):
        raise ValueError('READONLY_LOCAL_ROOT_REQUIRED')
    relative('/'.join(PureWindowsPath(root).parts[1:]))
    winroot = PureWindowsPath(root)
    manifests = resource.get('steam_manifests', [])
    expected_manifests = []
    if winroot.parent.name.casefold() == 'common' and winroot.parent.parent.name.casefold() == 'steamapps':
        expected_manifests = [str(winroot.parent.parent / ('appmanifest_' + app + '.acf'))
                              for app in ('4349200', '4348910')]
    if (not isinstance(manifests,list) or any(not isinstance(p,str) for p in manifests) or
            manifests and [str(PureWindowsPath(p)).casefold() for p in manifests] != [p.casefold() for p in expected_manifests]):
        raise ValueError('READONLY_STEAM_MANIFEST_SCOPE_REQUIRED')
    if resource.get('log_enumeration',[]) not in ([],['MelonLoader/Logs']):
        raise ValueError('READONLY_LOG_ENUMERATION_SCOPE')
    if resource['trees'] != list(TREES) or resource['files'] != list(REQUIRED_FILES) or resource['evidence_fields'] != list(EVIDENCE_FIELDS):
        raise ValueError('READONLY_INCOMPLETE_RESOURCE_SCOPE')
    if not isinstance(resource['logs'], list) or len(resource['logs']) > 64:
        raise ValueError('READONLY_LOG_SCOPE_LIMIT')
    for name in resource['logs']:
        path = relative(name)
        if not path.lower().endswith('.log') or not (path.startswith('MelonLoader/Logs/') or path.startswith('UserData/')):
            raise ValueError('READONLY_LOG_ROOT_REFUSED')
    budget = value['budget']
    if not isinstance(budget, dict) or set(budget) != {'max_files','max_bytes','max_seconds'}:
        raise ValueError('READONLY_BUDGET_REQUIRED')
    for key, upper in [('max_files',5000),('max_bytes',1024**3),('max_seconds',120)]:
        if type(budget[key]) is not int or not 1 <= budget[key] <= upper:
            raise ValueError('READONLY_BUDGET_INVALID')
    return value


class ReadonlyAudit:
    """Harness defense in depth: product outputs remain real; forbidden IO raises."""
    def __init__(self, authorization):
        validate_authorization(authorization)
        resource=authorization['resources']
        self.root=Path(resource['installation_root'])
        self.resource=resource
        self.denied=[];self.reads=set();self.enumerations=set()
        self.prefix=os.path.normcase(os.path.normpath(str(self.root)))+os.sep
        self.allowed={name.casefold() for name in resource['files']+resource['logs']}
        self.manifests={os.path.normcase(os.path.normpath(p)) for p in resource.get('steam_manifests',[])}
        self.external_candidates={os.path.normcase(os.path.normpath(str(self.root.parent.parent/f'appmanifest_{app}.acf')))
                                  for app in ('4349200','4348910')}

    def _relative(self,path):
        if not isinstance(path,(str,bytes,os.PathLike)):return None
        name=os.path.normcase(os.path.abspath(os.fsdecode(path)))
        if name.startswith(self.prefix):return name[len(self.prefix):].replace('\\','/')
        if name==self.prefix[:-1]:return ''
        return None

    def __call__(self,event,args):
        if event in ('socket.connect','socket.bind','socket.getaddrinfo','socket.gethostbyname','os.system','os.startfile'):
            raise PermissionError('READONLY_EXTERNAL_OPERATION_REFUSED')
        if event=='subprocess.Popen' and self._relative(args[0]) is not None:
            raise PermissionError('READONLY_GAME_EXECUTION_REFUSED')
        if event not in ('open','os.scandir','os.listdir','os.remove','os.rename','os.mkdir','os.rmdir','os.chmod','os.link','os.symlink'):
            return
        path=args[0]
        if not isinstance(path,(str,bytes,os.PathLike)):return
        if event in ('os.rename','os.link','os.symlink') and len(args)>1 and self._relative(args[1]) is not None:
            self.denied.append(dict(event=event,relative_path=self._relative(args[1])))
            raise PermissionError('READONLY_RESOURCE_OPERATION_REFUSED')
        normalized=os.path.normcase(os.path.abspath(os.fsdecode(path)))
        rel=self._relative(path)
        if rel is None:
            if normalized in self.external_candidates:
                flags=args[2] if event=='open' and len(args)>2 and isinstance(args[2],int) else 0
                if event!='open' or normalized not in self.manifests or flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):
                    self.denied.append('external_manifest');raise PermissionError('READONLY_EXTERNAL_MANIFEST_REFUSED')
                self.reads.add('external_manifest/'+Path(path).name)
            return
        if event=='open':
            flags=args[2] if len(args)>2 and isinstance(args[2],int) else 0
            allowed=not flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)
            allowed=allowed and (rel.casefold() in self.allowed or rel.split('/')[0].casefold() in {'mods','plugins','userlibs'})
            if allowed:
                _check_path(Path(path));self.reads.add(rel);return
        elif event in ('os.scandir','os.listdir'):
            if rel=='' or rel.split('/')[0].casefold() in {'mods','plugins','userlibs'} or rel.casefold()=='melonloader/logs' and self.resource.get('log_enumeration')==['MelonLoader/Logs']:
                _check_path(Path(path));self.enumerations.add(rel);return
        self.denied.append(dict(event=event,relative_path=rel))
        raise PermissionError('READONLY_RESOURCE_OPERATION_REFUSED')

    def check_watch_scope(self):
        if self.resource.get('log_enumeration')!=['MelonLoader/Logs']:
            raise ValueError('READONLY_LOG_ENUMERATION_AUTHORIZATION_REQUIRED')
        directory=_check_path(self.root/'MelonLoader/Logs')
        if not directory.exists():return
        with os.scandir(directory) as entries:
            for index,entry in enumerate(entries):
                if index>=256:raise ValueError('READONLY_ARCHIVE_DIRECTORY_LIMIT')
                if entry.name.lower().endswith('.log'):
                    name='MelonLoader/Logs/'+entry.name
                    if name.casefold() not in self.allowed:
                        raise ValueError('READONLY_ARCHIVE_FILE_NOT_AUTHORIZED')
                    _check_path(Path(entry.path))


def load_authorization(environ=None):
    environ = os.environ if environ is None else environ
    if environ.get('PSMM_VERIFY_PROFILE') != 'real-readonly':
        raise ValueError('READONLY_PROFILE_REQUIRED')
    name = environ.get('PSMM_READONLY_AUTHORIZATION_FILE')
    if not name:
        raise ValueError('READONLY_AUTHORIZATION_MISSING')
    path = Path(name)
    if not path.is_absolute():
        raise ValueError('READONLY_AUTHORIZATION_PATH')
    _check_path(path)
    if path.stat().st_size > 65536:
        raise ValueError('READONLY_AUTHORIZATION_SIZE')
    return validate_authorization(json.loads(path.read_text(encoding='utf-8')))


def _check_path(path):
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0) & 0x400:
            raise ValueError('READONLY_REPARSE_REFUSED')
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ValueError('READONLY_HARDLINK_REFUSED')
    return path


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def snapshot_scope(root, *, trees=TREES, files=REQUIRED_FILES, max_files=5000, max_bytes=1024**3, max_seconds=120):
    """Explicit roots only. Reads all registered files without following links."""
    root = Path(root)
    if not root.is_absolute():
        raise ValueError('READONLY_LOCAL_ROOT_REQUIRED')
    _check_path(root)
    root_info = _signature(root.stat())[:2]
    deadline = time.monotonic() + max_seconds
    queue = [relative(t) for t in trees]
    selected = set(relative(f) for f in files)
    absent, directories = [], []
    while queue:
        if time.monotonic() > deadline or len(directories) > 500:
            raise ValueError('READONLY_DIRECTORY_BUDGET')
        name = queue.pop()
        directory = _check_path(root / name)
        if not directory.exists():
            absent.append(name); continue
        if not directory.is_dir():
            raise ValueError('READONLY_EXPECTED_DIRECTORY')
        directories.append(name)
        with os.scandir(directory) as entries:
            for entry in entries:
                child = relative(name + '/' + entry.name)
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0) & 0x400:
                    raise ValueError('READONLY_REPARSE_REFUSED')
                if stat.S_ISDIR(info.st_mode): queue.append(child)
                elif stat.S_ISREG(info.st_mode): selected.add(child)
                else: raise ValueError('READONLY_NON_REGULAR_FILE')
                if len(selected) + len(queue) > max_files + 500:
                    raise ValueError('READONLY_FILE_BUDGET')
    rows, used = [], 0
    for name in sorted(selected):
        path = _check_path(root / name)
        if not path.exists():
            absent.append(name); continue
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError('READONLY_NON_REGULAR_FILE')
        if len(rows) >= max_files or used + before.st_size > max_bytes:
            raise ValueError('READONLY_READ_BUDGET')
        digest = hashlib.sha256()
        with path.open('rb', buffering=0) as stream:
            if _signature(os.fstat(stream.fileno())) != _signature(before):
                raise ValueError('READONLY_FILE_DRIFT')
            while chunk := stream.read(256 * 1024):
                if time.monotonic() > deadline:
                    raise ValueError('READONLY_TIME_BUDGET')
                used += len(chunk)
                if used > max_bytes:
                    raise ValueError('READONLY_READ_BUDGET')
                digest.update(chunk)
            if _signature(os.fstat(stream.fileno())) != _signature(before):
                raise ValueError('READONLY_FILE_DRIFT')
        if _signature(_check_path(path).stat()) != _signature(before):
            raise ValueError('READONLY_FILE_DRIFT')
        rows.append(dict(relative_path=name, size=before.st_size, sha256=digest.hexdigest()))
    if _signature(root.stat())[:2] != root_info:
        raise ValueError('READONLY_ROOT_DRIFT')
    return dict(files=rows, absent=sorted(absent), directories=sorted(directories), bytes=used)


def compare_sets(expected, actual):
    def index(rows):
        result = {}
        for row in rows:
            key = relative(row['relative_path']).casefold()
            if key in result or not re.fullmatch('[a-fA-F0-9]{64}',row['sha256']):
                raise ValueError('READONLY_INVALID_BASELINE')
            result[key] = row
        return result
    old, new = index(expected), index(actual)
    return dict(added=sorted(new.keys()-old.keys()), removed=sorted(old.keys()-new.keys()),
                changed=[dict(relative_path=key, expected=old[key]['sha256'], actual=new[key]['sha256'])
                         for key in sorted(old.keys() & new.keys()) if old[key]['sha256'].lower()!=new[key]['sha256'].lower()])


def snapshot_authorized(value):
    validate_authorization(value)
    resource, budget = value['resources'], value['budget']
    result = snapshot_scope(resource['installation_root'], trees=resource['trees'],
                            files=resource['files'] + resource['logs'], **budget)
    manifests = resource.get('steam_manifests', [])
    if manifests:
        first = Path(manifests[0])
        result['steam_manifests'] = snapshot_scope(first.parent, trees=(),
                                                   files=[Path(p).name for p in manifests], **budget)
    return result


def parse_loader_hashes(text):
    """Raw historical log excerpt parser. Does not guess missing loaded records."""
    pending = None; rows = []
    for line in text.splitlines():
        match = re.search(r"Melon Assembly loaded: '(.+?)'", line)
        if match:
            pending = match[1].replace('\\','/')
            if pending.startswith('./'): pending = pending[2:]
            pending = relative(pending)
            if pending.split('/')[0] not in TREES:
                raise ValueError('READONLY_LOG_PATH_REFUSED')
        match = re.search(r"SHA256 Hash: '([a-fA-F0-9]{64})'", line)
        if match and pending:
            rows.append(dict(relative_path=pending,sha256=match[1].lower())); pending=None
    compare_sets(rows, rows)
    return rows


def load_historical_baseline(project):
    """Read fixed project archives only; never dereference paths in their text."""
    project = Path(project)
    names = ['ProbablyStolen_ModManager_Analysis.zip','ProbablyStolen_RimCrow_Analysis.zip']
    docs, sources = [], {}
    for archive_name, member in zip(names, ['ProbablyStolen_Mod_Inventory.json','ProbablyStolen_Analysis/mod_inventory.json']):
        path = _check_path(project / 'docs' / archive_name)
        sources[archive_name] = hashlib.sha256(path.read_bytes()).hexdigest()
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo(member)
            if info.file_size > 1024**2:
                raise ValueError('READONLY_BASELINE_SIZE')
            docs.append(json.loads(archive.read(member)))
            if archive_name == names[0]:
                raw_text = archive.read('ProbablyStolen_Audit_Evidence.md').decode('utf-8')
    first, second = docs
    if first.get('audit_date') != HISTORICAL_DATE:
        raise ValueError('READONLY_BASELINE_DATE_CHANGED')
    physical = [dict(relative_path='Mods/'+relative(r['file']),sha256=r['sha256']) for r in first['artifacts']]
    secondary = [dict(relative_path='Mods/'+relative(r['file']),sha256=r['sha256']) for r in second]
    if any(compare_sets(physical,secondary).values()) or len(physical)!=45:
        raise ValueError('READONLY_BASELINE_INCONSISTENT')
    reported = [dict(relative_path='Mods/'+relative(r['file']),sha256=r['sha256']) for r in second
                if r.get('hash_mentioned_in_bundled_loader_log') is True]
    raw = parse_loader_hashes(raw_text)
    gaps = []
    if len(reported)!=43: gaps.append('historical_reported_43_set_incomplete')
    if len(raw)!=43: gaps.append('historical_raw_43_loader_hashes_missing')
    return dict(historical_date=HISTORICAL_DATE, physical=physical, loaded_reported=reported,
                loaded_raw=raw, historical_log_sha256=HISTORICAL_LOG_HASH, source_hashes=sources,
                gaps=gaps, real_environment_verified=False)
