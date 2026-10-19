"""Small per-send limits shared by text, tool exchanges and connection tests."""
from dataclasses import dataclass
import json
import time


class AIError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class BudgetLimits:
    request_seconds: float = 180
    output_bytes: int = 262144
    frames: int = 65536
    wire_bytes: int = 16777216
    provider_calls: int = 4
    tool_batches: int = 3
    tool_calls: int = 12
    input_bytes: int = 60000


class RequestBudget:
    def __init__(self, limits=None, *, deadline=None):
        self.limits = limits or BudgetLimits()
        end = time.monotonic() + self.limits.request_seconds
        self.deadline = min(end, deadline) if deadline is not None else end
        self.output_bytes = self.frames = self.provider_calls = 0
        self.tool_batches = self.tool_calls = 0
        self.wire_bytes = 0

    def check(self, cancelled):
        cancelled()
        if time.monotonic() > self.deadline:
            raise AIError('AI_TIMEOUT')

    def provider(self, messages):
        if len(json.dumps(messages, ensure_ascii=False).encode('utf-8')) > self.limits.input_bytes:
            raise AIError('AI_INPUT_LIMIT')
        if self.provider_calls >= self.limits.provider_calls:
            raise AIError('AI_TOOL_LIMIT')
        self.provider_calls += 1

    def frame(self):
        self.frames += 1
        if self.frames > self.limits.frames:
            raise AIError('AI_STREAM_LIMIT')

    def wire(self, count):
        self.wire_bytes += count
        if self.wire_bytes > self.limits.wire_bytes:
            raise AIError('AI_STREAM_LIMIT')

    def text(self, value):
        self.output_bytes += len(value.encode('utf-8'))
        if self.output_bytes > self.limits.output_bytes:
            raise AIError('AI_OUTPUT_LIMIT')

    def tools(self, count):
        self.tool_batches += 1
        self.tool_calls += count
        if not 1 <= count <= 4 or self.tool_batches > self.limits.tool_batches or self.tool_calls > self.limits.tool_calls:
            raise AIError('AI_TOOL_LIMIT')
