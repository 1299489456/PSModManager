"""One installation watcher, shared task events and immutable evidence queries."""
import json
from pathlib import Path, PureWindowsPath
import sqlite3
import threading
import time
from types import SimpleNamespace

from backend.app.identity import DATABASE_APPLICATION_ID
from backend.games.probably_stolen.layout import identity, safe_path
from backend.runtime.jobs import RuntimePolicyError
from backend.storage import Store
from .evidence import EvidenceRepository
from .sessions import SessionTracker
from .tailer import Tailer

POLL_SECONDS = .1
MAX_ARCHIVES = 16


def user_log_path(value):
    if not isinstance(value,str) or len(value)>256:
        raise ValueError('INVALID_LOG_PATH')
    path=PureWindowsPath(value)
    if (path.is_absolute() or path.drive or value.startswith(('/', '\\')) or ':' in value
            or len(path.parts)<2 or path.parts[0]!='UserData'
            or path.suffix.lower()!='.log'
            or any(p in ('.','..') or p.endswith((' ','.')) or
                   any(ord(c)<32 or c in '<>"|?*' for c in p) for p in path.parts)):
        raise ValueError('INVALID_LOG_PATH')
    return '/'.join(path.parts)


class LogService:
    def __init__(self,paths):
        self.paths=paths
        self._lock=threading.RLock()
        self._active={}
        self._closed=False

    def _reader(self,installation_id):
        self.paths.check_owned_paths()
        db=sqlite3.connect(self.paths.database.as_uri()+'?mode=ro',uri=True,timeout=2)
        db.row_factory=sqlite3.Row
        try:
            if db.execute('PRAGMA application_id').fetchone()[0]!=DATABASE_APPLICATION_ID:
                raise RuntimePolicyError('LOG_DATABASE_INVALID')
            row=db.execute('SELECT payload FROM installations WHERE installation_id=?',(installation_id,)).fetchone()
            if row is None:raise RuntimePolicyError('INSTALLATION_NOT_FOUND')
            return db,json.loads(row[0])
        except BaseException:
            db.close();raise

    def watch(self,jobs,request_id,installation_id,user_logs=()):
        names=tuple(sorted({user_log_path(name) for name in user_logs}))
        if len(names)>8:raise ValueError('LOG_SOURCE_LIMIT')
        db,installation=self._reader(installation_id);db.close()
        with self._lock:
            if self._closed:raise RuntimePolicyError('RUNTIME_CLOSED')
            active=self._active.get(installation_id)
            if active is not None:
                if active['names']!=names:raise RuntimePolicyError('LOG_WATCH_OPTIONS_CHANGED')
                return dict(jobs.snapshot(active['task_id']),reused=True)
            if len(self._active)>=4:raise RuntimePolicyError('CAPACITY_EXCEEDED')
            active={'names':names,'task_id':None}
            self._active[installation_id]=active
            def release(reason=None):
                with self._lock:
                    if self._active.get(installation_id) is active:self._active.pop(installation_id,None)
            def worker(task):
                try:return self._watch(installation,names,task)
                finally:release()
            try:
                started=jobs.start(request_id,worker,on_abort=release)
                active['task_id']=started['task_id']
                return dict(started,reused=False)
            except BaseException:
                release();raise

    @staticmethod
    def _paths(root,names):
        result=[('MelonLoader/Latest.log','latest')]
        errors=[]
        try:
            directory=safe_path(root,'MelonLoader/Logs',directory=True)
            # Directory iteration is bounded before sorting; never walk a tree.
            entries=[]
            for index,path in enumerate(directory.iterdir()):
                if index>=256:
                    errors.append('ARCHIVE_DIRECTORY_LIMIT');break
                if path.suffix.lower()=='.log':entries.append(path.name)
            if len(entries)>MAX_ARCHIVES:errors.append('ARCHIVE_SOURCE_LIMIT')
            result += [('MelonLoader/Logs/'+name,'archive') for name in sorted(entries)[:MAX_ARCHIVES]]
        except FileNotFoundError:pass
        except (OSError,ValueError):errors.append('ARCHIVE_DIRECTORY_UNREADABLE')
        result += [(name,'user') for name in names]
        return result,errors

    def _watch(self,installation,names,task):
        root=Path(installation['canonical_root']);installation_id=installation['installation_id']
        if root==self.paths.root or root in self.paths.root.parents or self.paths.root in root.parents:
            raise RuntimePolicyError('LOG_ROOT_CONFLICT')
        store=Store(self.paths.database);repo=EvidenceRepository(store)
        readers={};trackers={};count=0;last=None
        try:
            while not self._closed:
                task.check_cancelled();self.paths.check_owned_paths()
                if identity(root)!=installation['file_identity']:
                    raise RuntimePolicyError('LOG_INSTALLATION_CHANGED')
                paths,errors=self._paths(root,names)
                for relative,kind in paths:
                    task.check_cancelled()
                    if relative not in readers:
                        sid=repo.register_source(installation_id,relative,kind)
                        source=next(s for s in repo.sources(installation_id) if s['source_id']==sid)
                        checkpoint=source['state'] or {}
                        readers[relative]=(sid,Tailer(root,relative,state=checkpoint.get('tailer'),
                                                     cancelled=lambda: task.cancelled))
                        trackers[relative]=SessionTracker(sid,state=checkpoint.get('sessions'))
                    sid,reader=readers[relative];result=reader.poll()
                    tracker=trackers[relative];records=tracker.classify(result['records'])
                    checkpoint={'tailer':result['state'],'sessions':tracker.export_state()}
                    snapshot=result.get('snapshot')
                    if result['state']['pending'] or result['state']['fragmented']:
                        snapshot=None
                    if snapshot is not None:snapshot=dict(snapshot,epoch=result['epoch'])
                    ids=repo.ingest(sid,checkpoint,records,snapshot=snapshot)
                    count+=len(ids)
                    if result['status']=='error':errors.append(result.get('error_code') or 'LOG_READ_ERROR')
                update=(count,tuple(errors),len(readers))
                if update!=last:
                    task.progress(count,None,'logs','LOG_EVIDENCE_UPDATED',
                                  {'evidence_count':count,'source_count':len(readers),'errors':errors[:16],
                                   'coverage':'partial' if errors else 'monitoring'})
                    last=update
                time.sleep(POLL_SECONDS)
            return {'evidence_count':count,'stopped':True}
        except ValueError as exc:
            if str(exc)=='LOG_CAPACITY_EXCEEDED':
                task.progress(count,None,'logs','LOG_CAPACITY_EXCEEDED',
                              {'evidence_count':count,'coverage':'partial',
                               'errors':['LOG_CAPACITY_EXCEEDED']})
            raise
        finally:
            store.close()

    def _query(self,installation_id,method,*args,**kwargs):
        db,_=self._reader(installation_id)
        try:
            repo=EvidenceRepository(SimpleNamespace(connection=db))
            return getattr(repo,method)(installation_id,*args,**kwargs)
        except KeyError:
            raise RuntimePolicyError('EVIDENCE_NOT_FOUND') from None
        finally:db.close()

    def sources(self,installation_id):
        rows=self._query(installation_id,'sources')
        # Checkpoints contain decoder buffers; never expose them to a page.
        return {'sources':[{k:r[k] for k in ('source_id','relative_path','kind')} for r in rows]}

    def evidence(self,installation_id,after=0,limit=20,session_id=None,group_id=None):
        return self._query(installation_id,'list',after=after,limit=limit,session_id=session_id,group_id=group_id)

    def read(self,installation_id,evidence_id,offset=0,limit=4096):
        return self._query(installation_id,'read',evidence_id,offset=offset,limit=limit)

    def sessions(self,installation_id,after=0,limit=20):
        return self._query(installation_id,'sessions',after=after,limit=limit)

    def close(self):
        with self._lock:self._closed=True
