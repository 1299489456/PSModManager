"""Evidence rules; capability presence is not malware or a high-risk verdict."""
RULE_VERSION = 'local-security-v3'

# Game capability (manifest <Capabilities>) that each static call category needs.
CATEGORY_CAPABILITY = {
    'network': 'network', 'process_start': 'process', 'open_url': 'process', 'native_interop': 'native',
    'dynamic_load': 'dynamic-code', 'dynamic_code': 'dynamic-code', 'reflection': 'reflection-external',
    'file_read': 'files', 'file_write': 'files', 'file_delete': 'files', 'filesystem': 'files',
}
CAPABILITY_LABEL = {'files': '文件读写', 'network': '访问互联网', 'process': '启动程序或打开链接',
                    'native': '原生系统库', 'dynamic-code': '动态代码', 'reflection-external': '反射访问'}
SYSTEM_COMMAND = ('currentversion/run', 'start menu/programs/startup', 'schtasks', 'powershell', 'cmd.exe',
                  'wscript', 'mshta', 'rundll32')
REGISTRY_WRITE = ('::setvalue', '::createsubkey', '::deletevalue', '::deletesubkey')


FRAMEWORK_TOKENS = {
    'mscorlib': 'b77a5c561934e089', 'System': 'b77a5c561934e089',
    'System.Private.CoreLib': '7cec85d7bea7798e',
    **{name: 'b03f5f7f11d50a3a' for name in ('System.Runtime', 'System.Runtime.Extensions',
        'System.IO.FileSystem', 'System.IO.FileSystem.Primitives', 'System.Net.Http',
        'System.Net.Requests')},
    'System.Net.WebClient': 'cc7b13ffcd2ddd51',
}


def framework_reference(call):
    """Declared external framework identity, not proof of loaded implementation."""
    declared = call.get('declaring_scope') or {}
    assembly = declared.get('assembly') or {}
    return (declared.get('kind') == 'assembly_reference'
            and call.get('target_kind') in {'member_reference', 'method_specification'}
            and assembly.get('name') in FRAMEWORK_TOKENS
            and str(assembly.get('public_key_token', '')).lower() == FRAMEWORK_TOKENS[assembly['name']]
            and assembly.get('culture') in ('', 'neutral', None))


