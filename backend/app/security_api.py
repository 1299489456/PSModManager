"""Local Mod safety commands; the host resolves every package and write target."""
from contextlib import nullcontext
from typing import Literal

from pydantic import Field, StrictBool

from . import native_api
from .native_api import Installed, Mod, NativeId, ServiceToken


class Scan(Installed):
    ids: list[NativeId] | None = Field(default=None, min_length=1, max_length=1000)
    antivirus: StrictBool = False


class DisposePreview(Mod):
    mode: Literal['disable', 'quarantine']


class Dispose(Installed):
    preview_id: ServiceToken


class Restore(Installed):
    record_id: ServiceToken


def commands(context):
    def call(method, payload, *args, **kwargs):
        selected = native_api.installation(context, payload.installation_id)
        foreground = (context.workshop.foreground_operation(selected)
                      if method in {'dispose', 'restore'} else nullcontext())
        with foreground:
            return getattr(context.mod_security, method)(selected, *args, **kwargs)

    return {
        'security.status': (Installed, lambda _, p: call('status', p)),
        'security.activity': (Installed, lambda _, p: call('activity', p)),
        'security.detail': (Mod, lambda _, p: call('detail', p, p.id)),
        'security.scan': (Scan, lambda _, p: call('scan', p, ids=p.ids, antivirus=p.antivirus)),
        'security.dispose.preview': (DisposePreview, lambda _, p: call('dispose_preview', p, p.id, mode=p.mode)),
        'security.dispose': (Dispose, lambda _, p: call('dispose', p, p.preview_id)),
        'security.restore': (Restore, lambda _, p: call('restore', p, p.record_id)),
    }
