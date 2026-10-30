"""Windows File objects become picker-equivalent, preview-only import grants.

No page API accepts a path. WebView2 supplies CoreWebView2File objects for
files selected by the OS; script-created Files have no usable native path.
See Microsoft's WebView2 WebView.postMessageWithAdditionalObjects reference.
"""
from itertools import islice
import json
from pathlib import Path
import zipfile

from backend.app.paths import REFERENCE_ROOT, overlaps, reject_reparse
from backend.catalog import archive_policy as policy

EVENT = 'psmm-native-import'
MAX_SOURCES = 8

# FilesDropped is also understood (and otherwise ignored) by pywebview's own
# WebMessage handler. A separate JSON command would make that handler treat a
# drop as an attempted RPC. Only the native AdditionalObjects carry authority.
_BIND_SCRIPT = r"""(() => {
  if (window.__psmmNativeDropBound) return;
  window.__psmmNativeDropBound = true;
  const host = window.chrome.webview;
  host.addEventListener('message', event => {
    if (event.data?.type === 'psmm-native-import') {
      window.dispatchEvent(new CustomEvent('psmm-native-import', {detail: event.data.detail}));
    }
  });
  const fileDrag = event => Array.from(event.dataTransfer?.types || []).includes('Files');
  document.addEventListener('dragover', event => {
    if (!fileDrag(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
  });
  document.addEventListener('drop', event => {
    if (!fileDrag(event)) return;
    event.preventDefault();
    if (!event.isTrusted) return;
    host.postMessageWithAdditionalObjects('FilesDropped', event.dataTransfer.files);
  });
})()"""


def _is_modpack(path, paths):
    """Inspect the bounded ZIP directory, never read or extract member bytes."""
    if not path.is_absolute() or str(path).startswith(('\\\\', '//')):
        raise policy.ImportRejected('IMPORT_PATH_INVALID')
    policy.canonical_name('/'.join(path.parts[1:]))
    try:
        reject_reparse(path)
    except ValueError:
        raise policy.ImportRejected('IMPORT_LINK_REFUSED') from None
    if overlaps(path, paths.root):
        raise policy.ImportRejected('IMPORT_SOURCE_PROTECTED_LIBRARY')
    if overlaps(path, REFERENCE_ROOT):
        raise policy.ImportRejected('IMPORT_SOURCE_PROTECTED_REFERENCE')
    from backend.profiles.sharing import MAX_BYTES, MAX_FILES, MAX_MANIFEST
    with policy.locked_path(path), path.open('rb') as stream:
        archive, files = policy.zip_entries(stream, max_archive_bytes=MAX_BYTES + MAX_MANIFEST + 2*1024**2,
            max_total_bytes=MAX_BYTES + MAX_MANIFEST, max_file_bytes=512*1024**2, max_files=MAX_FILES + 1)
        try:
            return any(name == 'modpack.json' for _, name in files)
        finally:
            archive.close()


def _grant_path(context, selected):
    path = Path(selected)
    if not selected or len(selected) > 32767 or not path.is_absolute() or str(path).startswith(('\\\\', '//')):
        raise policy.ImportRejected('IMPORT_PATH_INVALID')
    if path.is_dir():
        kind = 'directory'
    elif path.suffix.lower() == '.zip':
        kind = 'zip'
    else:
        raise policy.ImportRejected('IMPORT_FORMAT_UNSUPPORTED')
    grant = context.native_commands.grant(selected, kind)
    return dict(grant, kind=kind)


def _grant_objects(context, objects, file_type):
    """Native handler core; bounded independently from the page's drop script."""
    objects = list(islice(objects, MAX_SOURCES + 1))
    if len(objects) > MAX_SOURCES:
        return dict(sources=[], errors=[dict(code='IMPORT_CAPACITY', display_name='')])
    sources, errors, seen = [], [], set()
    for file in objects:
        if not isinstance(file, file_type):
            errors.append(dict(code='IMPORT_DROP_INVALID', display_name=''))
            continue
        selected = str(file.Path or '')
        # A File constructed in JS is not authority to read its supplied name.
        if not selected:
            errors.append(dict(code='IMPORT_DROP_INVALID', display_name=''))
            continue
        key = selected.casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            sources.append(_grant_path(context, selected))
        except (ValueError, OSError, zipfile.BadZipFile) as exc:
            code = 'IMPORT_ARCHIVE_INVALID' if isinstance(exc, zipfile.BadZipFile) else getattr(exc, 'code', 'IMPORT_PATH_INVALID')
            errors.append(dict(code=code, display_name=Path(selected).name))
    return dict(sources=sources, errors=errors)


def install_native_drop(window, context):
    """Call once after create_window, before webview.start (Windows/WebView2).

    The page receives ``psmm-native-import`` with ``detail.sources`` containing
    picker-equivalent {source_handle, display_name, kind} records and
    ``detail.errors`` containing {code, display_name}. Kinds are directory or
    zip. The consumer must preview and confirm each source; this
    adapter never publishes a package, changes a profile, or deploys anything.
    """
    if getattr(window, '_psmm_native_drop_installed', False):
        return
    window._psmm_native_drop_installed = True

    def attach():
        from Microsoft.Web.WebView2.Core import CoreWebView2File
        native = window.native.browser.webview

        def dropped(sender, event):
            # NavigateToString's trusted top-level document has about:blank
            # origin. Untrusted frames/documents must not supply source grants.
            if str(event.Source) != 'about:blank' or event.WebMessageAsJson != '"FilesDropped"':
                return
            objects = event.AdditionalObjects
            if objects is None:
                return
            result = _grant_objects(context, objects, CoreWebView2File)
            if result['sources'] or result['errors']:
                native.CoreWebView2.PostWebMessageAsJson(json.dumps(dict(type=EVENT, detail=result), ensure_ascii=False))

        native.WebMessageReceived += dropped
        window._psmm_native_drop_handler = dropped

    def loaded():
        window.evaluate_js(_BIND_SCRIPT)

    window.events.before_show += attach
    window.events.loaded += loaded
    window._psmm_native_drop_callbacks = (attach, loaded)
