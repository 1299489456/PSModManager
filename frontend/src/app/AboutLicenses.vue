<script setup>
import { computed } from 'vue'
import { BadgeCheck, Blocks, Globe, Scale, ShieldCheck, Sparkles } from 'lucide-vue-next'
import licenseText from '../../../LICENSE?raw'
import noticesText from '../../../THIRD_PARTY_NOTICES.md?raw'

defineProps({ version: { type: String, default: '' } })
// Every copyright line of LICENSE: this project's and the upstream notice MIT requires us to keep.
const copyrights = computed(() => licenseText.split('\n').map(line => line.trim()).filter(line => /^Copyright/i.test(line)))
// Main components shipped with the portable build; the full list is in the notices below.
const groups = [
  { name: '界面', items: [
    ['Vue', '3.5.40', 'MIT', '界面框架'], ['Lucide', '0.562.0', 'ISC', '图标'] ] },
  { name: '桌面与运行环境', items: [
    ['CPython', '3.11.14', 'PSF License', '程序运行环境'], ['pywebview', '6.2.1', 'BSD-3-Clause', '桌面窗口'],
    ['Microsoft WebView2 SDK', '1.0.3856.49', 'Microsoft 软件许可', '页面显示'], ['pythonnet', '3.0.5', 'MIT', '调用 .NET 组件'],
    ['.NET 运行时', '10.0.11', 'MIT', '辅助程序运行环境'], ['pywin32', '311', 'PSF License', 'Windows 系统接口'] ] },
  { name: '网络与数据', items: [
    ['httpx', '0.28.1', 'BSD-3-Clause', '连接 AI 服务'], ['pydantic', '2.12.5', 'MIT', '数据校验'],
    ['keyring', '25.7.0', 'MIT', '把密钥存进 Windows 凭据'], ['certifi', '2026.6.17', 'MPL-2.0', '根证书'],
    ['OpenSSL', '3.5.5', 'Apache-2.0', '加密连接'], ['SQLite', '3.50.4', '公有领域', '本地数据库'], ['psutil', '7.2.2', 'BSD-3-Clause', '进程管理'] ] },
  { name: '打包', items: [['PyInstaller', '6.19.0', 'GPL-2.0（含运行时例外）', '生成便携版']] },
]
const services = [
  ['MelonLoader', 'Apache-2.0', '游戏的 Mod 加载器。PSMM 只在你点击安装时从官方 GitHub 发布页下载，不包含它的代码。'],
  ['Nexus Mods', '服务条款', '使用你自己的个人 API Key 浏览和下载 Mod，请遵守 Nexus Mods 的服务条款和作者的许可。'],
  ['AI 服务', '由你选择', '你自己配置的 OpenAI 兼容服务。只有点击“分析日志”或“AI 翻译”时才会发送内容。'],
]
const features = [
  ['模组', '启用和停用 Mod，导入 DLL、文件夹或 ZIP，识别新旧版本并升级或降级，保存前可撤销。'],
  ['方案', '保存一组 Mod 的启停和版本，随时切换、应用到游戏，导出清单或含 Mod 文件的整合包。'],
  ['Nexus', '浏览和搜索 Nexus 上本游戏的全部 Mod，查看详情、AI 翻译介绍，下载后预览再导入。'],
  ['日志', '连续阅读游戏和 PSMM 日志，查找、只看错误，让 AI 分析原因并给出可确认的操作建议。'],
  ['其他', '检查和安装 MelonLoader、编辑 Mod 配置、保存后一键启动游戏、本地数据检查。'],
]
</script>

