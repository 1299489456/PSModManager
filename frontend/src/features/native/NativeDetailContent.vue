<script setup>
import { computed, ref, watch } from 'vue'
import Icon from './GardenIcon.vue'
import Growth from './NativeGrowth.vue'
import Permissions from './NativePermissions.vue'
import { nameOf, sourceText, sizeText } from './nativeModel.js'
const props = defineProps({ mod: Object, mods: Array, order: Array, baseline: Array, issues: Array, tab: String, drawer: Boolean, expanded: Boolean, config: Object, busy: Boolean, writeAllowed: Boolean, segment: String })
const emit = defineEmits(['tab', 'toggle', 'close', 'expand', 'action', 'goto', 'fix', 'save-config'])
const entries = ref([])
watch(() => props.config, c => { entries.value = (c?.entries || []).map(x => ({ ...x })) }, { immediate: true })
const mod = computed(() => props.mod)
const reverse = computed(() => props.mods.filter(m => m.prerequisites?.includes(mod.value?.id)))
const issues = computed(() => props.issues.filter(i => i.id === mod.value?.id || i.dep === mod.value?.id))
const byId = id => props.mods.find(m => m.id === id)
const assemblies = computed(() => (mod.value?.assemblies || []).map(x => typeof x === 'string' ? { path: x } : x))
const files = computed(() => (mod.value?.files || []).map(x => typeof x === 'string' ? { path: x } : x))
const entryCount = computed(() => mod.value?.entry_count ?? mod.value?.metadata?.entry_count ?? null)

