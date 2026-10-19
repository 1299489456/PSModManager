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
            dialog.Filter = 'DLL 文件 (*.dll)|*.dll' if kind == 'dll' else 'ZIP 压缩包 (*.zip)|*.zip'
            dialog.CheckFileExists = True
            dialog.CheckPathExists = True
            dialog.Multiselect = False
            dialog.RestoreDirectory = True
            dialog.InitialDirectory = os.environ.get('HOMEPATH', '')
            return str(dialog.FileName) if dialog.ShowDialog(window.native) == DialogResult.OK else None
        finally:
            dialog.Dispose()

    return window.native.Invoke(Func[Object](choose))
