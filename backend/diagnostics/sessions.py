"""Conservative log-declared sessions; no process or deployment inference."""
from copy import deepcopy
from datetime import datetime, timezone
import re
from uuid import NAMESPACE_URL, uuid5


_TIME = re.compile(r"^\[?(?P<value>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})|\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]?(?:\s+|$)")
_BANNER = re.compile(r"^(?:MelonLoader\s+v?\d+\.\d+\.\d+(?:[\w.+-]*)|(?:Version:\s*|v)\d+\.\d+\.\d+(?:[\w.+-]*))(?:\s+(?:Open-Beta|Alpha|Beta|Release))?\s*$", re.I)
_PID = re.compile(r"^(?:Process ID|PID)\s*[:=]\s*(\d{1,10})\s*$", re.I)
_EXCEPTION = re.compile(r"^(?:[\w.`+]+(?:Exception|Error))\s*:")
_CONTINUATION = re.compile(r"^\s*(?:at\s+\S|--->\s|--- End of (?:inner exception|stack trace)|Caused by:\s)")
MAX_GROUP_LINES = 128


def _fields(text):
    body = text.rstrip("\r\n")
    match = _TIME.match(body)
    source_time = match.group("value") if match else None
    if match:
        body = body[match.end():]
    melon_prefix = False
    while body.startswith("["):
        end = body.find("]")
        if end < 0:
            break
        melon_prefix |= body[1:end].casefold() == "melonloader"
        body = body[end + 1:].lstrip(" ")
    return body, source_time, melon_prefix


def _utc(value):
    if not value or "T" not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


class SessionTracker:
    """State is small and serializable; callers persist it beside tail offsets."""

    def __init__(self, source_id, state=None):
        self.source_id = str(source_id)
        self._state = {"version": 1, "source_id": self.source_id, "epoch": None,
                       "session": None, "stage": "unknown", "group": None, "group_lines": 0,
                       "stack": False}
        if state is not None:
            if state.get("version") != 1 or state.get("source_id") != self.source_id:
                raise ValueError("SESSION_STATE_SOURCE_MISMATCH")
            self._state.update(deepcopy(state))

    def _id(self, epoch, offset, kind):
        return str(uuid5(NAMESPACE_URL, f"PSModManager/log/{self.source_id}/{epoch}/{offset}/{kind}"))

    def _session(self, epoch, offset, *, reason="no_explicit_start_marker", pid=None, started_at=None):
        return {"session_id": self._id(epoch, offset, "session/" + reason), "pid": pid,
                "started_at": started_at, "generation_id": None,
                "association_confidence": "unknown" if reason == "no_explicit_start_marker" else "inferred",
                "reason": reason}

    def classify(self, records):
        output = []
        for record in records:
            row = dict(record)
            epoch, offset = str(row["epoch"]), row["byte_start"]
            # A decoder's discarded old-epoch fragment must not switch the active session.
            if row.get("abandoned"):
                row.update(session=self._session(epoch, 0), stage="unknown", source_time=None,
                           event_group_id=self._id(epoch, offset, "event"))
                output.append(row)
                continue
            state = self._state
            if epoch != state["epoch"]:
                state.update(epoch=epoch, session=self._session(epoch, 0), stage="unknown",
                             group=None, group_lines=0, stack=False)
            body, source_time, melon_prefix = _fields(row["text"])
            complete = bool(row.get("complete_line")) and not row.get("fragment")
            if complete:
                banner = _BANNER.fullmatch(body) and (body.lower().startswith("melonloader") or melon_prefix)
                pid_match = _PID.fullmatch(body)
                pid = int(pid_match[1]) if pid_match and 0 < int(pid_match[1]) <= 4294967295 else None
                if banner:
                    state.update(session=self._session(epoch, offset, reason="log_start_marker_unverified",
                                                       started_at=_utc(source_time)), stage="bootstrap",
                                 group=None, stack=False, group_lines=0)
                elif pid is not None:
                    if state["session"]["pid"] != pid:
                        state.update(session=self._session(epoch, offset, pid=pid,
                                                           reason="log_declared_unverified", started_at=_utc(source_time)),
                                     stage="unknown", group=None, stack=False, group_lines=0)
                if re.match(r"^Loading (?:assembly|assemblies|Mods|Plugins|UserLibs)(?:\s|:|\.|$)", body, re.I):
                    state["stage"] = "assembly_loading"
                elif re.match(r"^(?:Initializing|Initialization(?: started| complete)?)(?:\s|:|\.|$)", body, re.I):
                    state["stage"] = "initialization"
                elif re.match(r"^(?:(?:ItemForge|CharacterForge):\s*)?Registered\s+\d+\s+(?:items?|recipes?|characters?|bundles?)\b", body, re.I):
                    state["stage"] = "content"
            continuation = complete and state["stack"] and _CONTINUATION.match(body)
            if not continuation or state["group_lines"] >= MAX_GROUP_LINES:
                state["group"] = self._id(epoch, offset, "event")
                state["group_lines"] = 0
            state["group_lines"] += 1
            state["stack"] = bool(complete and (_EXCEPTION.match(body) or continuation))
            row.update(session=deepcopy(state["session"]), stage=state["stage"], source_time=source_time,
                       event_group_id=state["group"])
            output.append(row)
        return output

    def export_state(self):
        return deepcopy(self._state)
