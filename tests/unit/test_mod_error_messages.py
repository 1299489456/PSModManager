"""Ordinary file failures retain actionable desktop feedback."""
from types import SimpleNamespace

import pytest

from backend.app.api import DesktopApi


@pytest.mark.parametrize(('result', 'expected'), [
    ({'error_code': 'EXEC_IO_FAILED', 'io_error': {'kind': 'file_busy'}}, '文件被占用'),
    ({'error_code': 'EXEC_IO_FAILED', 'io_error': {'kind': 'permission_denied'}}, '检查目录权限'),
    ({'error_code': 'EXEC_IO_FAILED', 'io_error': {'kind': 'disk_full'}}, '磁盘空间不足'),
    ({'error_code': 'PLAN_MANAGED_DRIFT'}, '已被外部修改，已保留'),
    ({'error_code': 'PLAN_UNMANAGED_CONFLICT'}, '目标已有其他文件，已保留'),
    ({'error_code': 'EXEC_EXTERNAL_CONFLICT', 'recovery_required': True}, '请关闭游戏后重新打开管理器'),
])
def test_failure_result_has_actionable_message(result, expected):
    host = SimpleNamespace(_jobs=lambda: SimpleNamespace(start=lambda request_id, work: work(None)))
    response = DesktopApi._mod_job(host, 'owned-test', lambda task: dict(result))
    assert expected in response['message']
    assert response['error_code'] == result['error_code']


def test_success_response_is_not_rewritten():
    result = {'completed': True, 'state': 'enabled'}
    host = SimpleNamespace(_jobs=lambda: SimpleNamespace(start=lambda request_id, work: work(None)))
    assert DesktopApi._mod_job(host, 'owned-test', lambda task: result) is result