<template>
  <div class="licenses" data-testid="about-licenses">
    <article class="garden-card wide license-hero">
      <span class="seal" aria-hidden="true"><Scale :size="26"/></span>
      <div>
        <h2>PSMM 以 MIT 许可证开源</h2>
        <p>可以免费使用、复制、修改和分发，只需在副本中保留版权和许可声明。软件按“原样”提供，不附带任何形式的担保。</p>
        <dl class="garden-facts wide"><dt>版本</dt><dd>PSMM v{{ version }}</dd><dt>许可证</dt><dd>MIT License</dd><dt>版权声明</dt><dd><span v-for="line in copyrights" :key="line" class="copyright-line">{{ line }}</span></dd></dl>
        <details class="fulltext"><summary>查看许可证全文</summary><pre data-testid="license-text">{{ licenseText.trim() }}</pre></details>
      </div>
    </article>

    <div class="garden-cards">
      <article class="garden-card">
        <h2><ShieldCheck :size="18" aria-hidden="true"/>版权与商标</h2>
        <ul class="plain">
          <li>PSMM 是玩家社区的非官方工具，与 Probably Stolen 的开发商和发行商、Nexus Mods、MelonLoader 项目都没有隶属或合作关系。</li>
          <li>“Probably Stolen”及游戏素材的版权和商标归各自权利人所有。</li>
          <li>通过 PSMM 浏览和下载的 Mod 版权归原作者，使用和分享时请遵守作者的许可。</li>
          <li>AI 的翻译和分析结果仅供参考，可能存在错误。</li>
        </ul>
      </article>
      <article class="garden-card">
        <h2><BadgeCheck :size="18" aria-hidden="true"/>数据与隐私</h2>
        <ul class="plain">
          <li>所有数据保存在本机的数据目录，删除程序不会删除它们。</li>
          <li>只在三种情况下联网：访问 Nexus（使用你的 Key）、下载 MelonLoader、把内容发送给你配置的 AI 服务。</li>
          <li>发送给 AI 的日志会先隐藏用户名等个人信息；密钥只保存在 Windows 凭据中。</li>
          <li>PSMM 不上传使用统计。</li>
        </ul>
      </article>
    </div>

    <article class="garden-card wide">
      <h2><Sparkles :size="18" aria-hidden="true"/>功能</h2>
      <dl class="feature-list"><template v-for="[name, text] in features" :key="name"><dt>{{ name }}</dt><dd>{{ text }}</dd></template></dl>
    </article>

    <article class="garden-card wide">
      <h2><Blocks :size="18" aria-hidden="true"/>使用的开源组件</h2>
      <p class="note">以下是主要组件，每个组件适用各自的许可证。完整清单见下方“第三方说明”，便携版的 licenses 目录保存了每个组件的许可正文。</p>
      <section v-for="group in groups" :key="group.name" class="component-group">
        <h3>{{ group.name }}</h3>
        <ul class="components">
          <li v-for="[name, version, license, use] in group.items" :key="name"><b>{{ name }}</b><span class="version">{{ version }}</span><span class="use">{{ use }}</span><span class="tg on">{{ license }}</span></li>
        </ul>
      </section>
    </article>

    <article class="garden-card wide">
      <h2><Globe :size="18" aria-hidden="true"/>外部工具与服务</h2>
      <ul class="components services">
        <li v-for="[name, license, text] in services" :key="name"><b>{{ name }}</b><span class="use">{{ text }}</span><span class="tg off">{{ license }}</span></li>
      </ul>
    </article>

    <article class="garden-card wide">
      <details class="fulltext"><summary>第三方说明（全文）</summary><pre data-testid="third-party-text">{{ noticesText.trim() }}</pre></details>
    </article>
  </div>
</template>

<style scoped>
.licenses{display:flex;flex-direction:column;gap:18px;max-width:1100px}
.license-hero{display:flex;gap:18px;align-items:flex-start;background:linear-gradient(135deg,#fffbeb,rgba(255,255,255,.95) 55%)}
.seal{flex:none;width:56px;height:56px;display:grid;place-items:center;border-radius:50% 50% 50% 16px;background:linear-gradient(140deg,var(--g-sun),var(--g-sun-2));color:#fff;box-shadow:0 10px 18px -8px rgba(245,158,11,.7)}
.license-hero h2{margin:2px 0 6px;font-size:19px}
.license-hero p{margin:0 0 12px;font-size:14px;line-height:1.7;color:var(--g-text-2)}
.copyright-line{display:block}
.fulltext{margin-top:12px}
.fulltext summary{cursor:pointer;font-size:13.5px;font-weight:600;color:var(--g-leaf-3)}
.fulltext pre{max-height:360px;overflow:auto;margin:10px 0 0;padding:14px 16px;border-radius:14px;background:#f6f8f6;border:1px solid #e2ebe5;font:12.5px/1.6 Consolas,monospace;white-space:pre-wrap;color:var(--g-text-2)}
.plain{margin:0;padding-left:18px;display:flex;flex-direction:column;gap:6px;font-size:13.5px;line-height:1.65;color:var(--g-text-2)}
.plain li::marker{color:var(--g-leaf-2)}
.feature-list{display:grid;grid-template-columns:72px 1fr;gap:8px 14px;margin:8px 0 0;font-size:13.5px;line-height:1.65}
.feature-list dt{font-weight:700;color:var(--g-ink)}
.feature-list dd{margin:0;color:var(--g-text-2)}
.note{margin:0 0 6px;font-size:13px;color:var(--g-text-3)}
.component-group h3{margin:14px 0 6px;font-size:13px;letter-spacing:.04em;color:var(--g-leaf-3)}
.components{margin:0;padding:0;list-style:none;display:flex;flex-direction:column}
.components li{display:grid;grid-template-columns:minmax(150px,220px) 110px minmax(0,1fr) auto;align-items:center;gap:12px;padding:8px 0;border-bottom:1px dashed #dcfce7;font-size:13.5px}
.components li:last-child{border-bottom:0}
.components .version{color:var(--g-text-3);font-variant-numeric:tabular-nums}
.components .use{color:var(--g-text-2)}
.components .tg{justify-self:end;white-space:nowrap}
.services li{grid-template-columns:minmax(120px,160px) minmax(0,1fr) auto}
@media (max-width:900px){.components li{grid-template-columns:1fr auto}.components .version,.components .use{grid-column:1}}
</style>
