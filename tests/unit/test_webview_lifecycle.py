import subprocess
import threading
from types import SimpleNamespace

from backend.app.webview_lifecycle import guard_dispatch_return, install_callback_lifecycle_guard


class ClosingEvent:
    def __init__(self):
        self.callbacks = []

    def __iadd__(self, callback):
        self.callbacks.append(callback)
        return self


def test_dispatch_guard_retains_payload_and_only_wraps_library_return():
    expression = 'window.pywebview._returnValuesCallbacks["dispatch"]["12345"]({value: "a) [中文]"})'
    result = guard_dispatch_return(expression)
    assert result.endswith('({value: "a) [中文]"})')
    for other in ('document.body.innerText', 'throw new Error("visible")',
                  'window.pywebview._returnValuesCallbacks["other"]["123"]({})'):
        assert guard_dispatch_return(other) == other


def test_actual_javascript_ignores_gone_document_but_propagates_live_callback_error():
    expression = guard_dispatch_return('window.pywebview._returnValuesCallbacks["dispatch"]["123"]({value: "ok"})')
    script = 'const assert=require("node:assert/strict");global.window={};' + expression + ';'
    script += 'window.pywebview={_returnValuesCallbacks:{dispatch:{}}};' + expression + ';'
    script += 'let seen;window.pywebview._returnValuesCallbacks.dispatch["123"]=v=>{seen=v.value};' + expression + ';assert.equal(seen,"ok");'
    script += 'window.pywebview._returnValuesCallbacks.dispatch["123"]=()=>{throw new Error("live")};assert.throws(()=>{' + expression + '},/live/);'
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_guard_accepts_actual_pywebview_random_id_edge_cases():
    import json
    generated = subprocess.run(['node', '-e',
        'console.log(JSON.stringify([0,1e-7,1.2e-8,.12345].map(v=>(v+"").substring(2))))'],
        capture_output=True, text=True, timeout=10)
    assert generated.returncode == 0, generated.stderr
    for callback_id in json.loads(generated.stdout):
        expression = 'window.pywebview._returnValuesCallbacks["dispatch"][' + json.dumps(callback_id) + ']({})'
        assert guard_dispatch_return(expression).startswith('window.pywebview?._returnValuesCallbacks?.')


def test_guard_is_window_local_preserves_call_arguments_and_errors():
    class Window:
        def __init__(self):
            self.calls=[]
            self.events=SimpleNamespace(closing=ClosingEvent())
        def evaluate_js(self, script, *args, **kwargs):
            self.calls.append((script,args,kwargs))
            if script=='throw': raise RuntimeError('not swallowed')
            return 'result'
    first, second = Window(), Window()
    install_callback_lifecycle_guard(first)
    assert first.evaluate_js('ordinary', False, marker=1)=='result'
    assert first.calls==[('ordinary',(False,),{'marker':1})]
    import pytest
    with pytest.raises(RuntimeError):first.evaluate_js('throw')
    second.evaluate_js('window.pywebview._returnValuesCallbacks["dispatch"]["123"]({})')
    assert '?._return' not in second.calls[0][0]


def test_closing_defers_destruction_until_native_evaluation_returns():
    entered, release, destroyed = threading.Event(), threading.Event(), threading.Event()
    notices = []
    class Window:
        def __init__(self):
            self.events=SimpleNamespace(closing=ClosingEvent())
            self.calls=[]

        def evaluate_js(self, script):
            self.calls.append(script)
            entered.set()
            assert release.wait(2), 'UI continuation was not allowed to finish'
            return 'delivered'

        def destroy(self):
            assert all(callback() is True for callback in self.events.closing.callbacks)
            destroyed.set()

    window = Window()
    lifecycle = install_callback_lifecycle_guard(window, lambda: notices.append('api closed'))
    returned = []
    active = threading.Thread(target=lambda: returned.append(window.evaluate_js('document.body.innerText')))
    active.start()
    assert entered.wait(1)
    assert lifecycle.closing() is False
    assert lifecycle.closing() is False
    assert not destroyed.is_set()
    assert window.evaluate_js('window.pywebview._returnValuesCallbacks["dispatch"]["123"]({})') is None
    assert len(window.calls) == 1
    release.set()
    active.join(2)
    lifecycle.join()
    assert destroyed.is_set() and returned == ['delivered']
    assert notices == ['api closed']
    assert lifecycle.stats == {'closing':True, 'active':0, 'blocked':1, 'drained':1}


