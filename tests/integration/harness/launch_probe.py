"""Self-authored bounded process; never a real game or game DLL."""
import os
from pathlib import Path
import shutil
import subprocess

from backend.games.probably_stolen.layout import EXE
from tests.integration.test_deployment_guards import register
from tests.unit.test_installation_layout import make_installation

SOURCE = r'''
using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
public static class LaunchProbe {
    public static int Main(string[] args) {
        File.WriteAllLines("launch-probe.txt", new string[] {
            Process.GetCurrentProcess().Id.ToString(),
            Process.GetCurrentProcess().MainModule.FileName,
            Environment.CurrentDirectory, args.Length.ToString()
        });
        File.AppendAllText("launch-count.txt", "started\n");
        for (int i=0; i<2250; i++) {
            if (File.Exists("exit-request.txt")) {
                int code;
                try { if (int.TryParse(File.ReadAllText("exit-request.txt"), out code)) return code; }
                catch (IOException) { } // A test writer may still be closing the control file.
            }
            Thread.Sleep(20);
        }
        return 124;
    }
}
'''


def build_probe(directory):
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / 'LaunchProbe.cs'
    source.write_text(SOURCE, encoding='utf-8')
    executable = directory / EXE
    compiler = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    assert compiler.is_file(), 'Existing Windows Framework compiler required; do not install automatically'
    result = subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:x64',
                             '/out:' + str(executable), str(source)], capture_output=True, timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stdout.decode(errors='replace')
    return executable


def setup_installation(context, game, probe):
    make_installation(game)
    shutil.copyfile(probe, game / EXE)
    (game / 'synthetic-save.dat').write_bytes(b'untouched synthetic save')
    return register(context, game)
