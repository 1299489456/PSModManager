"""Native file picker without the Vista shell dialog's in-process extensions."""
import os


def choose_file(window, kind):
    from System import Func, Object
    from System.Windows.Forms import DialogResult, OpenFileDialog

    def choose():
        dialog = OpenFileDialog()
        try:
            # A host TortoiseSVN shell extension crashes IFileDialog.Show here.
            # Keep this setting local to our picker; do not alter Windows/COM.
            dialog.AutoUpgradeEnabled = False
            dialog.Filter = {'dll': 'DLL 文件 (*.dll)|*.dll', 'json': '模组方案 (*.json)|*.json',
                             'zip': 'ZIP 压缩包 (*.zip)|*.zip'}[kind]
            dialog.CheckFileExists = True
            dialog.CheckPathExists = True
            dialog.Multiselect = False
            dialog.RestoreDirectory = True
            dialog.InitialDirectory = os.environ.get('HOMEPATH', '')
            return str(dialog.FileName) if dialog.ShowDialog(window.native) == DialogResult.OK else None
        finally:
            dialog.Dispose()

    return window.native.Invoke(Func[Object](choose))


def save_profile(window):
    from System import Func, Object
    from System.Windows.Forms import DialogResult, SaveFileDialog

    def choose():
        dialog = SaveFileDialog()
        try:
            dialog.AutoUpgradeEnabled = False
            dialog.Filter = '模组方案 (*.json)|*.json'
            dialog.FileName = 'PSMM-Mod方案.json'
            dialog.DefaultExt = 'json'
            dialog.AddExtension = True
            dialog.CheckPathExists = True
            dialog.OverwritePrompt = True
            dialog.RestoreDirectory = True
            return str(dialog.FileName) if dialog.ShowDialog(window.native) == DialogResult.OK else None
        finally:
            dialog.Dispose()
    return window.native.Invoke(Func[Object](choose))
