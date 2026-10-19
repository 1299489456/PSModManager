"""附加 Windows WER 复现，仅在明确诊断执行时显式指定本文件。

已实证 FAILED：LPAC 下 Environment.FailFast 可令 csrss.exe 显示
dotnet.exe / Unknown Hard Error，即使 ErrorMode=0x8003 和 WER_NO_UI=0x20。
运行会干扰用户桌面；不属于日常 runner，也不代表正常产品验收 PASS。
下面保留原来的父进程超时与清理断言；它们通过不代表无系统弹框。

历史证据：docs/development/evidence/PS-01-07/
20260929T010932Z-ui-feedback/dotnet/wer-once.json。
文件故意不使用 test_ 前缀，避免自动收集触发实际 WER 故障。
即使显式指定文件，也必须设置 PSMM_DIAGNOSTIC_ALLOW_SYSTEM_DIALOGS=1；
否则明确 SKIP / NOT_RUN，禁止将未执行诊断记为通过。
"""
import os

import pytest

from tests.integration.test_metadata_sandbox import probe, invoke

pytestmark = [
    pytest.mark.skipif(os.name != 'nt', reason='Windows WER diagnostic only'),
    pytest.mark.skipif(os.environ.get('PSMM_DIAGNOSTIC_ALLOW_SYSTEM_DIALOGS') != '1',
        reason='NOT_RUN: explicit PSMM_DIAGNOSTIC_ALLOW_SYSTEM_DIALOGS=1 required; may show system dialogs'),
]


def test_failfast_reporting_cannot_hang_parent(probe):
    # WER may be inaccessible under LPAC; its failure/reporting wait must still
    # be bounded. This is not presented as a successful natural crash exit.
    result = invoke(probe, 'failfast', timeout=2)
    assert result['status'] in ('exited', 'timeout') and result['exit_code'] != 0, result
    assert result['duration_seconds'] < 8, result
