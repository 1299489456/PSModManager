"""Runner isolation is opt-in; direct early-card entry points remain usable."""
import os
import json
from pathlib import Path

import pytest


def pytest_collection_finish(session):
    value = os.environ.get('PSMM_VERIFY_COLLECTION_FILE')
    if value:
        path = Path(value)
        root = Path(os.environ['PSMM_VERIFY_RUN_ROOT']).resolve(strict=True)
        assert path.resolve().is_relative_to(root)
        path.write_text(json.dumps({'schema_version': 1, 'nodeids': [item.nodeid for item in session.items]}), encoding='utf-8')


@pytest.fixture(scope='session', autouse=True)
def verify_runner_isolation():
    value = os.environ.get('PSMM_VERIFY_RUN_ROOT')
    if value:
        root = Path(value).resolve(strict=True)
        project = Path(__file__).resolve().parents[1]
        assert root.is_relative_to(project / 'docs/development/evidence')
        for name in ('TEMP', 'TMP', 'APPDATA', 'LOCALAPPDATA', 'USERPROFILE'):
            target = Path(os.environ[name]).resolve(strict=True)
            assert target.is_relative_to(root), f'{name} escaped isolation'
        profile = os.environ['PSMM_VERIFY_PROFILE']
        assert profile in ('synthetic', 'desktop-local', 'real-readonly', 'ai-live')
        if profile == 'real-readonly':
            from scripts.verification.readonly import load_authorization
            load_authorization()  # Preserve explicit resource, expiry and read-only checks.
