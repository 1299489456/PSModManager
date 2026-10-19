"""Deployment planning policy; no caller-provided paths or approval assertions."""
import json
import unicodedata
import hmac
import secrets
from uuid import UUID

from .paths import ROOTS, GuardRejected, canonical_relative, target_path, pinned_chain, open_handle, _information
from backend.contracts.plans import OwnedFile


MAX_MANAGED_FILES = 5000


class PlanRejected(ValueError):
    def __init__(self, code, conflicts=()):
        self.code = code
        self.conflicts = list(conflicts)[:20]
        super().__init__(code)


def native_approval_id(data_root, plan_hash, *, create=False):
    """Authenticate native issuance across restart, independently of a DTO/DB claim.

    The private issuer key has no bridge/tool read endpoint. Only the host's
    completed human confirmation or closed ordinary Mod action may create it. Ordinary Approval inserts
    cannot compute a valid identity. This is not a defense against arbitrary
    code execution or an administrator reading the private data root.
    """
    import win32file
    path = data_root / '.plan-approval-key'
    with pinned_chain(data_root) as chain:
        try:
            handle = open_handle(path,parent=chain.handles[-1])
        except FileNotFoundError:
            if not create:
                raise PlanRejected('PLAN_APPROVAL_INVALID') from None
            handle = open_handle(path,parent=chain.handles[-1],create=True)
            try:
                secret = secrets.token_bytes(32)
                win32file.WriteFile(handle,secret)
                win32file.FlushFileBuffers(handle)
                chain.check()
            finally:
                handle.Close()
        else:
            try:
                info = _information(handle,False)
                if (info[5]<<32)|info[6] != 32:
                    raise PlanRejected('PLAN_APPROVAL_INVALID')
                _,secret = win32file.ReadFile(handle,32)
                if len(secret)!=32:
                    raise PlanRejected('PLAN_APPROVAL_INVALID')
                chain.check()
            finally:
                handle.Close()
    return UUID(bytes=hmac.digest(secret,('PSModManager.native-approval.v1:'+plan_hash).encode('ascii'),'sha256')[:16])


def target_key(kind, relative):
    if kind not in ROOTS:
        raise PlanRejected('PLAN_TARGET_NOT_ALLOWED')
    canonical_relative(relative)
    return kind + '/' + unicodedata.normalize('NFC', relative).casefold()


def writable_file(file, root):
    # Config, mutable data, unknown files and scripts need their own adapters.
    if file['lifecycle'] != 'immutable' or file['ownership'] not in {'exclusive','shared_identical'}:
        raise PlanRejected('PLAN_TARGET_NOT_ALLOWED')
    target_path(root, file['target_root_kind'], file['relative_path'])
    return target_key(file['target_root_kind'], file['relative_path'])


def check_value(plan, kind):
    matches = [item.expected for item in plan.preconditions if item.kind == kind]
    if len(matches) != 1:
        raise PlanRejected('PLAN_CORRUPT')
    return matches[0]


def owned_after(plan):
    try:
        files = json.loads(check_value(plan, 'owned_after'))
        if not isinstance(files, list) or len(files) > MAX_MANAGED_FILES:
            raise ValueError()
        files = [OwnedFile.model_validate(f).model_dump(mode='json') for f in files]
        if any(f['owner_ids'] != sorted(set(f['owner_ids'])) for f in files):
            raise ValueError()
        keys = [target_key(f['target_root_kind'], f['relative_path']) for f in files]
        if keys != sorted(set(keys)):
            raise ValueError()
        return {key: file for key, file in zip(keys, files)}
    except (ValueError, TypeError, KeyError):
        raise PlanRejected('PLAN_CORRUPT') from None


def capacity(volumes, *, library_volume, target_volume, backup_volume,
             staging, backup, target_growth, available, recovery=0):
    """Sum overlapping demands on physical volumes; never budget them separately."""
    output = {volume: dict(library=0, staging=0, backup=0, target_growth=0,
                          overhead=0, required=0, available=available[volume]) for volume in volumes}
    # Verified library bytes already exist. No download/copy into the library.
    output[library_volume]['library'] += 0
    output[target_volume]['staging'] += staging
    output[target_volume]['target_growth'] += target_growth
    output[backup_volume]['backup'] += backup
    if recovery:
        # Originals remain retained while rollback publishes a verified copy.
        output[target_volume]['recovery'] = recovery
    if staging or backup or target_growth:
        output[backup_volume]['overhead'] += 1024 * 1024
    for value in output.values():
        value['required'] = sum(value.get(key, 0) for key in ('library', 'staging', 'backup', 'target_growth', 'overhead', 'recovery'))
        if value['available'] < value['required']:
            raise PlanRejected('PLAN_SPACE_INSUFFICIENT')
    return output
