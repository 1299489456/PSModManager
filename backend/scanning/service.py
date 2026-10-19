"""Early inventory consumer: bounded explicit-file inspection, no full scan yet."""
import json
from pathlib import Path
from uuid import uuid4

from backend.storage import Store
from backend.runtime.jobs import RuntimePolicyError
from backend.games.probably_stolen.layout import identity
from .worker_client import MetadataWorker, owned_directory, relative_file


class MetadataInspectionService:
    def __init__(self, paths):
        self.paths = paths

    def inspect(self, jobs, request_id, installation_id, files):
        files = [relative_file(value) for value in files]
        if not 1 <= len(files) <= 8 or len(set(files)) != len(files):
            raise ValueError('Expected 1..8 distinct files')
        if any(value.split('/')[0].lower() not in ('mods','plugins','userlibs') or not value.lower().endswith('.dll') for value in files):
            raise ValueError('Only explicit DLLs in registered mod roots')
        self.paths.check_owned_paths()
        store = Store(self.paths.database)
        try:
            installation = store.get('Installation', installation_id)
        except KeyError:
            raise RuntimePolicyError('INSTALLATION_NOT_FOUND') from None
        finally:
            store.close()
        def inspect(task):
            if identity(Path(installation.canonical_root)) != installation.file_identity:
                raise ValueError('Installation directory identity changed')
            worker = MetadataWorker(self.paths.root)
            reports = owned_directory(self.paths.root / 'metadata/reports')
            rows = []
            for index, relative in enumerate(files):
                task.check_cancelled()
                result = worker.inspect(installation.canonical_root, relative, cancelled=lambda:task.cancelled)
                task.check_cancelled()
                report_id = str(uuid4())
                with (reports / (report_id+'.json')).open('x',encoding='utf-8') as output:
                    json.dump(result,output,ensure_ascii=False,allow_nan=False)
                metadata = result['metadata'] or {}
                rows.append(dict(relative_path=relative,sha256=result['sha256'],size=result['size'],
                                 status=result['status'],error=result['error'],metadata_report_id=report_id,
                                 assembly_name=metadata.get('assembly_name'),assembly_version=metadata.get('assembly_version')))
                task.progress(index+1,len(files),'metadata','METADATA_FILE_CHECKED')
            return dict(installation_id=installation_id,files=rows,
                        coverage='explicit_files_only',complete_inventory=False)
        return jobs.start(request_id,inspect)
