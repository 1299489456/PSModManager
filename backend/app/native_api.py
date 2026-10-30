"""Closed desktop commands for the game's native Mod format.

Only the host's pickers grant import/export paths. The page deals in registered
installation IDs, manifest IDs and expiring tokens, never arbitrary file paths.
"""
import base64
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import time
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints

from backend.app.paths import reject_reparse
from backend.runtime.events import RuntimePolicyError
from backend.storage import Store

NativeId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=64)]
Revision = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=256)]
ServiceToken = Annotated[str, StringConstraints(strict=True, pattern=r'^[a-f0-9]{32}$')]


class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Installed(Payload):
    installation_id: UUID


class Mod(Installed):
    id: NativeId


class Order(Installed):
    enabled_ids: list[NativeId] = Field(max_length=1000)


class Validate(Order):
    rules: list[dict] | None = Field(default=None, max_length=1000)


class Save(Order):
    expected_revision: Revision


class ConfigEntry(Payload):
    key: str = Field(min_length=1, max_length=1024)
    value: str = Field(max_length=65536)


class ConfigSave(Mod):
    expected_revision: Revision
    entries: list[ConfigEntry] = Field(max_length=1000)


class ImportChoose(Installed):
    kind: Literal['directory', 'zip']


class ImportPreview(Installed):
    source_handle: UUID


class ImportCommit(Installed):
    preview_id: ServiceToken
    expected_revision: Revision


class RecoveryRestore(Installed):
    id: ServiceToken
    expected_revision: Revision


class LocalChange(Mod):
    expected_revision: Revision


class ProfileCreate(Order):
    name: str = Field(min_length=1, max_length=100)


class ProfileIdentity(Installed):
    profile_id: str = Field(pattern=r'^(?:[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})$')


class ProfileUpdate(ProfileCreate):
    profile_id: ServiceToken


class ProfileExport(ProfileIdentity):
    kind: Literal['file', 'text'] = 'file'


class ProfileImport(ProfileCreate):
    source_handle: UUID


class Open(Installed):
    id: NativeId | None = None
    kind: Literal['mod', 'config', 'data', 'local_mods', 'persistent']


class RulesSave(Installed):
    expected_revision: Revision
    rules: list[dict] = Field(max_length=1000)


def installation(context, identity):
    """Resolve durable authority, not a page-supplied installation object."""
    context.paths.check_owned_paths()
    store = Store(context.paths.database, validate=False)
    try:
        try:
            return store.get('Installation', str(identity)).model_dump(mode='json')
        except KeyError:
            raise RuntimePolicyError('INSTALLATION_NOT_FOUND') from None
    finally:
        store.close()


@contextmanager
def write_guard(context, value):
    with context.deployment_guards.operation(str(value['installation_id'])) as session:
        yield session


def process_status(context, value, *, foreground=False):
    result = context.deployment_guards.status(str(value['installation_id']))
    status = dict(state={'clear': 'stopped', 'running': 'running'}.get(result['process_status'], 'unknown'),
                  blockers=result['blockers'], writable=result['ready'])
    # Foreground commands drain our own read-only probe before taking a lease.
    # Only that proven owner is preemptible; a queued probe proves nothing.
    if (foreground and status['state'] == 'stopped' and
            status['blockers'] == ['GUARD_LEASE_BUSY'] and
            context.workshop.probe_owns_lease(value)):
        status.update(blockers=[], writable=True, busy_owner='workshop_probe')
    return status


def _preview_image(mod):
    """Return bounded image bytes, not a file URL usable by untrusted markup."""
    path, root = mod.get('preview_path'), mod.get('folder_path')
    if not path or not root:
        return
    try:
        target, folder = Path(path), Path(root)
        reject_reparse(target)
        if not target.resolve().is_relative_to(folder.resolve()):
            return
        info = target.stat()
        if info.st_nlink != 1 or not 0 < info.st_size <= 4 * 1024 * 1024:
            return
        content = target.read_bytes()
        kind = ('png' if content.startswith(b'\x89PNG\r\n\x1a\n') else
                'jpeg' if content.startswith(b'\xff\xd8\xff') else
                'webp' if content[:4] == b'RIFF' and content[8:12] == b'WEBP' else None)
        if kind:
            mod['preview_url'] = 'data:image/' + kind + ';base64,' + base64.b64encode(content).decode('ascii')
    except (OSError, ValueError):
        return


