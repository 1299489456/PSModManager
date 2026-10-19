"""Validation and copying at the asynchronous JSON event boundary."""
import json
import math
import re


class RuntimePolicyError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def json_copy(value, *, limit=32768):
    def validate(item, depth=0):
        if depth > 12:
            raise RuntimePolicyError("INVALID_JOB_OUTPUT")
        if item is None or type(item) in (bool, str, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list and len(item) <= 1024:
            for child in item:
                validate(child, depth + 1)
            return
        if type(item) is dict and len(item) <= 128 and all(type(k) is str for k in item):
            for child in item.values():
                validate(child, depth + 1)
            return
        raise RuntimePolicyError("INVALID_JOB_OUTPUT")

    validate(value)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > limit:
            raise RuntimePolicyError("INVALID_JOB_OUTPUT")
        return json.loads(encoded)
    except (ValueError, OverflowError, UnicodeError):
        raise RuntimePolicyError("INVALID_JOB_OUTPUT") from None


def token(value):
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value) is None:
        raise RuntimePolicyError("INVALID_JOB_PROGRESS")
    return value
