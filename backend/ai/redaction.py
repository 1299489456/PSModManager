"""Bounded text only; remove secrets before requests, events and exports."""
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from backend.utils.redaction import is_sensitive_key

HIDDEN = '[已隐藏]'
SENSITIVE = r'(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret|proxy[_-]?authorization|authorization|signature|credential)'


def _url(match):
    value = match.group(0)
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        if ':' in host:
            host = '['+host+']'
        if parsed.port:
            host += ':'+str(parsed.port)
        query = [(key, HIDDEN if is_sensitive_key(key) or key.casefold() in {'key','token','auth','sig','signature','credential','code'} else data)
                 for key, data in parse_qsl(parsed.query, keep_blank_values=True)]
        return urlunsplit((parsed.scheme, host, parsed.path, urlencode(query), ''))
    except ValueError:
        return HIDDEN


def redact(text, secrets=(), *, partial=False):
    # Remove terminal/control sequences; newline/tab remain readable text.
    text = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', str(text))
    text = re.sub(r'https?://[^\s<>"\']+', _url, text, flags=re.I)
    text = re.sub(r'(?i)\b(?:Bearer|Basic)\s+[^\s,;"\']+', lambda m:m.group(0).split()[0]+' '+HIDDEN, text)
    text = re.sub(r'(?i)(["\']?\b'+SENSITIVE+r'["\']?\s*[:=]\s*)(?:\[已隐藏\]|"[^"\n]*(?:"|$)|\'[^\'\n]*(?:\'|$)|[^\s&,;}\]]+)',
                  lambda m:m.group(1)+HIDDEN, text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]+', HIDDEN, text)
    for key in sorted(set(key for key in secrets if key), key=len, reverse=True):
        text = text.replace(key, HIDDEN)
        if partial:
            # Never publish a trailing fragment of a known key across chunks.
            for count in range(min(len(key)-1,len(text)),0,-1):
                if text.endswith(key[:count]):
                    text = text[:-count]+HIDDEN
                    break
    return text


def redact_arguments(text, secrets=()):
    """Keep valid argument JSON valid when reflecting a provider's tool call."""
    def clean(value):
        if isinstance(value,str):
            return redact(value,secrets)
        if isinstance(value,list):
            return [clean(item) for item in value]
        if isinstance(value,dict):
            return {redact(key,secrets): HIDDEN if is_sensitive_key(key) else clean(item)
                    for key,item in value.items()}
        return value
    try:
        return json.dumps(clean(json.loads(text)),ensure_ascii=False,allow_nan=False)
    except (ValueError,TypeError,RecursionError):
        return redact(text,secrets)
