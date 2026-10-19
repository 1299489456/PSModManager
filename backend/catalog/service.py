"""Read-only, generation-bound projections for component details and graph UI."""
import json

from backend.scanning.content_manifests import analyze
from backend.storage import Store
from backend.storage.inventory_repository import InventoryRepository
from backend.runtime.jobs import RuntimePolicyError
from .relations import build_graph

MAX_GRAPH_ENTRIES = 512
MAX_GRAPH_BYTES = 16 * 1024 * 1024


class CatalogService:
    def __init__(self, paths):
        self.paths = paths

    def _open(self, installation_id):
        self.paths.check_owned_paths()
        store = Store(self.paths.database)
        try:
            store.get('Installation', installation_id)
            return store
        except KeyError:
            store.close()
            raise RuntimePolicyError('INSTALLATION_NOT_FOUND') from None

    @staticmethod
    def _analysis(row, observed_at):
        return analyze(row.get('metadata') or {}, artifact_sha256=row.get('sha256'), observed_at=observed_at)

    def detail(self, installation_id, entry_id, generation=None):
        store = self._open(installation_id)
        try:
            repo = InventoryRepository(store)
            page = repo.list(installation_id, generation, limit=1)
            row = repo.entry(installation_id, page['generation'], entry_id)
            scan = repo.summary(installation_id, page['generation'])
            analysis = self._analysis(row, scan['finished_at'] or scan['started_at'])
            # Full ID list is independently paged by inventory.contents. Keep
            # details bounded even with 256 long namespace-qualified IDs.
            output = dict(installation_id=installation_id, generation=page['generation'],
                          scan_status=page['status'], entry_id=entry_id,
                          relative_path=row['relative_path'], sha256=row['sha256'],
                          presence=row['presence'], parser_status=row['status'], prediction=row['prediction'],
                          analysis=analysis,interpretation_version='psmm-content-relations-v1')
            output['analysis']['content_id_count'] = len(analysis['content_ids'])
            output['analysis']['content_ids'] = analysis['content_ids'][:16]
            for manifest in analysis['manifests']:
                manifest['content_id_count'] = len(manifest.pop('content_ids', []))
            return output
        except KeyError:
            raise RuntimePolicyError('INVENTORY_ENTRY_NOT_FOUND') from None
        finally:
            store.close()

    def contents(self, installation_id, entry_id, generation=None, offset=0, limit=20):
        store = self._open(installation_id)
        try:
            repo = InventoryRepository(store); page = repo.list(installation_id, generation, limit=1)
            row = repo.entry(installation_id, page['generation'], entry_id)
            scan = repo.summary(installation_id, page['generation'])
            ids = self._analysis(row, scan['finished_at'] or scan['started_at'])['content_ids']
            return dict(installation_id=installation_id, generation=page['generation'], entry_id=entry_id,
                        total=len(ids), offset=offset, entries=ids[offset:offset+limit],
                        next_offset=offset+limit if offset+limit<len(ids) else None)
        except KeyError:
            raise RuntimePolicyError('INVENTORY_ENTRY_NOT_FOUND') from None
        finally:
            store.close()

    def graph(self, installation_id, generation=None, offset=0, limit=20):
        store = self._open(installation_id)
        try:
            repo = InventoryRepository(store);page = repo.list(installation_id, generation, limit=100)
            generation = page['generation']; rows=[];used=0;cursor=0;truncated=False
            if generation is None:
                return dict(installation_id=installation_id,generation=None,coverage='not_run',relations=[],total=0,next_offset=None)
            scan = repo.summary(installation_id,generation);observed_at=scan['finished_at'] or scan['started_at']
            while True:
                for row in page['entries']:
                    used += len(json.dumps(row,ensure_ascii=False).encode('utf-8'))
                    if len(rows)>=MAX_GRAPH_ENTRIES or used>MAX_GRAPH_BYTES:
                        truncated=True;break
                    row['analysis']=self._analysis(row,observed_at);rows.append(row)
                cursor += len(page['entries'])
                if truncated or cursor>=page['total']:break
                page=repo.list(installation_id,generation,limit=100,offset=cursor)
            graph=build_graph(rows,generation=generation,installation_id=installation_id,observed_at=observed_at)
            incomplete=(truncated or scan['status']!='complete' or graph.get('coverage')=='partial'
                        or any(row['analysis'].get('resource_coverage',{}).get('status')=='partial' for row in rows)
                        or any(m['status']!='valid' for row in rows for m in row['analysis']['manifests']))
            if incomplete:
                for edge in graph['relations']:
                    if edge.get('resolution')=='missing':edge['resolution']='unknown'
            edges=graph['relations']; selected=[]
            for edge in edges[offset:offset+limit]:
                if len(json.dumps(selected+[edge],ensure_ascii=False).encode('utf-8'))>40*1024:break
                selected.append(edge)
            return dict(installation_id=installation_id,generation=generation,coverage='partial' if incomplete else 'complete',
                        scan_status=scan['status'],node_count=len(graph['nodes']),relations=selected,total=len(edges),
                        offset=offset,next_offset=offset+len(selected) if offset+len(selected)<len(edges) else None,
                        diagnostics=graph['diagnostics'][:16],truncated=truncated,plan_validation='not_run',
                        dependency_coverage='unknown',interpretation_version='psmm-content-relations-v1')
        except KeyError:
            raise RuntimePolicyError('INVENTORY_SCAN_NOT_FOUND') from None
        finally:
            store.close()
