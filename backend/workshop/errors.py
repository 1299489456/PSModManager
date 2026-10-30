MESSAGES = {
    'WORKSHOP_AUTO_UPDATE_DISABLED': '管理器自动更新已关闭，该条目尚未提交；已提交的 Steam 任务继续核对结果。',
    'WORKSHOP_SESSION_CONNECTING': '正在后台读取 Steam 状态，本机清单与公开详情可继续使用。',
    'WORKSHOP_INSTALLATION_BUSY': '安装目录正由其他操作占用，请稍后重试。',
    'WORKSHOP_RECOVERY_REQUIRED': '安装恢复检查未通过，请查看恢复状态或诊断后重试。',
    'WORKSHOP_INSTALLATION_CHANGED': '游戏安装目录或文件标识已变化，请重新扫描并确认安装目录后重试。',
    'WORKSHOP_INSTALLATION_UNAVAILABLE': '无法安全访问游戏安装目录，请检查目录与权限后重新扫描。',
    'WORKSHOP_GUARD_UNAVAILABLE': '无法确认安装保护状态，已暂停 Steam 操作；请刷新或查看诊断。',
    'WORKSHOP_SESSION_UNSUPPORTED': '直接管理工坊需要 64 位 Windows 与已安装的正式版游戏。',
    'WORKSHOP_STEAM_DLL_MISSING': '未找到正式版游戏自带的 Steamworks DLL，请在 Steam 验证游戏文件。',
    'WORKSHOP_STEAM_DLL_UNTRUSTED': 'Steamworks DLL 的 Valve 签名无法验证，已禁止加载，请在 Steam 验证游戏文件。',
    'WORKSHOP_STEAM_INTERFACE_UNSUPPORTED': '游戏 Steamworks 接口版本不受支持，请更新游戏或管理器。',
    'WORKSHOP_STEAM_CLIENT_MISSING': 'Steam 客户端未运行，请先打开并登录 Steam。',
    'WORKSHOP_STEAM_INIT_FAILED': '无法建立 Steamworks 会话，请确认 Steam 与管理器权限级别一致且当前账号拥有游戏。',
    'WORKSHOP_STEAM_OFFLINE': 'Steam 账号未连接在线会话，请在 Steam 登录后重试。',
    'WORKSHOP_STEAM_LICENSE_MISSING': '当前 Steam 账号没有正式版游戏许可。',
    'WORKSHOP_STEAM_APP_MISMATCH': 'Steam 会话的游戏标识不匹配，已禁止操作。',
    'WORKSHOP_SESSION_TIMEOUT': 'Steam 会话响应超时；已提交的操作结果尚未确认，请刷新，不要重复提交。',
    'WORKSHOP_SESSION_LOST': 'Steam 会话已断开；已提交的操作可能仍在 Steam 执行，请刷新确认。',
    'WORKSHOP_PROCESS_RUNNING': '游戏正在运行，请退出游戏后再订阅、取消订阅或更新模组。',
    'WORKSHOP_PROCESS_UNKNOWN': '暂时无法确认游戏已退出，已禁止修改工坊内容。',
    'WORKSHOP_OPERATION_BUSY': '这些条目已有未确认的工坊操作，请等待或刷新结果。',
    'WORKSHOP_OPERATION_REJECTED': 'Steam 未接受该操作，请刷新状态后重试。',
    'WORKSHOP_OPERATION_FAILED': 'Steam 返回操作失败，请检查客户端提示后重试。',
    'WORKSHOP_OPERATION_TIMEOUT': '等待 Steam 回调超时，结果仍未确认，请刷新核对；不会自动重复提交。',
    'WORKSHOP_NOT_SUBSCRIBED': '请先订阅该条目，再更新其持久安装内容。',
    'WORKSHOP_SESSION_LIMIT': 'Steam 订阅条目数量超过本次安全读取上限。',
    'WORKSHOP_OPERATION_INTERRUPTED': '游戏或安装保护状态发生变化，已停止提交后续操作；Steam 已接受的任务可能继续执行。',
    'WORKSHOP_WRONG_APP': '工坊条目不属于 Probably Stolen 正式版。',
    'WORKSHOP_PAGE_CHANGED': 'Steam 工坊页面结构变化或未返回所请求内容，请重试或打开官方页面。',
    'WORKSHOP_UNSAFE_URL': '拒绝访问非 Steam 官方地址。',
    'WORKSHOP_ACCESS_DENIED': 'Steam 拒绝公开访问，请使用官方页面；管理器不会绕过访问限制。',
    'WORKSHOP_RATE_LIMITED': 'Steam 请求过于频繁，请稍后重试。',
    'WORKSHOP_UNAVAILABLE': 'Steam 暂时不可用，请稍后重试。',
    'WORKSHOP_HTTP_ERROR': 'Steam 未返回所请求内容，请打开官方页面核对。',
    'WORKSHOP_RESPONSE_LIMIT': 'Steam 返回内容超过读取上限。',
    'WORKSHOP_NETWORK_ERROR': '无法连接 Steam。仍可查看本机模组，网络恢复后请重试。',
    'WORKSHOP_RESPONSE_INVALID': 'Steam 返回内容格式无效，请重试。',
    'WORKSHOP_INVALID_QUERY': '搜索条件、页码或批量查询条目数量无效。',
    'WORKSHOP_INVALID_HANDOFF': '工坊交接操作无效。',
    'WORKSHOP_INVALID_ID': '工坊 ID 必须是有效的数字条目 ID。',
    'WORKSHOP_OPEN_FAILED': '无法打开 Steam 官方页面，请确认客户端或默认浏览器可用。',
    'WORKSHOP_ANONYMOUS_UNAVAILABLE': '本游戏的匿名下载与更新能力尚未验证；请使用 Steam 官方订阅流程。',
    'WORKSHOP_INSTALLATION_REQUIRED': '请先选择游戏安装目录。',
    'WORKSHOP_WRONG_GAME': '创意工坊仅支持正式版 AppID 4348910。',
    'WORKSHOP_INVALID_INSTALLATION': '游戏安装路径必须是宿主确认的绝对路径。',
    'WORKSHOP_UNSAFE_OR_UNREADABLE_PATH': '工坊路径不可安全读取。',
    'STEAM_LIBRARY_NOT_DETECTED': '该安装目录不在可识别的 Steam 库中。',
    'WORKSHOP_ACF_MISSING': '未找到 Steam 工坊本机记录，订阅状态未确认。',
    'WORKSHOP_ACF_INVALID': 'Steam 工坊本机记录无效，仍可检查内容目录。',
    'WORKSHOP_ITEM_LIMIT': '本机工坊条目超过读取上限。',
    'WORKSHOP_CONTENT_UNREADABLE': '工坊内容目录不可读取。',
    'WORKSHOP_MANIFEST_UNREADABLE': '模组清单不可安全读取。',
    'WORKSHOP_ITEM_UNAVAILABLE': 'Steam 未返回该条目的公开详情。',
}


class WorkshopError(Exception):
    """Safe bridge error: never include response bodies, cookies or account IDs."""

    def __init__(self, code, message, *, retryable=False):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)

    def as_dict(self):
        return dict(code=self.code, message=self.message, retryable=self.retryable)
