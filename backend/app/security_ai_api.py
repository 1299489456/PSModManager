"""Explicit preview/confirm endpoints; only host-resolved completed reports."""
from . import native_api
from .native_api import Mod, Payload, ServiceToken
from backend.ai.security_report import SecurityReportAnalysis


class Preview(Mod):
    report_id: ServiceToken


class Send(Payload):
    preview_id: ServiceToken


def commands(context, load_report=None):
    def load(installation_id, mod_id, report_id):
        selected = native_api.installation(context, installation_id)
        return context.mod_security.detail(selected, mod_id)

    analysis = SecurityReportAnalysis(context.ai, context.paths, load_report or load)
    return {
        'security.ai.preview': (Preview, lambda _, p: analysis.preview(str(p.installation_id), p.id, p.report_id)),
        'security.ai.send': (Send, lambda rid, p: analysis.send(rid, p.preview_id)),
    }