class NativeCommands:
    def __init__(self, context):
        self.context = context
        self.sources = {}

    def _installation(self, payload):
        return installation(self.context, payload.installation_id)

    @property
    def service(self):
        return self.context.native_mods

    def call(self, method, payload, *args, **kwargs):
        selected = self._installation(payload)
        # An idle Steam observation must yield before a user's game-file write.
        # Workshop's own writes use the original guard and do not recurse here.
        writes = {'apply', 'save', 'rules_save', 'config_save', 'import_commit',
                  'recovery_restore', 'recovery_create', 'local_remove', 'source_switch'}
        foreground = (self.context.workshop.foreground_operation(selected)
                      if method in writes else nullcontext())
        with foreground:
            if method == 'apply':
                self.context.mod_security.check_launch(selected, enabled_ids=args[1])
            result = getattr(self.service, method)(selected, *args, **kwargs)
            return self._result(payload, selected, result)

    def _result(self, payload, selected, result):
        if isinstance(result, dict) and 'mods' in result and 'runtime' in result:
            # A service can assemble its result while its own write lease is
            # held. Re-observe after release, without clearing native blockers.
            result['runtime'].update(process_status(self.context, selected, foreground=True))
            if result['runtime'].get('blocking_reason'):
                result['runtime']['writable'] = False
            result['identity'] = self.identity(payload)
        return result

    def save(self, payload):
        return self.call('apply', payload, payload.expected_revision, payload.enabled_ids)

    def snapshot(self, payload):
        return self.call('snapshot', payload)

    def identity(self, payload):
        from backend.native_mods import locations
        from backend.games.probably_stolen.native_identity import observe
        selected = self._installation(payload)
        return observe(selected, locations(selected)['persistent_root'])

    def detail(self, payload):
        result = self.call('detail', payload, payload.id)
        _preview_image(result['mod'])
        return result

    def grant(self, selected, kind, identity=None):
        if not selected:
            return dict(source_handle=None, cancelled=True)
        path = Path(selected)
        if not path.is_absolute() or str(path).startswith(('\\\\', '//')):
            raise RuntimePolicyError('IMPORT_PATH_INVALID')
        reject_reparse(path)
        now = time.monotonic()
        self.sources = {key: value for key, value in self.sources.items() if value['expires'] > now}
        if len(self.sources) >= 32:
            raise RuntimePolicyError('CAPACITY_EXCEEDED')
        handle = str(uuid4())
        self.sources[handle] = dict(path=str(path), kind=kind, installation_id=str(identity) if identity else None,
                                    expires=now + 600)
        return dict(source_handle=handle, display_name=path.name, kind=kind)

    def source(self, handle, identity, kinds):
        row = self.sources.get(str(handle))
        if (not row or row['expires'] <= time.monotonic() or row['kind'] not in kinds or
                row['installation_id'] not in (None, str(identity))):
            raise RuntimePolicyError('SOURCE_EXPIRED')
        return row

    def choose(self, payload):
        self._installation(payload)
        return self.grant(self.context.choose_import_source(payload.kind), payload.kind, payload.installation_id)

    def import_preview(self, payload):
        row = self.source(payload.source_handle, payload.installation_id, ('directory', 'zip'))
        return self.call('import_preview', payload, row['path'])

    def profile_list(self, payload):
        result = self.call('profile_list', payload)
        # Previous-format profiles remain visible, but never reach the old executor.
        for old in self.context.profiles.list(str(payload.installation_id)).get('profiles', []):
            result['items'].append(dict(id=old['profile_id'], name=old['name'], legacy=True,
                                        enabled_ids=[], preview_only=True))
        return result

    def profile_preview(self, payload):
        old = next((item for item in self.profile_list(payload)['items']
                    if item['id'] == str(payload.profile_id) and item.get('legacy')), None)
        if old:
            source = self.context.profiles.get(str(payload.profile_id))
            result = self.call('migration_preview', payload, source)
            return dict(result, profile=old, legacy=True, preview_only=True)
        return self.call('profile_preview', payload, str(payload.profile_id))

    def profile_export(self, payload):
        value = self.profile_preview(payload)
        profile = value['profile']
        if profile.get('legacy'):
            manifest = self.context.profiles.export(str(payload.profile_id))
        else:
            # Portable identities only; no game approval, absolute paths or user configuration.
            manifest = dict(format='psmm.native-profile.v1', name=profile['name'],
                            enabled_ids=profile['enabled_ids'], sources=profile.get('sources', []),
                            rules=profile.get('rules', []))
        if payload.kind == 'text':
            return dict(manifest=manifest, text=json.dumps(manifest, ensure_ascii=False, indent=2))
        from .profile_export import save_manifest
        return save_manifest(self.context.paths, self.context.choose_profile_destination(), manifest)

    def profile_import_choose(self, payload):
        self._installation(payload)
        selected = self.context.choose_profile_source()
        grant = self.grant(selected, 'profile', payload.installation_id)
        if not grant.get('source_handle'):
            return grant
        path = Path(selected)
        if path.suffix.lower() != '.json' or path.stat().st_size > 262144 or path.stat().st_nlink != 1:
            raise RuntimePolicyError('PROFILE_MANIFEST_INVALID')
        document = json.loads(path.read_text('utf-8-sig'))
        if not isinstance(document, dict):
            raise RuntimePolicyError('PROFILE_MANIFEST_INVALID')
        modern = document.get('format') == 'psmm.native-profile.v1'
        if modern:
            value = ProfileCreate.model_validate(dict(installation_id=payload.installation_id,
                name=document.get('name'), enabled_ids=document.get('enabled_ids')))
            preview = self.call('validate', payload, value.enabled_ids, document.get('rules', []))
        else:
            preview = self.call('migration_preview', payload, document)
        row = self.sources[grant['source_handle']]
        row['document'] = document
        row['legacy'] = not modern
        return {**grant, **preview, 'profile': document, 'legacy': not modern, 'preview_only': not modern}

    def profile_import_commit(self, payload):
        row = self.source(payload.source_handle, payload.installation_id, ('profile',))
        if row.get('legacy') or 'document' not in row:
            raise RuntimePolicyError('PROFILE_MANIFEST_INVALID')
        # Import the previewed bytes held by the host, not changed file contents.
        if payload.enabled_ids != row['document']['enabled_ids']:
            raise RuntimePolicyError('PROFILE_PREVIEW_CHANGED')
        value = self.call('profile_create', payload, payload.name, payload.enabled_ids,
                          row['document'].get('rules', []), row['document'].get('sources'))
        self.sources.pop(str(payload.source_handle), None)
        return value

    def open(self, payload):
        from backend.native_mods import locations
        locations_ = locations(self._installation(payload))
        if payload.kind in ('mod', 'config', 'data'):
            if payload.id is None:
                raise RuntimePolicyError('INVALID_REQUEST')
            mod = self.call('detail', payload, payload.id)['mod']
            if payload.kind == 'mod':
                target = Path(mod['folder_path'])
            else:
                target = Path(locations_['persistent_root']) / ('ModConfigs' if payload.kind == 'config' else 'ModData')
                if payload.kind == 'data':
                    target = target / mod['id']
        else:
            target = Path(locations_['mods_root' if payload.kind == 'local_mods' else 'persistent_root'])
        reject_reparse(target)
        if not target.is_dir():
            return dict(opened=False, message='这个目录尚未由游戏或模组创建。')
        os.startfile(str(target))
        return dict(opened=True)

    def commands(self):
        c = self.call
        return {
            'native.snapshot': (Installed, lambda _, p: self.snapshot(p)),
            'native.identity': (Installed, lambda _, p: self.identity(p)),
            'native.detail': (Mod, lambda _, p: self.detail(p)),
            'native.validate': (Validate, lambda _, p: c('validate', p, p.enabled_ids, p.rules)),
            'native.rules.read': (Installed, lambda _, p: c('rules_read', p)),
            'native.rules.save': (RulesSave, lambda _, p: c('rules_save', p, p.expected_revision, p.rules)),
            'native.save': (Save, lambda _, p: self.save(p)),
            'native.config.read': (Mod, lambda _, p: c('config_read', p, p.id)),
            'native.config.save': (ConfigSave, lambda _, p: c('config_save', p, p.id, p.expected_revision,
                                                          [row.model_dump() for row in p.entries])),
            'native.import.choose': (ImportChoose, lambda _, p: self.choose(p)),
            'native.import.preview': (ImportPreview, lambda _, p: self.import_preview(p)),
            'native.import.commit': (ImportCommit, lambda _, p: c('import_commit', p, str(p.preview_id), p.expected_revision)),
            'native.recovery.list': (Installed, lambda _, p: c('recovery_list', p)),
            'native.recovery.restore': (RecoveryRestore, lambda _, p: c('recovery_restore', p, str(p.id), p.expected_revision)),
            'native.recovery.create': (Mod, lambda _, p: c('recovery_create', p, p.id)),
            'native.local.remove': (LocalChange, lambda _, p: c('local_remove', p, p.id, p.expected_revision)),
            'native.source.switch': (LocalChange, lambda _, p: c('source_switch', p, p.id, p.expected_revision)),
            'native.profile.list': (Installed, lambda _, p: self.profile_list(p)),
            'native.profile.create': (ProfileCreate, lambda _, p: c('profile_create', p, p.name, p.enabled_ids)),
            'native.profile.update': (ProfileUpdate, lambda _, p: c('profile_update', p, str(p.profile_id), p.name, p.enabled_ids)),
            'native.profile.delete': (ProfileIdentity, lambda _, p: c('profile_delete', p, str(p.profile_id))),
            'native.profile.preview': (ProfileIdentity, lambda _, p: self.profile_preview(p)),
            'native.profile.export': (ProfileExport, lambda _, p: self.profile_export(p)),
            'native.profile.import.choose': (Installed, lambda _, p: self.profile_import_choose(p)),
            'native.profile.import.commit': (ProfileImport, lambda _, p: self.profile_import_commit(p)),
            'native.open': (Open, lambda _, p: self.open(p)),
        }
