"""Deliver pywebview dispatch results only while their JS callback still exists.

pywebview's return script can outlive a document reload. Scope the compatibility
guard to its dispatch-return expression on this window; do not suppress errors
in live callbacks or other JavaScript, and do not modify the installed package.
"""
import re
import threading

_DISPATCH_RETURN = re.compile(r'^window\.pywebview\._returnValuesCallbacks\["dispatch"\]\["([0-9e-]*)"\]\(')


def guard_dispatch_return(script):
    match = _DISPATCH_RETURN.match(script)
    if not match:
        return script
    return ('window.pywebview?._returnValuesCallbacks?.["dispatch"]?.["'
            + match[1] + '"]?.(' + script[match.end():])


class CallbackLifecycle:
    """Keep native script continuations inside the lifetime of the UI loop."""

    def __init__(self, window, on_closing=None):
        self._window = window
        self._evaluate = window.evaluate_js
        self._on_closing = on_closing
        self._condition = threading.Condition()
        self._closing = False
        self._active = 0
        self._blocked = 0
        self._drained = 0
        self._closer = None
        self._close_error = None
        self._ready_to_close = False

    @property
    def stats(self):
        with self._condition:
            return dict(closing=self._closing, active=self._active,
                        blocked=self._blocked, drained=self._drained)

    def evaluate(self, script, *args, **kwargs):
        with self._condition:
            if self._closing:
                if _DISPATCH_RETURN.match(script):
                    self._blocked += 1
                    return None
                raise RuntimeError('Window is closing; JavaScript execution is unavailable')
            self._active += 1
        try:
            return self._evaluate(guard_dispatch_return(script), *args, **kwargs)
        finally:
            with self._condition:
                self._active -= 1
                if self._closing:
                    self._drained += 1
                self._condition.notify_all()

    def closing(self):
        with self._condition:
            if self._ready_to_close:
                return True
            self._closing = True
            if not self._active and self._on_closing is None:
                self._ready_to_close = True
                return True
            if self._closer is None:
                self._closer = threading.Thread(target=self._drain_then_close,
                                                name='PSModManager-window-close')
                self._closer.start()
        # FormClosing runs on the UI thread. Let the pump run outstanding
        # ExecuteScriptAsync continuations before asking it to close again.
        return False

    def _drain_then_close(self):
        # API dispatch may hold its lock while a native file dialog awaits the
        # UI pump. Never acquire that lock in the FormClosing event thread.
        try:
            if self._on_closing:
                self._on_closing()
        except Exception as exc:
            self._close_error = exc
        with self._condition:
            self._condition.wait_for(lambda: self._active == 0)
            self._ready_to_close = True
        try:
            self._window.destroy()
        except Exception as exc:
            self._close_error = exc

    def join(self):
        """Call after the native UI loop exits, before Python.NET finalizes."""
        if self._closer is not None:
            self._closer.join(timeout=5)
            if self._closer.is_alive():
                raise RuntimeError('Window close callback did not finish')
        if self._close_error is not None:
            raise RuntimeError('Window close callback failed') from self._close_error
        if self.stats['active']:
            raise RuntimeError('Window exited with pending JavaScript callbacks')


def install_callback_lifecycle_guard(window, on_closing=None):
    lifecycle = CallbackLifecycle(window, on_closing)
    window.evaluate_js = lifecycle.evaluate
    window.events.closing += lifecycle.closing
    window._psmm_callback_lifecycle = lifecycle
    return lifecycle
