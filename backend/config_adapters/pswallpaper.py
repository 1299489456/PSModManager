"""Field-only edits for PSWallpaper's INI-like cfg, not a TOML serializer."""
from configparser import ConfigParser, Error as IniError
from dataclasses import dataclass
import re


MAX_BYTES = 64 * 1024
FIELDS = (
    dict(key='WallpaperFit', label='壁纸适配方式', kind='choice', options=['cover', 'fit', 'stretch']),
    dict(key='WallpaperBrightness', label='壁纸亮度', kind='integer', min=0, max=100),
    dict(key='LogToFile', label='写入壁纸日志', kind='boolean'),
)


class ConfigRejected(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _value(key, text):
    if key == 'WallpaperFit' and text in ('cover', 'fit', 'stretch'):
        return text
    if key == 'WallpaperBrightness' and re.fullmatch(r'[+-]?[0-9]+', text):
        value = int(text)
        if 0 <= value <= 100:
            return value
    if key == 'LogToFile' and text.lower() in ('true', 'false'):
        return text.lower() == 'true'
    raise ConfigRejected('CONFIG_UNSUPPORTED')


def _replacement(key, value):
    if key == 'WallpaperFit' and type(value) is str and value in ('cover', 'fit', 'stretch'):
        return value
    if key == 'WallpaperBrightness' and type(value) is int and 0 <= value <= 100:
        return str(value)
    if key == 'LogToFile' and type(value) is bool:
        return 'true' if value else 'false'
    raise ConfigRejected('CONFIG_VALUE_INVALID')


@dataclass
class Document:
    raw: bytes
    text: str
    encoding: str
    bom: bytes
    values: dict
    spans: dict

    def fields(self):
        return [dict(field, value=self.values[field['key']]) for field in FIELDS if field['key'] in self.values]

    def patch(self, changes):
        if type(changes) is not dict or not 1 <= len(changes) <= len(FIELDS):
            raise ConfigRejected('CONFIG_VALUE_INVALID')
        replacements = []
        for key, value in changes.items():
            if key not in self.spans:
                raise ConfigRejected('CONFIG_VALUE_INVALID')
            replacement = _replacement(key, value)
            if value != self.values[key]:
                start, end = self.spans[key]
                replacements.append((start, end, replacement))
        if not replacements:
            return self.raw
        text = self.text
        for start, end, replacement in sorted(replacements, reverse=True):
            text = text[:start] + replacement + text[end:]
        return self.bom + text.encode(self.encoding)


def parse(raw):
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise ConfigRejected('CONFIG_UNSUPPORTED')
    bom, encoding = b'', 'utf-8'
    for marker, codec in ((b'\xef\xbb\xbf', 'utf-8'), (b'\xff\xfe', 'utf-16-le'), (b'\xfe\xff', 'utf-16-be')):
        if raw.startswith(marker):
            bom, encoding = marker, codec
            break
    try:
        text = raw[len(bom):].decode(encoding)
        if '\x00' in text:
            raise ValueError()
        parser = ConfigParser(interpolation=None, delimiters=('=',), strict=True,
                              inline_comment_prefixes=('#', ';'), empty_lines_in_values=False)
        parser.optionxform = str
        parser.read_string(text)
        if 'PSWallpaper' not in parser:
            raise ValueError()
        # Defaults/inheritance are not this Mod's known format.
        if parser.defaults():
            raise ValueError()
        section, offset, spans, values = None, 0, {}, {}
        for line in text.splitlines(keepends=True):
            body = line.rstrip('\r\n')
            header = re.fullmatch(r'[ \t]*\[([^\]]+)\][ \t]*(?:[#;].*)?', body)
            if header:
                section = header[1]
            elif section == 'PSWallpaper':
                match = re.match(r'[ \t]*(WallpaperFit|WallpaperBrightness|LogToFile)[ \t]*=[ \t]*(.*)$', body)
                if match and match[1] in parser['PSWallpaper']:
                    key, tail = match[1], match[2]
                    comment = re.search(r'(?<!\S)[#;]', tail)
                    token = (tail[:comment.start()] if comment else tail).rstrip(' \t')
                    if key in spans or token != parser['PSWallpaper'][key]:
                        raise ValueError()
                    values[key] = _value(key, token)
                    spans[key] = (offset + match.start(2), offset + match.start(2) + len(token))
            offset += len(line)
        supported = {field['key'] for field in FIELDS} & set(parser['PSWallpaper'])
        if not values or supported != set(values):
            raise ValueError()
        return Document(raw, text, encoding, bom, values, spans)
    except (UnicodeError, IniError, ValueError, RecursionError):
        raise ConfigRejected('CONFIG_UNSUPPORTED') from None