def analyze(package, files, parsed):
    findings, gaps, embedded = [], [], 0
    def finding(rule, severity, title, evidence, basis='static_evidence'):
        if len(findings) >= 1024:
            if not any(g['reason'] == 'finding_evidence_limit' for g in gaps):
                gaps.append(dict(path=None, reason='finding_evidence_limit'))
            return
        findings.append(dict(id=f'F{len(findings)+1:04}', rule=rule, severity=severity, title=title,
                             evidence=evidence[:24], basis=basis))
    listed = {p.casefold() for p in package['assemblies']}
    detected, registry, credential, commands, urls = {}, [], [], [], {}
    for row in files:
        path = row['path']
        if row['kind'] in {'script', 'archive'}:
            finding('uninspected_payload', 'warning', '包内脚本或归档需另行审查', [dict(path=path, detail=row['kind'])])
            gaps.append(dict(path=path, reason='script_or_nested_archive_not_analyzed'))
        if row['kind'] != 'pe':
            continue
        if path.casefold() not in listed:
            finding('unlisted_pe', 'warning', '发现清单未声明的可执行格式文件', [dict(path=path)])
        result = parsed.get(path) or {}
        metadata = result.get('metadata') or {}
        sec = metadata.get('security') or {}
        if result.get('status') not in {'managed', 'native'} or not sec:
            gaps.append(dict(path=path, reason=result.get('error') or 'parser_unavailable'))
            continue
        coverage = sec.get('coverage') or {}
        if coverage.get('status') != 'complete':
            gaps.append(dict(path=path, reason='partial_static_analysis', details=coverage.get('reasons', [])))
        if any(coverage.get('omitted_evidence_counts', {}).get(k, 0) for k in ('calls', 'strings')):
            gaps.append(dict(path=path, reason='rule_evidence_limited', details=coverage.get('omitted_evidence_counts', {})))
        embedded += max(0, sec.get('sizes', {}).get('embedded_resource_payload_bytes', 0))
        if result['status'] == 'native':
            finding('native_code', 'warning', '原生代码未进行反汇编行为分析', [dict(path=path)])
            gaps.append(dict(path=path, reason='native_behavior_not_analyzed'))
        calls = sec.get('calls', [])
        strings_all = sec.get('strings', [])
        for c in calls:
            row = dict(path=path, method=c.get('method'), offset=c.get('offset'), detail=c.get('target'))
            capability = CATEGORY_CAPABILITY.get(c.get('category'))
            if capability:
                detected.setdefault(capability, []).append(row)
            if c.get('category') == 'registry':
                registry.append((row, any(t in str(c.get('target', '')).casefold() for t in REGISTRY_WRITE)))
            if c.get('category') == 'credential_access':
                credential.append((row, c.get('method_token')))
        for pinvoke in sec.get('pinvokes', []):
            detected.setdefault('native', []).append(dict(path=path, method=pinvoke.get('method'),
                detail=str(pinvoke.get('module')) + '!' + str(pinvoke.get('entry_point'))))
        for value in strings_all:
            text = str(value.get('value', ''))
            if value.get('kind') == 'url' or '://' in text:
                urls.setdefault(text[:200], dict(path=path, method=value.get('method'), offset=value.get('offset'), detail=text[:200]))
            elif any(t in text.casefold().replace('\\', '/') for t in SYSTEM_COMMAND):
                token = value.get('method_token')
                acts = [c for c in calls if token and c.get('method_token') == token and (
                    c.get('category') in ('process_start', 'file_write') or
                    (c.get('category') == 'registry' and any(t in str(c.get('target', '')).casefold() for t in REGISTRY_WRITE)))]
                commands.append((dict(path=path, method=value.get('method'), offset=value.get('offset'), detail=text[:160]),
                                 [dict(path=path, method=c.get('method'), offset=c.get('offset'), detail=c.get('target')) for c in acts],
                                 any(c.get('category') == 'network' for c in calls if token and c.get('method_token') == token)))
        # Method tokens are only unique within one assembly; combine within this file.
        for token in sorted({c.get('method_token') for c in calls if c.get('category') == 'credential_access' and c.get('method_token')}):
            same = [c for c in calls if c.get('method_token') == token]
            sends = [c for c in same if c.get('category') == 'network']
            if sends:
                rows = [dict(path=path, method=c.get('method'), offset=c.get('offset'), detail=c.get('target'))
                        for c in same if c.get('category') in ('credential_access', 'network')]
                finding('credential_decrypt_and_send', 'high', '同一方法解密本机受保护数据并调用网络，需复核是否上传', rows,
                        'same_method_static_combination')
        for category in sorted({c.get('category') for c in calls if c.get('category')}):
            evidence = [dict(path=path, method=c.get('method'), offset=c.get('offset'), detail=c.get('target'))
                        for c in calls if c.get('category') == category]
            finding('capability_call', 'info', '静态调用：' + category, evidence, 'call_instruction_not_runtime_execution')
        if sec.get('pinvokes'):
            finding('pinvoke', 'info', '存在系统接口声明，需结合用途判断', [dict(path=path, method=p.get('method'), detail=str(p.get('module')) + '!' + str(p.get('entry_point'))) for p in sec['pinvokes']])
        resources = [r for r in sec.get('resources', []) if str(r.get('header_kind', '')).lower() in {'pe', 'mz', 'executable', 'zip', '7z', 'rar', 'gzip'}]
        if resources:
            finding('embedded_executable', 'warning', '内嵌资源包含可执行格式或归档，需复核加载用途', [dict(path=path, detail=r.get('name')) for r in resources])
            gaps.append(dict(path=path, reason='embedded_executable_not_analyzed'))
        if sum(sec.get('resource_kind_counts', {}).get(k, 0) for k in ('pe', 'zip', '7z', 'rar', 'gzip')) > len(resources):
            gaps.append(dict(path=path, reason='embedded_payload_evidence_limited'))
        # A concrete same-method combination is stronger than an arbitrary API
        # reference. Still call it a review signal, not proved data exfiltration.
        strings = sec.get('strings', [])
        targets = ('login data', 'local state', 'cookies', 'wallet.dat', 'loginusers.vdf')
        sensitive = [s for s in strings if any(t in str(s.get('value', '')).casefold() for t in targets)]
        if sensitive:
            tokens = {s.get('method_token') for s in sensitive if s.get('method_token')}
            matching = [c for c in calls if c.get('method_token') in tokens
                        and any(t in str(c.get('target', '')).casefold() for t in ('system.io.file', 'system.io.directory', 'system.net', 'unitywebrequest'))]
            finding('sensitive_data_reference', 'warning', '凭据或会话数据特征与文件/网络调用需要复核' if matching else '发现凭据或会话数据特征字符串',
                    [dict(path=path, method=s.get('method'), offset=s.get('offset'), detail='sensitive-path-pattern') for s in sensitive],
                    'same_method_static_combination' if matching else 'string_only')
            for token in sorted(tokens):
                method = next(s.get('method') for s in sensitive if s.get('method_token') == token)
                members = [c for c in calls if c.get('method_token') == token and framework_reference(c)]
                reads = [c for c in members if any(t in str(c.get('target', '')).casefold()
                         for t in ('system.io.file::readall', 'system.io.file::readlines', 'system.io.file::openread',
                                   'system.io.file.readall', 'system.io.file.readlines', 'system.io.file.openread'))]
                sends = [c for c in members if any(t in str(c.get('target', '')).casefold()
                         for t in ('httpclient::post', 'httpclient::send', 'webclient::upload',
                                   'httpwebrequest::getrequeststream', 'unitywebrequest::post',
                                   'httpclient.post', 'httpclient.send', 'webclient.upload',
                                   'httpwebrequest.getrequeststream', 'unitywebrequest.post'))]
                if reads and sends:
                    finding('sensitive_read_and_send', 'high', '同一方法包含凭据数据特征、文件读取及网络发送调用，启动前需复核',
                            [dict(path=path, method=method, method_token=token, offset=c.get('offset'), detail=c.get('target'), declaring_scope=c.get('declaring_scope')) for c in reads + sends]
                            + [dict(path=path, method=method, method_token=token, offset=s.get('offset'), detail='sensitive-path-pattern') for s in sensitive if s.get('method_token') == token],
                            'same_method_three_signals_no_dataflow_or_execution_proof')
    # Declared permissions versus what the code statically calls. A mismatch is a review
    # signal: undeclared use means the manifest understates what the mod can do.
    declared = {str(value) for value in package.get('capabilities') or []}
    for capability in sorted(detected):
        if capability not in declared:
            rows = detected[capability]
            finding('undeclared_capability', 'info' if capability == 'reflection-external' else 'warning',
                    f'代码使用了清单未声明的权限：{CAPABILITY_LABEL.get(capability, capability)}（{len(rows)} 处）', rows,
                    'undeclared_capability_static')
    for capability in sorted(declared - set(detected)):
        finding('declared_without_static_use', 'info', f'声明了权限但静态未发现对应调用：{CAPABILITY_LABEL.get(capability, capability)}',
                [dict(path='manifest.xml', detail=capability)], 'declared_without_static_use')
    writes = [row for row, write in registry if write]
    if writes:
        finding('registry_write', 'warning', f'修改 Windows 注册表（{len(writes)} 处）', writes, 'call_instruction_not_runtime_execution')
    elif registry:
        finding('registry_read', 'info', f'读取 Windows 注册表（{len(registry)} 处）', [row for row, _ in registry], 'call_instruction_not_runtime_execution')
    if credential:
        finding('credential_decrypt', 'warning', '调用本机受保护数据解密或凭据库接口', [row for row, _ in credential],
                'call_instruction_not_runtime_execution')
    combined = [(text, acts, net) for text, acts, net in commands if acts]
    if combined:
        finding('system_command_action', 'high', '同一方法包含系统命令或自启动特征，并启动进程、写文件或改注册表',
                [row for text, acts, _ in combined for row in [text, *acts]], 'same_method_static_combination')
    loose = [text for text, acts, _ in commands if not acts]
    if loose:
        finding('system_command_string', 'warning', '发现系统命令或自启动相关字符串', loose, 'string_only')
    if urls:
        finding('network_endpoints', 'info', f'代码中出现的网址（{len(urls)} 个）', list(urls.values()), 'string_only')
    if package.get('capabilities'):
        finding('declared_capabilities', 'info', '模组声明的能力（不是实际行为保证）',
                [dict(path='manifest.xml', detail=', '.join(package['capabilities']))], 'manifest_declaration')
    return findings, gaps, embedded
