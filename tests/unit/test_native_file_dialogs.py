"""JSON profile pickers use the same shell-extension-safe Windows entry point."""
import sys
from types import SimpleNamespace

from backend.app.file_dialogs import choose_file, save_profile


class Delegate:
    def __class_getitem__(cls, item):
        return lambda function: function


def install(monkeypatch, accepted):
    created = []
    class Dialog:
        def __init__(self):
            self.disposed = False
            created.append(self)
        def ShowDialog(self, window):
            self.FileName = r'C:\selected\profile.json'
            return accepted
        def Dispose(self):
            self.disposed = True
    monkeypatch.setitem(sys.modules, 'System', SimpleNamespace(Func=Delegate, Object=object))
    monkeypatch.setitem(sys.modules, 'System.Windows.Forms', SimpleNamespace(
        DialogResult=SimpleNamespace(OK=True), OpenFileDialog=Dialog, SaveFileDialog=Dialog))
    window = SimpleNamespace(native=SimpleNamespace(Invoke=lambda function: function()))
    return window, created


def test_json_open_keeps_native_safe_picker(monkeypatch):
    window, dialogs = install(monkeypatch, True)
    assert choose_file(window, 'json') == r'C:\selected\profile.json'
    assert dialogs[0].Filter == '模组方案 (*.json)|*.json'
    assert dialogs[0].AutoUpgradeEnabled is False and dialogs[0].disposed


def test_json_export_and_cancel(monkeypatch):
    window, dialogs = install(monkeypatch, False)
    assert save_profile(window) is None
    assert dialogs[0].AutoUpgradeEnabled is False and dialogs[0].OverwritePrompt
    assert dialogs[0].DefaultExt == 'json' and dialogs[0].disposed
