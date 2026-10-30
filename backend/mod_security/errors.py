from backend.runtime.events import RuntimePolicyError

MESSAGES = {
    'SECURITY_RECORD_INVALID': '检查或处置记录损坏或无法读取，请保留记录并检查管理器数据目录。',
    'SECURITY_SCAN_REQUIRED': '尚未检查当前内容，可在模组检查中手动分析。',
    'SECURITY_CONTENT_CHANGED': '模组内容已变化，旧报告不再适用；请重新检查。',
    'SECURITY_HIGH_RISK': '检查发现高风险告警，请复核证据；需要时可主动停用或隔离。',
    'SECURITY_QUARANTINED': '启用模组处于逻辑隔离或未完成处置状态，请先处理安全记录。',
    'SECURITY_INCOMPLETE': '检查未完成，请查看覆盖缺口；普通模组管理不依赖检查结果。',
    'SECURITY_BUSY': '本安装正在进行安全检查或处置，请等待完成。',
    'SECURITY_CLOSED': '本地防护服务已关闭。',
    'SECURITY_INVALID_REQUEST': '安全检查参数无效。',
    'SECURITY_MOD_NOT_FOUND': '未找到当前来源的模组，请刷新列表。',
    'SECURITY_PREVIEW_EXPIRED': '处置预览已过期，请重新预览并确认。',
    'SECURITY_PROCESS_UNSAFE': '游戏正在运行或无法确认已退出，暂不执行安全处置。',
    'SECURITY_IO': '安全报告未能保存或读取，请检查管理器数据目录。',
    'SECURITY_GAME_SCAN_INCOMPLETE': '历史游戏原生检查未完成，请结合报告时间查看覆盖缺口。',
    'SECURITY_GAME_SCAN_REFUSED': '历史游戏原生检查报告拒绝，请查看具体证据及报告适用范围。',
    'SECURITY_UNDECLARED_CAPABILITIES': '报告检测到清单未声明的权限，请复核详情。',
}


class SecurityError(RuntimePolicyError):
    def __init__(self, code, detail=None):
        super().__init__(code)
        self.detail = detail or {}
