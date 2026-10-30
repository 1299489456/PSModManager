"""Read-only physical observations, content-keyed parsing and atomic generations."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import time
import tomllib

from backend.games.probably_stolen.layout import safe_path, identity, signature, LOADERS
from backend.games.probably_stolen.fingerprint import Budget, DiscoveryCancelled, read_file, file_version
from backend.scanning import legacy_loader_rules as v073
from backend.runtime.jobs import RuntimePolicyError, _Cancelled
from backend.storage import Store
from backend.storage.inventory_repository import InventoryRepository
from .worker_client import MetadataWorker, MAX_INPUT

ROOTS = ('Mods','Plugins','UserLibs')
MAX_FILES = 5000
MAX_DIRS = 500
MAX_DEPTH = 16
MAX_SECONDS = 120


def parser_version():
    base=Path(__file__).resolve().parents[2]
    digest=hashlib.sha256(b'psmm-inventory-parser-v1')
    for name in ('tools/PSModManager.Metadata/bin/PSModManager.Metadata.dll',
                 'tools/PSModManager.Metadata/bin/PSModManager.Metadata.runtimeconfig.json',
                 'backend/scanning/worker_client.py','backend/scanning/worker_sandbox.py',
                 'backend/scanning/worker_host.py'):
        path=base/name
        digest.update(path.read_bytes() if path.is_file() else ('missing:'+name).encode())
    return digest.hexdigest()


def _error(errors,path,code):
    if len(errors)<200:errors.append({'path':path,'code':code})


def _walk(root,budget,errors):
    """Enumerate even excluded folders; exclusions are prediction, not absence."""
    files={}; directories=[]; queue=[]
    # Resolve casing from physical names; only three explicitly allowed roots.
    with os.scandir(root) as iterator:
        names={entry.name.casefold():entry.name for entry in iterator if entry.name.casefold() in {r.lower() for r in ROOTS}}
    for name in ROOTS:
        actual=names.get(name.lower())
        if actual is None:directories.append({'path':name,'status':'absent'})
        else:queue.append((actual,0))
    while queue:
        budget.check()
        relative,depth=queue.pop(0)
        if len(directories)>=MAX_DIRS or depth>MAX_DEPTH:
            _error(errors,relative,'DIRECTORY_LIMIT');break
        try:
            directory=safe_path(root,relative,directory=True)
            with os.scandir(directory) as iterator:
                entries=[]
                for entry in iterator:
                    if len(entries)+len(files)+len(queue)>MAX_FILES+MAX_DIRS:
                        raise ValueError('ENTRY_LIMIT')
                    entries.append(entry)
            directories.append({'path':relative,'status':'complete'})
            for entry in sorted(entries,key=lambda e:e.name.casefold()):
                budget.check(); child=relative+'/'+entry.name
                if len(child)>512:
                    _error(errors,child[:512],'PATH_LIMIT');continue
                info=entry.stat(follow_symlinks=False)
                if entry.is_symlink() or info.st_file_attributes & 0x400:
                    _error(errors,child,'REPARSE_POINT');continue
                if stat.S_ISDIR(info.st_mode):queue.append((child,depth+1))
                elif stat.S_ISREG(info.st_mode):
                    if len(files)>=MAX_FILES:raise ValueError('FILE_LIMIT')
                    # Windows DirEntry.stat may return zero file identity from
                    # enumeration data. Compare fresh handle-backed stat facts.
                    files[child]=signature(safe_path(root,child).stat())
                else:_error(errors,child,'NON_REGULAR_FILE')
        except (OSError,ValueError) as exc:
            directories.append({'path':relative,'status':'partial'})
            _error(errors,relative,'DIRECTORY_UNREADABLE' if isinstance(exc,OSError) else str(exc))
    return files,directories


def _policy(root,budget):
    tracked={}; version=None; options=None; disabled=False
    for name in LOADERS:
        try:
            digest,_,before=read_file(root,name,budget,limit=32*1024*1024)
            version=file_version(safe_path(root,name)); tracked[name]=digest
            if signature(safe_path(root,name).stat())!=before:raise ValueError('POLICY_DRIFT')
            break
        except FileNotFoundError:continue
    name='UserData/Loader.cfg'
    try:
        digest,data,_=read_file(root,name,budget,limit=1024*1024,capture=True); tracked[name]=digest
        data=tomllib.loads(data.decode('utf-8-sig')).get('loader',{})
        if not isinstance(data,dict):raise ValueError('LOADER_OPTIONS_INVALID')
        a=data.get('disable_subfolder_load',False); b=data.get('disable_subfolder_manifest',False)
        disabled=data.get('disable',False)
        if all(type(v) is bool for v in (a,b,disabled)):
            options={'DisableSubFolderLoad':a,'DisableSubFolderManifest':b}
        else:raise ValueError('LOADER_OPTIONS_INVALID')
    except FileNotFoundError:
        tracked[name]=None
        options={'DisableSubFolderLoad':False,'DisableSubFolderManifest':False}
    return dict(loader_version=version,options=options,disabled=disabled,rule_version=v073.RULE_VERSION,
                option_scope='current_config_stock_prediction_not_runtime_command_line'),tracked


def _small_summary(value):
    """UI summary is bounded; full coverage and raw metadata remain in SQLite."""
    if value is None:return None
    result={key:value[key] for key in ('installation_id','generation','status','total','latest_complete','latest_attempt','latest_requested') if key in value}
    coverage=value.get('coverage',{})
    result['coverage']={key:item for key,item in coverage.items() if key!='directories'}
    result['coverage']['directory_count']=len(coverage.get('directories',[]))
    result['errors']=value.get('errors',[])[:10]
    result['error_count']=len(value.get('errors',[]))
    return result


class InventoryService:
    def __init__(self,paths):self.paths=paths

    def _installation(self,installation_id):
        self.paths.check_owned_paths();store=Store(self.paths.database)
        try:return store.get('Installation',installation_id)
        except KeyError:raise RuntimePolicyError('INSTALLATION_NOT_FOUND') from None
        finally:store.close()

    def scan(self,jobs,request_id,installation_id,force=False):
        installation=self._installation(installation_id)
        store=Store(self.paths.database)
        try:generation=InventoryRepository(store).begin(installation_id,{'parser_version':parser_version(),'force':force})
        finally:store.close()
        def abort(reason):
            store=Store(self.paths.database)
            try:
                repo=InventoryRepository(store)
                if repo.summary(installation_id,generation)['status']=='running':
                    repo.finish(installation_id,generation,[],'partial' if reason=='cancelled' else 'failed',
                                {'cancelled':reason=='cancelled'},[{'path':'','code':reason.upper()}])
            finally:store.close()
        def worker(task):
            return self._scan(installation,generation,force,task)
        try:
            result=jobs.start(request_id,worker,on_abort=abort)
            result['scan_generation']=generation
            return result
        except Exception:
            abort('failed');raise

    def _scan(self,installation,generation,force,task):
        root=Path(installation.canonical_root);installation_id=str(installation.installation_id)
        store=Store(self.paths.database);repo=InventoryRepository(store)
        budget=Budget(lambda:task.cancelled);budget.deadline=time.monotonic()+MAX_SECONDS
        entries=[];errors=[];directories=[];hits=0;policy={};status='failed'
        try:
            if identity(root)!=installation.file_identity:raise ValueError('INSTALLATION_CHANGED')
            if root==self.paths.root or root in self.paths.root.parents or self.paths.root in root.parents:
                raise ValueError('DATA_ROOT_CONFLICT')
            try:policy,tracked=_policy(root,budget)
            except (OSError,ValueError):
                policy=dict(loader_version=None,options=None,disabled=False,rule_version=v073.RULE_VERSION)
                tracked={};_error(errors,'','LOADER_POLICY_UNREADABLE')
            files,directories=_walk(root,budget,errors)
            manifests={}; parser=parser_version();worker=MetadataWorker(self.paths.root)
            for relative in files:
                if Path(relative).name.casefold()=='manifest.json':
                    try:
                        _,data,_=read_file(root,relative,budget,limit=1024*1024,capture=True)
                        manifests[str(Path(relative).parent).replace('\\','/')]=v073.manifest_parse(data)
                    except (OSError,ValueError):
                        manifests[str(Path(relative).parent).replace('\\','/')]={'present':None,'status':'unknown'}
                        _error(errors,relative,'MANIFEST_UNREADABLE')
            for index,relative in enumerate(sorted(files,key=str.casefold)):
                budget.check()
                row=dict(relative_path=relative,file_identity=None,size=None,mtime_ns=None,sha256=None,
                         status='unknown',metadata=None,prediction={},cache_hit=False,managed=None,
                         has_melon_info=None,assembly_name=None,assembly_version=None)
                try:
                    digest,_,info=read_file(root,relative,budget,limit=MAX_INPUT)
                    row.update(file_identity=f'{info[0]:x}:{info[1]:x}',size=info[2],mtime_ns=info[3],sha256=digest)
                    if relative.lower().endswith('.dll'):
                        cached=None if force else repo.cache_get(digest,parser)
                        if cached is None:
                            parsed=worker.inspect(root,relative,cancelled=lambda:task.cancelled)
                            if parsed['sha256']!=digest:raise ValueError('FILE_DRIFT')
                            cached={key:parsed[key] for key in ('status','metadata','error')}
                            if parsed['status'] in ('managed','native'):repo.cache_put(digest,parser,cached)
                        else:row['cache_hit']=True;hits+=1
                        row.update(status=cached['status'],metadata=cached['metadata'])
                        if cached['status'] not in ('managed','native'):
                            _error(errors,relative,cached.get('error') or 'METADATA_INCOMPLETE')
                        else:
                            metadata=cached['metadata'] or {};row['managed']=cached['status']=='managed'
                            row.update(assembly_name=metadata.get('assembly_name'),assembly_version=metadata.get('assembly_version'))
                            row['has_melon_info']=any(a.get('type')=='MelonLoader.MelonInfoAttribute' and a.get('decoded') for a in metadata.get('custom_attributes',[]))
                    if signature(safe_path(root,relative).stat())!=files[relative]:raise ValueError('FILE_DRIFT')
                except (OSError,ValueError) as exc:
                    row.update(status='unknown',metadata=None,managed=None,has_melon_info=None,assembly_name=None,assembly_version=None)
                    _error(errors,relative,'FILE_UNREADABLE' if isinstance(exc,OSError) else str(exc))
                entries.append(row)
                task.progress(index+1,len(files),'inventory','INVENTORY_FILE_SCANNED')
            for row in entries:
                row['prediction']=v073.predict(row['relative_path'],loader_version=policy['loader_version'],options=policy['options'],manifests=manifests)
                if policy['disabled'] and v073.supports_version(policy['loader_version']):
                    row['prediction'].update(status='excluded',reasons=['loader_disabled_in_config'])
            entries=v073.choose_candidates(entries,policy['loader_version'])
            # Re-enumeration plus fresh hashes catches same-length/restored-mtime
            # edits as well as renames/replacements while metadata was parsed.
            final,_=_walk(root,budget,errors)
            if files!=final:_error(errors,'','DIRECTORY_DRIFT')
            for row in entries:
                if row['sha256'] is None:continue
                try:
                    if read_file(root,row['relative_path'],budget,limit=MAX_INPUT)[0]!=row['sha256']:
                        _error(errors,row['relative_path'],'FILE_DRIFT')
                        row['prediction']={'status':'unknown','stage':None,'reasons':['file_drift']}
                        row['status']='unknown';row['metadata']=None
                except (OSError,ValueError):_error(errors,row['relative_path'],'FINAL_READ_FAILED')
            for name,digest in tracked.items():
                try:actual=read_file(root,name,budget,limit=32*1024*1024)[0]
                except FileNotFoundError:actual=None
                if digest!=actual:_error(errors,name,'POLICY_DRIFT')
            if identity(root)!=installation.file_identity:_error(errors,'','INSTALLATION_CHANGED')
            if errors:
                # A missing/unstable sibling may shadow a candidate. Never turn
                # incomplete physical coverage into a positive loading claim.
                for row in entries:
                    if row['prediction'].get('status')=='candidate':
                        row['prediction'].update(status='unknown',reasons=['incomplete_scan_coverage'])
            status='partial' if errors else 'complete'
        except (DiscoveryCancelled, _Cancelled):
            _error(errors,'','CANCELLED');status='partial'
        except (OSError,ValueError) as exc:
            _error(errors,'','SCAN_UNREADABLE' if isinstance(exc,OSError) else str(exc))
            status='partial' if entries else 'failed'
        finally:
            coverage=dict(directories=directories,policy=policy,hash_bytes=budget.used,cache_hits=hits,
                          cancelled=task.cancelled,physical_files=len(entries),parser_version=parser_version())
            try:
                if status=='complete':
                    result=task.commit(lambda:repo.finish(installation_id,generation,entries,status,coverage,errors))
                else:result=repo.finish(installation_id,generation,entries,status,coverage,errors)
            finally:store.close()
        task.check_cancelled()
        return _small_summary(result)

    def list(self,installation_id,generation=None,limit=50,offset=0):
        self._installation(installation_id);store=Store(self.paths.database)
        try:
            page=InventoryRepository(store).list(installation_id,generation,limit,offset)
            output=_small_summary(page);output.update(entries=[],offset=offset,total=page['total'],last_attempt=_small_summary(page['last_attempt']))
            for row in page['entries']:
                brief={key:row.get(key) for key in ('entry_id','relative_path','presence','file_identity','size','mtime_ns','sha256','status','prediction','cache_hit','tags')}
                for key in ('assembly_name','assembly_version'):
                    value=row.get(key);brief[key]=value[:256] if isinstance(value,str) else value
                    if isinstance(value,str) and len(value)>256:brief[key+'_truncated']=True
                output['entries'].append(brief)
                if len(json.dumps(output,ensure_ascii=False).encode())>48*1024:
                    output['entries'].pop();break
            output['next_offset']=offset+len(output['entries']) if offset+len(output['entries'])<page['total'] else None
            return output
        except KeyError:raise RuntimePolicyError('INVENTORY_SCAN_NOT_FOUND') from None
        finally:store.close()