def test_closed_window_drops_late_bridge_delivery_but_rejects_other_scripts():
    import pytest
    class Window:
        def __init__(self):
            self.events=SimpleNamespace(closing=ClosingEvent())
        def evaluate_js(self, script):
            raise AssertionError('must not enter native UI after closing')
    window = Window()
    lifecycle = install_callback_lifecycle_guard(window)
    assert lifecycle.closing() is True
    assert window.evaluate_js('window.pywebview._returnValuesCallbacks["dispatch"]["123"]({})') is None
    with pytest.raises(RuntimeError, match='Window is closing'):
        window.evaluate_js('document.body.innerText')
    lifecycle.join()
    assert lifecycle.stats['active'] == 0


def test_native_close_failure_is_not_reported_as_success():
    import pytest
    entered, release = threading.Event(), threading.Event()
    class Window:
        def __init__(self):
            self.events=SimpleNamespace(closing=ClosingEvent())
        def evaluate_js(self, script):
            entered.set()
            assert release.wait(2)
        def destroy(self):
            raise RuntimeError('owned native close failure')
    window = Window()
    lifecycle = install_callback_lifecycle_guard(window)
    active = threading.Thread(target=lambda: window.evaluate_js('1'))
    active.start()
    assert entered.wait(1)
    assert lifecycle.closing() is False
    release.set()
    active.join(2)
    with pytest.raises(RuntimeError, match='Window close callback failed'):
        lifecycle.join()


def test_closing_does_not_wait_for_api_lock_on_ui_thread():
    from backend.app.api import DesktopApi, Empty
    entered, finish_handler, ui_returned, destroyed = (threading.Event() for _ in range(4))
    def handler(request_id, payload):
        entered.set()
        assert finish_handler.wait(3), 'UI pump did not resume'
        return {}
    api = DesktopApi(SimpleNamespace(jobs=None), test_commands={'test.wait_for_ui': (Empty, handler)})
    class Window:
        def __init__(self):
            self.events = SimpleNamespace(closing=ClosingEvent())
        def evaluate_js(self, script):
            return None
        def destroy(self):
            assert all(callback() is True for callback in self.events.closing.callbacks)
            assert api._closed
            destroyed.set()
    window = Window()
    lifecycle = install_callback_lifecycle_guard(window, api._close)
    responses, closing_results = [], []
    dispatch = threading.Thread(target=lambda: responses.append(api.dispatch({
        'schema_version':1, 'request_id':'wait', 'command':'test.wait_for_ui', 'payload':{}})))
    def closing_on_ui():
        closing_results.append(lifecycle.closing())
        ui_returned.set()
    ui = threading.Thread(target=closing_on_ui)
    dispatch.start()
    assert entered.wait(1)
    ui.start()
    try:
        assert ui_returned.wait(.5), 'FormClosing blocked on the API lock'
        assert closing_results == [False]
        assert not destroyed.is_set()
    finally:
        finish_handler.set()
        dispatch.join(3)
        ui.join(3)
        lifecycle.join()
    assert responses[0]['ok'] and destroyed.is_set()
    assert lifecycle.stats['active'] == 0


def test_api_close_exception_still_drains_and_is_not_silenced():
    import pytest
    entered, release, destroyed = threading.Event(), threading.Event(), threading.Event()
    class Window:
        def __init__(self):
            self.events = SimpleNamespace(closing=ClosingEvent())
        def evaluate_js(self, script):
            entered.set()
            assert release.wait(2)
        def destroy(self):
            assert all(callback() is True for callback in self.events.closing.callbacks)
            destroyed.set()
    def fail_close():
        raise RuntimeError('owned API close failure')
    window = Window()
    lifecycle = install_callback_lifecycle_guard(window, fail_close)
    active = threading.Thread(target=lambda: window.evaluate_js('1'))
    active.start()
    assert entered.wait(1)
    try:
        assert lifecycle.closing() is False
        assert not destroyed.is_set()
    finally:
        release.set()
        active.join(2)
    with pytest.raises(RuntimeError, match='Window close callback failed'):
        lifecycle.join()
    assert destroyed.is_set()
