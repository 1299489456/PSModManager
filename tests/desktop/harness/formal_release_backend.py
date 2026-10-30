"""Real desktop services over an isolated copy of the user's native packages.

Development acceptance only: never imported by the application. The game EXE
is a self-authored bounded process probe; no third-party Mod code is executed.
The loopback transport exists only in this harness, not in the shipped desktop.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
import sys
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
GAME = Path(r'E:\Entertainment\Steam\steamapps\common\Probably Stolen')
WORKSHOP = Path(r'E:\Entertainment\Steam\steamapps\workshop\content\4348910')
USER = Path.home() / 'AppData/LocalLow/Questing Goose Studio/Probably Stolen'


def prepare(out):
    from backend.app.composition import DesktopContext
    from backend.app.paths import resolve_paths
    from backend.app import native_api
    from backend.app.api import DesktopApi
    from tests.integration.harness.launch_probe import build_probe
    from tests.integration.test_deployment_guards import register

    out = out.resolve()
    assert out.is_relative_to(ROOT / '.preview') and not out.exists(), 'Use a fresh .preview directory'
    out.mkdir(parents=True)
    game = out / 'owned/steamapps/common/Probably Stolen'
    managed = game / 'Probably Stolen_Data/Managed'
    managed.mkdir(parents=True)
    (game / 'MonoBleedingEdge').mkdir()
    shutil.copyfile(GAME / 'Probably Stolen_Data/Managed/Assembly-CSharp.dll', managed / 'Assembly-CSharp.dll')
    shutil.copyfile(build_probe(out / 'probe'), game / 'Probably Stolen.exe')
    (game.parents[1] / 'appmanifest_4348910.acf').write_text(
        '"AppState" { "appid" "4348910" "installdir" "Probably Stolen" "buildid" "26284217" }')
    workshop = game.parents[1] / 'workshop/content/4348910'
    shutil.copytree(WORKSHOP, workshop)
    user = out / 'owned/user'
    user.mkdir()
    for name in ('SaveFile.es3', 'Player.log', 'Player-prev.log'):
        if (USER / name).is_file():
            shutil.copyfile(USER / name, user / name)
    if (USER / 'ModConfigs').is_dir():
        shutil.copytree(USER / 'ModConfigs', user / 'ModConfigs')

    context = DesktopContext(resolve_paths(str(out / 'manager')))
    context.initialize()
    identity = register(context, game)
    original = native_api.installation
    def isolated(ctx, ident):
        value = original(ctx, ident)
        assert Path(value['canonical_root']).resolve() == game.resolve()
        return {**value, 'persistent_root': str(user), 'workshop_root': str(workshop)}
    native_api.installation = isolated
    snapshot = context.native_mods.snapshot
    context.native_mods.snapshot = lambda value: snapshot(isolated(context, value['installation_id']))
    context.logs.user_data_resolver = lambda _: user
    context.ai.tools.installation_resolver = lambda ident: isolated(context, ident)
    context.storage.accept()
    # Reuse the real registered selection, without discovering the user's game.
    context.installations.auto_discover = lambda jobs, req: context.installations.use(jobs, req, identity)
    context.window_frame = SimpleNamespace(control=lambda op: dict(maximized=False, minimized=False))
    context.workshop.opener = lambda url: True  # official handoff recorded, not executed
    context.api = DesktopApi(context)
    return context, dict(installation_id=identity, game=str(game), user=str(user), workshop=str(workshop))


def serve(context, out, fixture):
    dist = ROOT / 'frontend/dist'
    nonce = uuid4().hex
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, code, body, content_type):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split('?', 1)[0]
            if path in ('/', '/index.html'):
                html = (dist / 'index.html').read_text(encoding='utf-8')
                html = re.sub(r'<meta[^>]+http-equiv="Content-Security-Policy"[^>]*>', '', html)
                script = '<script>window.pywebview={api:{dispatch:request=>fetch("/rpc",{method:"POST",headers:{"Content-Type":"application/json","X-Test-Token":"' + nonce + '"},body:JSON.stringify(request)}).then(r=>r.json())}};</script>'
                return self.send(200, html.replace('<head>', '<head>' + script).encode(), 'text/html; charset=utf-8')
            asset = (dist / path.lstrip('/')).resolve()
            if not asset.is_relative_to(dist.resolve()) or not asset.is_file():
                return self.send(404, b'Not found', 'text/plain')
            mime = {'.js': 'application/javascript', '.css': 'text/css', '.png': 'image/png', '.svg': 'image/svg+xml'}.get(asset.suffix, 'application/octet-stream')
            self.send(200, asset.read_bytes(), mime)

        def do_POST(self):
            if self.path != '/rpc' or self.headers.get('X-Test-Token') != nonce:
                return self.send(403, b'Forbidden', 'text/plain')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1048576:
                return self.send(413, b'Too large', 'text/plain')
            request = json.loads(self.rfile.read(length))
            # No host side effects from this test-only transport.
            if request.get('command') in ('native.open', 'app.open_link', 'app.storage.restart'):
                response = dict(request_id=request.get('request_id'), ok=True, data=dict(opened=False))
            else:
                response = context.api.dispatch(request)
            self.send(200, json.dumps(response, ensure_ascii=False).encode(), 'application/json; charset=utf-8')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    url = 'http://127.0.0.1:' + str(server.server_port)
    (out / 'fixture.json').write_text(json.dumps({**fixture, 'url': url}, indent=2), encoding='utf-8')
    print(url, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        context.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence-root', type=Path, required=True)
    args = parser.parse_args()
    context, fixture = prepare(args.evidence_root)
    serve(context, args.evidence_root, fixture)
