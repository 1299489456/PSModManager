"""CASE-035 selected PSWallpaper fields; untouched bytes are the contract."""
import pytest

from backend.config_adapters.pswallpaper import ConfigRejected, MAX_BYTES, parse


TEXT = '''# 壁纸配置，保留注释与未知字段
[PSWallpaper]
WallpaperFile=C:\\wallpapers\\中文.png
WallpaperFit = cover   # 保留适配注释
WallpaperBrightness=55 ; 保留亮度注释
LogToFile=true
UnknownValue=100% 不做字符串插值

[进度标记]
LastGrant=2026-10-12
'''


@pytest.mark.parametrize('encoding,bom', [('utf-8', b''), ('utf-8', b'\xef\xbb\xbf'),
                                         ('utf-16-le', b'\xff\xfe'), ('utf-16-be', b'\xfe\xff')])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_case035_fields_roundtrip_preserves_encoding_comments_and_unknown(encoding, bom, newline):
    text = TEXT.replace('\n', newline)
    raw = bom + text.encode(encoding)
    document = parse(raw)
    assert document.values == dict(WallpaperFit='cover', WallpaperBrightness=55, LogToFile=True)
    changed = document.patch(dict(WallpaperFit='fit', WallpaperBrightness=80, LogToFile=False))
    expected = text.replace('WallpaperFit = cover', 'WallpaperFit = fit').replace(
        'WallpaperBrightness=55', 'WallpaperBrightness=80').replace('LogToFile=true', 'LogToFile=false')
    assert changed == bom + expected.encode(encoding)
    assert document.raw == raw
    assert parse(changed).values == dict(WallpaperFit='fit', WallpaperBrightness=80, LogToFile=False)


def test_case035_noop_keeps_original_scalar_representation_and_eof():
    raw = b'[PSWallpaper]\r\nWallpaperBrightness = +055  # original\r\nLogToFile=TRUE'
    document = parse(raw)
    assert document.patch(dict(WallpaperBrightness=55, LogToFile=True)) == raw
    assert document.patch(dict(LogToFile=False)) == raw.replace(b'LogToFile=TRUE', b'LogToFile=false')


@pytest.mark.parametrize('changes', [{}, {'WallpaperFile': 'arbitrary'}, {'WallpaperBrightness': True},
    {'WallpaperBrightness': 101}, {'WallpaperBrightness': -1}, {'WallpaperBrightness': '80'},
    {'WallpaperBrightness': 1.5}, {'LogToFile': 'false'}, {'LogToFile': 0}, {'WallpaperFit': 'bad'},
    {'LastGrant': 'reset'}, {'WallpaperFit': 'cover\n[Another]\nvalue=bad'}])
def test_case035_only_supported_typed_values_can_change(changes):
    document = parse(TEXT.encode())
    with pytest.raises(ConfigRejected, match='CONFIG_VALUE_INVALID'):
        document.patch(changes)
    assert document.raw == TEXT.encode()


@pytest.mark.parametrize('raw', [b'not a configuration', b'[PSWallpaper]\nWallpaperFit=cover\nWallpaperFit=fit',
    b'[PSWallpaper]\nLogToFile=maybe', b'[PSWallpaper]\nWallpaperBrightness=900',
    b'[PSWallpaper]\nWallpaperFit="cover"', b'[PSWallpaper]\nWallpaperBrightness=55\n    60',
    b'[PSWallpaper]\nUnknown=raw', b'[DEFAULT]\nLogToFile=true\n[PSWallpaper]\nWallpaperFit=cover',
    b'[PSWallpaper]\nLogToFile=true\x00', b'\xffbad encoding', b'[PSWallpaper]\nWallpaperFit=cover\n' + b'X' * MAX_BYTES,
    b'[PSWallpaper]\nUnknown=text\n    WallpaperFit=fit'], ids=['no-header', 'duplicate-key', 'invalid-bool',
    'invalid-brightness', 'quoted-choice', 'continued-field', 'no-supported-field', 'inherited-default',
    'nul', 'bad-encoding', 'oversize', 'assignment-in-continuation'])
def test_case035_unreliable_syntax_encoding_and_inherited_values_are_readonly(raw):
    with pytest.raises(ConfigRejected, match='CONFIG_UNSUPPORTED'):
        parse(raw)


def test_case035_missing_supported_fields_are_not_synthesized():
    document = parse(b'[PSWallpaper]\nWallpaperFit=cover\nOther=keep\n')
    assert [field['key'] for field in document.fields()] == ['WallpaperFit']
    with pytest.raises(ConfigRejected, match='CONFIG_VALUE_INVALID'):
        document.patch({'LogToFile': False})
