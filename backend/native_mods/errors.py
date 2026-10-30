"""Errors at the native service boundary, without private file contents."""
from backend.runtime.events import RuntimePolicyError


class NativeModError(RuntimePolicyError):
    def __init__(self, code, detail=None):
        super().__init__(code)
        self.detail = detail or {}


def issue(code, mod_id=None, *, severity='error', **detail):
    return {'code': code, 'mod_id': mod_id, 'severity': severity, **detail}


MESSAGES = {
    'NATIVE_APPROVAL_SOURCE_UNPROVEN': '来源身份不符合游戏原生格式，无法写入对应批准。',
    'NATIVE_STATE_CHANGED': '游戏状态或模组文件已被其他程序修改，请刷新后重新确认。',
    'NATIVE_PROCESS_UNKNOWN': '无法确认游戏已经退出，当前只读；请退出游戏后重试。',
    'NATIVE_STATE_FORMAT': '游戏状态文件不是受支持的 ES3 明文类型 JSON，已停止写入。',
    'NATIVE_CONFIG_FORMAT': '原生配置结构无效，已保留原文件。',
    'NATIVE_CONFIG_DUPLICATE_KEY': '配置存在重复键，无法安全确定修改目标。',
    'NATIVE_UNSAFE_PATH': '路径含跳转、链接、保留名称或不安全结构，已拒绝操作。',
    'NATIVE_PLAN_BLOCKED': '方案存在阻断问题，请先处理缺失依赖、循环或无效包。',
    'NATIVE_INSTALLATION_REQUIRED': '请先选择并登记游戏安装目录。',
    'NATIVE_USER_DATA_UNKNOWN': '未找到当前用户的数据目录，请重新识别游戏。',
    'NATIVE_DATA_ROOT_CONFLICT': '管理器数据目录与游戏或模组来源重叠，已停止操作。',
    'NATIVE_FILE_LIMIT': '文件超过安全读取上限。',
    'NATIVE_WRITE_READBACK_FAILED': '写入后的读回校验失败，请使用此次恢复点处理。',
    'NATIVE_INVALID_ORDER': '启用顺序包含无效或重复的原生 ID。',
    'NATIVE_INVALID_RULE': '排序规则必须引用有效的原生 ID。',
    'NATIVE_INVALID_ID': '模组 ID 不符合游戏原生格式。',
    'NATIVE_MOD_NOT_FOUND': '该模组已不在当前安装中，请刷新列表。',
    'NATIVE_MOD_MISSING': '方案中的模组尚未安装。',
    'NATIVE_DEPENDENCY_MISSING': '缺少必须安装的依赖模组。',
    'NATIVE_DEPENDENCY_DISABLED': '必须的依赖模组尚未加入启用列表。',
    'NATIVE_DEPENDENCY_CYCLE': '依赖或排序规则形成循环，不能保存。',
    'NATIVE_ORDER_WARNING': '保存顺序中有依赖排在使用它的模组之后。',
    'NATIVE_PACKAGE_DIRECTORY_REQUIRED': '请选择包含 manifest.xml 的完整包目录。',
    'NATIVE_CASE_COLLISION': '包内路径仅大小写不同，会在 Windows 上发生冲突。',
    'NATIVE_PACKAGE_LIMIT': '包超过文件数、单文件或展开体积的安全上限。',
    'NATIVE_MANIFEST_MISSING': '包中缺少原生 manifest.xml。',
    'NATIVE_MANIFEST_UNSAFE_XML': '清单包含不受支持的 XML 实体或文档类型。',
    'NATIVE_MANIFEST_INVALID': 'manifest.xml 无法解析。',
    'NATIVE_MANIFEST_REQUIRED_ELEMENTS': '清单缺少必需的 Assemblies 或 Capabilities 元素。',
    'NATIVE_INVALID_ASSEMBLY_PATH': '清单包含无效的 DLL 相对路径。',
    'NATIVE_MANIFEST_NO_ASSEMBLIES': '清单没有列出要加载的 DLL。',
    'NATIVE_UNKNOWN_CAPABILITY': '清单声明了游戏不认识的权限。',
    'NATIVE_INVALID_DEPENDENCY': '清单包含无效的依赖 ID。',
    'NATIVE_ASSEMBLY_MISSING': '清单列出的 DLL 不存在。',
    'NATIVE_ASSEMBLY_INVALID': '列出的文件不是可读取的托管 DLL。',
    'NATIVE_ASSEMBLY_NOT_MANAGED': '原生包列出了非托管 DLL，游戏不会按 IMod 加载它。',
    'NATIVE_UNLISTED_DLL': '包内包含清单未列出的 DLL。',
    'NATIVE_UNSAFE_ARCHIVE': 'ZIP 含链接、加密文件或不受支持的条目。',
    'NATIVE_ARCHIVE_PACKAGE_COUNT': 'ZIP 必须只包含一个完整原生模组包。',
    'NATIVE_ARCHIVE_MIXED_ROOTS': 'ZIP 有包目录之外的文件，不能自动归并。',
    'NATIVE_ARCHIVE_INVALID': 'ZIP 内容无效或不完整。',
    'NATIVE_PACKAGE_UNREADABLE': '模组包无法读取。',
    'NATIVE_LEGACY_LOOSE_DLL': '发现旧格式裸 DLL；正式版需要完整原生包。',
    'NATIVE_DUPLICATE_LOCAL_ID': '多个本地目录使用同一个原生 ID，请先处理重复来源。',
    'NATIVE_SOURCE_SHADOWED': '同 ID 有多个来源，本地副本优先于工坊副本。',
    'NATIVE_ID_CASE_COLLISION': '模组 ID 仅大小写不同，配置路径会在 Windows 上冲突。',
    'NATIVE_DATA_UNREADABLE': '原生配置或数据目录无法安全读取。',
    'NATIVE_IMPORT_SOURCE': '导入来源不可用或与管理器数据目录重叠。',
    'NATIVE_PACKAGE_REQUIRED': '请选择原生包目录或 ZIP，不支持直接导入裸 DLL。',
    'NATIVE_LOCAL_ALREADY_EXISTS': '该 ID 已有本地包；不能静默覆盖。',
    'NATIVE_TARGET_EXISTS': '目标目录已存在；原文件未被覆盖。',
    'NATIVE_PREVIEW_EXPIRED': '导入预览已失效，请重新选择包并检查。',
    'NATIVE_RECOVERY_INVALID': '恢复点结构无效，已停止恢复。',
    'NATIVE_RECOVERY_INCOMPLETE': '此备份创建未完成，游戏文件尚未被该操作修改；不能使用残缺副本恢复。',
    'NATIVE_RECOVERY_NOT_FOUND': '恢复点已不存在。',
    'NATIVE_RECOVERY_UNSUPPORTED': '此恢复点类型尚不受支持。',
    'NATIVE_RECOVERY_PENDING': '上一次原生写入未完成，请先在恢复点中处理后再继续。',
    'NATIVE_RUNTIME_UNSUPPORTED': '所选安装不是受支持的正式版 Mono 原生环境；已禁止读取正式版状态和写入操作。',
    'NATIVE_PROFILE_FORMAT': '方案文件格式无效，已保留原文件。',
    'NATIVE_PROFILE_NAME': '请输入不超过 100 字符的方案名称。',
    'NATIVE_PROFILE_LIMIT': '方案数量达到上限。',
    'NATIVE_PROFILE_NOT_FOUND': '方案已不存在，请刷新。',
    'NATIVE_LOCAL_NOT_FOUND': '当前生效来源不是本地包。',
    'NATIVE_WORKSHOP_COPY_REQUIRED': '没有可切换的同 ID 工坊副本。',
    'NATIVE_DEPENDENTS_ENABLED': '仍有已启用模组依赖它，请先确认并一起停用。',
    'NATIVE_SOURCE_SWITCH_UNSUPPORTED': '此操作仅支持从本地遮蔽副本切换到工坊副本。',
}