const facts = computed(() => [
  ['作者', mod.value.author || '未注明'], ['来源', sourceText(mod.value)], ['原生 ID', mod.value.id], ['工坊 ID', mod.value.workshop_id || '—'],
  ['程序集', `${assemblies.value.length} 个已声明 DLL · ${entryCount.value == null ? '入口待分析' : `${entryCount.value} 个入口`}`],
  ['文件', `${mod.value.file_count ?? files.value.length} 个 · ${sizeText(mod.value.size_bytes)}`],
  ['依赖', mod.value.prerequisites?.length ? mod.value.prerequisites.map(id => nameOf(byId(id)) === '未知模组' ? id : nameOf(byId(id))).join('、') : '没有声明'],
])
const fileChecks = computed(() => mod.value.package_checks || [])
const logLines = computed(() => { const v = mod.value.run?.evidence; return Array.isArray(v) ? v.map(x => typeof x === 'string' ? x : x.line || x.text || JSON.stringify(x)).join('\n') : typeof v === 'string' ? v : '' })
const has = tab => props.expanded ? (props.segment === 'primary' ? ['overview','files'].includes(tab) : ['perm','deps'].includes(tab)) : props.tab === tab
function copy(value) { emit('action', { kind: 'copy', value }) }
</script>
<template>
      <div class="d-body">
        <Growth v-if="expanded && segment === 'primary'" :mod="mod" :order="order" :baseline="baseline"/>
        <template v-if="!expanded && (has('overview') || has('deps')) || expanded && segment === 'primary'"><div v-for="(issue,i) in issues" :key="i" :class="[issue.level === 'block' ? 'care-note' : 'sign-note', 'o1']"><b>{{ issue.message }}</b><div v-if="issue.action" class="note-act"><button class="garden-btn xs" @click="$emit('fix', issue)">{{ { enable:'一起启用', sort:'自动排序', workshop:'在创意工坊查找' }[issue.action] }}</button></div></div></template>
        <Permissions v-if="segment !== 'primary' && (has('perm') || has('overview'))" :mod="mod" :order="order" :baseline="baseline" :full="expanded || tab === 'perm'" @details="$emit('tab', 'perm')"/>
        <template v-if="has('overview')">
          <div class="sec"><h3>简介</h3><div class="desc">{{ mod.description || '清单没有提供简介。' }}</div></div>
          <div class="sec"><h3>基本信息</h3><dl class="garden-facts"><template v-for="[label,value] in facts" :key="label"><dt>{{ label }}</dt><dd><template v-if="label.endsWith('ID')"><code>{{ value }}</code><button v-if="value !== '—'" class="copy" :title="`复制${label}`" @click="copy(value)"><Icon name="copy" size="xs"/></button></template><template v-else>{{ value }}</template></dd></template></dl></div>
        </template>
        <template v-if="has('deps')">
          <div v-if="mod.prerequisites?.length || reverse.length" class="sec"><h3>依赖关系 <small>来自 manifest.xml</small></h3><div class="vinegraph"><template v-if="mod.prerequisites?.length"><div class="vcol"><span v-for="id in mod.prerequisites" :key="id" class="vnode" :class="{ miss: !byId(id) }">{{ byId(id)?.name || id }}</span></div><Icon name="right"/></template><span class="vnode me">{{ nameOf(mod) }}</span><template v-if="reverse.length"><Icon name="right"/><div class="vcol"><span v-for="other in reverse" :key="other.id" class="vnode">{{ nameOf(other) }}</span></div></template></div></div>
          <div class="sec"><h3>需要 <small>{{ mod.prerequisites?.length || 0 }}</small></h3><div class="lst"><div v-for="id in mod.prerequisites" :key="id" class="li" :class="{ miss: !byId(id) }"><span class="garden-pot">{{ byId(id) ? nameOf(byId(id)).slice(0,2) : '?' }}</span><div class="ln"><b>{{ byId(id)?.name || id }}</b><span>{{ byId(id) ? `${sourceText(byId(id))} · v${byId(id).version || '未知'}` : '未安装 · 原生 ID' }}</span></div><span class="tg" :class="!byId(id) ? 'bad' : order.includes(id) ? 'on' : 'pend'">{{ !byId(id) ? '缺失' : order.includes(id) ? '已启用' : '未启用' }}</span><button class="garden-link" @click="byId(id) ? $emit('goto', id) : $emit('action', { kind:'search', value:id })">{{ byId(id) ? '查看' : '去工坊找' }}</button></div><span v-if="!mod.prerequisites?.length" class="muted">没有声明前置模组。没有声明不代表没有隐式依赖。</span></div></div>
          <div class="sec"><h3>被依赖 <small>{{ reverse.length }}</small></h3><div class="lst"><div v-for="other in reverse" :key="other.id" class="li"><span class="garden-pot">{{ nameOf(other).slice(0,2) }}</span><div class="ln"><b>{{ nameOf(other) }}</b><span>{{ order.includes(other.id) ? '已启用 · 停用本模组时会一起提示' : '未启用' }}</span></div><button class="garden-link" @click="$emit('goto', other.id)">查看</button></div><span v-if="!reverse.length" class="muted">没有其他已安装模组声明依赖它。</span></div></div>
          <div class="sec"><h3>潜在交互 <small>静态补丁交集，仅供验证</small></h3><div v-if="mod.patch_intersections?.length" class="lst"><div v-for="(overlap,i) in mod.patch_intersections" :key="i" class="li"><div class="ln"><b>{{ overlap.other_name || overlap.other_id }}</b><span>{{ overlap.targets?.join('、') || overlap.description }}</span></div><span class="tg off">需运行验证</span></div></div><p v-else class="muted">当前没有可用的补丁交集分析结果，不据此判断没有冲突。</p></div>
          <div class="sec"><h3>排序规则</h3><p class="muted">为本次顺序添加“ A 在 B 之前”的约束；与依赖形成环时阻止保存。</p><div class="note-act"><button class="garden-btn sm" @click="$emit('action', { kind:'rules' })"><Icon name="git" size="sm"/>编辑排序规则</button></div></div>
        </template>
        <template v-if="has('config') && !expanded">
          <div v-if="!config" class="garden-empty" role="status">正在读取配置…</div>
          <div v-else-if="config.error" class="care-note" role="alert">{{ config.error }}</div>
          <div v-else-if="!config.exists" class="garden-empty"><b>没有找到原生配置</b><span>ModConfigs 里没有 {{ mod.id }}.json，模组首次运行并注册配置后才会生成。</span></div>
          <template v-else><div class="mint-note">配置来自 <code class="mono">ModConfigs/{{ mod.id }}.json</code>。字段类型由模组运行时注册；这里按原文编辑，未知字段原样保留，保存前自动备份。<br v-if="!writeAllowed"/><b v-if="!writeAllowed">当前无法写入：操作中、游戏未停止或状态已变化。</b></div><div class="sec"><h3>配置项 <small>{{ entries.length }}</small><span class="sp"></span><button class="garden-btn soft xs" @click="$emit('action', { kind:'recovery' })"><Icon name="history" size="sm"/>备份</button><button class="garden-btn green xs" :disabled="busy || !writeAllowed" @click="$emit('save-config', entries.map(({key,value}) => ({key,value})))">保存配置</button></h3><table class="kv"><tbody><tr v-for="entry in entries" :key="entry.key"><td>{{ entry.key }}</td><td><input v-model="entry.value" :aria-label="entry.key"/></td><td class="ty">类型未知</td></tr></tbody></table></div></template>
        </template>
        <template v-if="has('files')">
          <div class="sec"><h3>包结构 <small>{{ mod.file_count ?? files.length }} 个文件 · {{ sizeText(mod.size_bytes) }}</small></h3><div class="garden-tree"><div><Icon name="folder" size="sm"/><span :title="mod.folder_path">{{ mod.folder_path }}</span></div><div v-for="(file,i) in files" :key="i" class="in1"><Icon :name="/\.dll$/i.test(file.path || file.relative_path) ? 'cpu' : 'file-text'" size="sm"/>{{ file.path || file.relative_path || file.name }}<span v-if="(file.size_bytes ?? file.size) != null" class="muted">{{ sizeText(file.size_bytes ?? file.size) }}</span></div><span v-if="!files.length" class="muted">没有文件清单。</span></div></div>
          <div class="sec"><h3>包检查</h3><div class="lst"><div v-for="(check,i) in fileChecks" :key="i" class="li"><Icon :name="check.passed ? 'check-circle' : 'info'"/><div class="ln"><b>{{ check.label || check.message }}</b><span>{{ check.detail }}</span></div></div><div class="li"><Icon name="layers"/><div class="ln"><b>{{ assemblies.length }} 个声明的 DLL</b><span>{{ entryCount == null ? '入口数量尚未分析；不运行 DLL 推断入口。' : `${entryCount} 个入口，包作为一个模组管理，入口结果分别核对。` }}</span></div></div><p v-if="!fileChecks.length && !mod.issues?.length" class="muted">清单与文件已读取；安全批准仍由游戏完成。</p></div></div>
          <div v-if="mod.sources?.length > 1" class="sec"><h3>安装来源 <small>同一 ID 有 {{ mod.sources.length }} 个</small></h3><div class="lst"><div v-for="source in mod.sources" :key="source.source_id" class="li"><span class="tg" :class="source.active ? 'on' : 'off'">{{ source.active ? '正在使用' : '被遮蔽' }}</span><div class="ln"><b>{{ sourceText(source) }} · {{ source.version || '版本未知' }}</b><span>{{ source.folder_path }}</span></div></div></div><div v-if="mod.source === 'local' && mod.sources.some(x => x.source === 'workshop')" class="note-act"><button class="garden-btn sm" :disabled="!writeAllowed || busy" @click="$emit('action', { kind:'switch-source' })"><Icon name="swap" size="sm"/>改用工坊版…</button></div></div>
        </template>
        <template v-if="has('log') && !expanded"><div class="sec"><h3>最近一次启动 <small>Player.log</small></h3><div class="logline">{{ logLines || '没有找到此模组的明确加载记录。启动游戏后刷新核对。' }}</div><div class="note-act"><button class="garden-link" @click="$emit('action', { kind:'logs' })">在日志页打开 →</button></div></div><div v-if="mod.run?.events?.length" class="sec"><h3>记录</h3><div class="timeline"><div v-for="(event,i) in mod.run.events" :key="i" class="ev"><time>{{ event.time || '日志事件' }}</time>{{ event.line || event.message }}</div></div></div></template>
        <div v-if="has('overview') || has('config') || has('files')" class="sec"><h3>数据位置 <small>卸载时不会自动清理</small></h3><div class="lst"><div class="path"><span class="pk">模组目录</span><code :title="mod.folder_path">{{ mod.folder_path }}</code><button class="garden-btn soft icon xs" title="打开模组目录" @click="$emit('action', { kind:'open', target:'mod' })"><Icon name="folder" size="sm"/></button></div><div class="path"><span class="pk">原生配置</span><code>{{ mod.data_locations?.find(x=>x.kind==='native_config')?.path || `ModConfigs/${mod.id}.json` }}</code><button class="garden-btn soft icon xs" title="打开配置目录" @click="$emit('action', { kind:'open', target:'config' })"><Icon name="folder" size="sm"/></button></div><div class="path"><span class="pk">原生数据</span><code>{{ mod.data_locations?.find(x=>x.kind==='native_data')?.path || `ModData/${mod.id}` }}</code><button class="garden-btn soft icon xs" title="打开数据目录" @click="$emit('action', { kind:'open', target:'data' })"><Icon name="folder" size="sm"/></button></div></div></div>
      </div>
</template>
