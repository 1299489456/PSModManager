from unittest.mock import Mock

from backend.app.window_frame import WindowFrame


def frame(style):
    value = WindowFrame.__new__(WindowFrame)
    value._hwnd = Mock(return_value=123)
    value._user32 = Mock()
    value._user32.GetWindowLongW.return_value = style
    value.apply_style = Mock()
    return value


def test_frameless_window_restores_taskbar_minimize_without_native_caption():
    value = frame(0x16010000)
    value.initialize()
    value._user32.SetWindowLongW.assert_called_once_with(123, -16, 0x160B0000)
    value._user32.SetWindowPos.assert_called_once_with(123, None, 0, 0, 0, 0, 0x37)
    value.apply_style.assert_called_once_with()
    assert not (0x160B0000 & 0x00C00000)  # WS_CAPTION stays absent.


def test_existing_minimize_capability_does_not_reframe_or_move_window():
    value = frame(0x160B0000)
    value.initialize()
    value._user32.SetWindowLongW.assert_not_called()
    value._user32.SetWindowPos.assert_not_called()
    value.apply_style.assert_called_once_with()


def test_shell_style_addition_keeps_existing_window_bits():
    original = 0x17050000
    value = frame(original)
    value.initialize()
    updated = value._user32.SetWindowLongW.call_args.args[2]
    assert updated & original == original
    assert updated & 0xA0000 == 0xA0000
