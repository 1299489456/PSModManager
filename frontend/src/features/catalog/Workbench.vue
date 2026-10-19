<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { Package, Info, ListTree, ScanLine, ShieldCheck, Play, Save, X, FileText, CircleAlert, ChevronRight, RefreshCw, Layers, Link2 } from 'lucide-vue-next'
import VirtualInventory from './VirtualInventory.vue'
import SelectMenu from '../../shared/components/desktop/SelectMenu.vue'
import { createWorkbenchController, filterEntries } from './controller.js'
import { classifications, ruleTitles, sessionLabel, evidenceText, scanLabel, historicalReference } from '../diagnostics/presentation.js'
const props = defineProps({ bridge: Object, installation: Object })
const state = reactive({}), controller = createWorkbenchController(props.bridge, state)
const detailTab = ref('details'), workspace = ref(null), widths = ref([23, 27, 24, 26])
const allRows = computed(() => filterEntries(state.entries, state.query))
const candidates = computed(() => filterEntries(state.entries, '', 'candidate'))
const selected = computed(() => state.entries.find(row => row.entry_id === state.focusedId))
const analysis = computed(() => state.detail?.analysis || {})
const title = computed(() => analysis.value.melon_info?.name || selected.value?.assembly_name || '组件详情')
const sourceText = computed(() => evidenceText(state.evidenceParts))
const grid = computed(() => widths.value.map(value => `minmax(0,${value}fr)`).join(' 6px '))
const taskLabel = computed(() => scanLabel(state.task))
const matchedHistoryDates = computed(() => [...new Set(state.sessions.filter(session => !state.sessionId || session.session_id === state.sessionId).map(session => historicalReference(session)?.date).filter(Boolean))])
const parserLabels = {managed:'已识别程序集',native:'原生库',corrupt:'无法解析',hash_only:'仅完成文件校验',unknown:'未知',limit_exceeded:'文件超出读取限制'}
const roles = { melon_mod: 'Melon 组件', itemforge_content: 'ItemForge 内容包', characterforge_content: 'CharacterForge 内容包', unknown: '角色未知' }
const related = computed(() => state.graph.filter(edge => edge.from_id === state.focusedId || edge.to_id === state.focusedId))
watch(() => props.installation, value => { if (value) void controller.choose(value) }, { immediate: true })
let drag = null
function resize(index, event) { event.preventDefault(); drag = { index, start: event.clientX, widths: [...widths.value], width: workspace.value.getBoundingClientRect().width }; event.currentTarget.setPointerCapture(event.pointerId) }
function move(event) { if (!drag) return; const { index, start, width, widths: initial } = drag; const delta = (event.clientX - start) / width * 100; const bounded = Math.max(12 - initial[index], Math.min(initial[index + 1] - 12, delta)); widths.value = [...initial]; widths.value[index] += bounded; widths.value[index + 1] -= bounded }
function end() { if (drag) { drag = null; try { localStorage.setItem('psmm.workbench.columns', JSON.stringify(widths.value)) } catch {} } }
function resizeKey(index, event) { if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return; event.preventDefault(); const delta = event.key === 'ArrowRight' ? 1 : -1; if (widths.value[index] + delta >= 12 && widths.value[index + 1] - delta >= 12) { widths.value[index] += delta; widths.value[index + 1] -= delta } }
function keyboard(event) { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f' && !document.querySelector('dialog[open]')) { event.preventDefault(); workspace.value?.querySelector('[data-testid="inventory-search"]')?.focus() } }
onMounted(() => { try { const saved = JSON.parse(localStorage.getItem('psmm.workbench.columns')); if (Array.isArray(saved) && saved.length === 4 && saved.every(x => typeof x === 'number' && x >= 12 && x <= 64) && Math.abs(saved.reduce((a,b)=>a+b,0)-100)<1) widths.value = saved } catch {} window.addEventListener('keydown', keyboard) })
onUnmounted(() => { window.removeEventListener('keydown', keyboard); void controller.dispose() })
</script>
<template>
  <div class="workbench-wrap">
    <div class="scope-strip"><span><span class="status-dot"></span>{{ installation ? '游戏文件' : '请选择游戏安装' }}</span><span>从安装目录扫描 · 此页用于查看文件</span><span class="scope-generation">{{ state.generation ? `第 ${state.generation} 次扫描` : '尚未扫描' }}</span></div>
    <main ref="workspace" class="rim-workspace" :style="{ gridTemplateColumns: grid }" data-testid="readonly-workbench">
      <aside class="rim-panel details-column" data-testid="component-detail">
        <header class="column-heading"><span class="heading-marker"></span><strong>组件详情</strong><Info :size="14" class="dim"/></header>
        <div class="detail-scroll">
          <template v-if="selected">
            <div class="component-hero"><Package :size="42"/><span>PROBABLY STOLEN</span></div>
            <div class="component-title"><h2>{{ title }}</h2><span class="version-pill">{{ analysis.melon_info?.version_raw || selected.assembly_version || '版本未知' }}</span></div>
            <div class="role-chips"><span v-for="role in analysis.roles || ['unknown']" :key="role">{{ roles[role] || role }}</span></div>
            <div class="detail-tabs"><button :class="{active: detailTab === 'details'}" @click="detailTab = 'details'"><Info :size="13"/>详情</button><button :class="{active: detailTab === 'contents'}" @click="detailTab = 'contents'"><Layers :size="13"/>内容</button><button :class="{active: detailTab === 'graph'}" @click="detailTab = 'graph'"><Link2 :size="13"/>关系</button></div>
            <div v-if="detailTab === 'details'" class="detail-body">
              <section class="state-layers" data-testid="three-layer-state"><div><span>配置</span><strong>在“模组”页编辑启用项</strong></div><div><span>部署</span><strong>未托管 · 仅查看文件</strong></div><div><span>运行</span><strong>未知 · 尚未验证启动</strong></div></section>
              <div v-if="selected.prediction?.reasons?.includes('assembly_version_shadowed')" class="inline-note warning" data-testid="shadow-explanation">检测到同名的更高版本，预计会优先加载高版本，本文件不会直接加载。这不是文件损坏，也不表示应该删除；实际加载情况需查看游戏日志。</div>
              <div v-if="analysis.roles?.some(role => role.endsWith('_content'))" class="inline-note" data-testid="content-explanation">框架内容包：不需要普通模组标记。文件已识别，是否成功加载仍需查看运行日志。</div>
              <div v-if="analysis.resource_coverage?.status === 'partial'" class="inline-note" data-testid="resource-coverage-warning">嵌入资源仅部分读取，内容信息可能不完整。</div>
              <dl class="identity-fields"><dt>文件</dt><dd>{{ selected.relative_path }}</dd><dt>程序集</dt><dd>{{ selected.assembly_name || '未知' }}</dd><dt>程序集版本</dt><dd>{{ selected.assembly_version || '未知' }}</dd><dt>Melon 显示名</dt><dd>{{ analysis.melon_info?.name || '无声明 / 未知' }}</dd><dt>Melon 原始版本</dt><dd>{{ analysis.melon_info?.version_raw || '无声明 / 未知' }}</dd><dt>识别结果</dt><dd>{{ parserLabels[selected.status] || selected.status }} · {{ selected.presence === 'missing' ? '文件缺失' : '文件存在' }}</dd></dl>
              <details class="source-details"><summary>来源与文件指纹</summary><code>{{ selected.sha256 || '未取得完整哈希' }}</code><p>来源：扫描读取的文件信息；预计加载结果尚未经游戏运行验证。</p><pre>{{ selected.prediction }}</pre></details>
            </div>
            <div v-else-if="detailTab === 'contents'" class="detail-body"><p class="dim">内容清单 · {{ analysis.content_id_count || 0 }} 个内容声明</p><div v-for="manifest in analysis.manifests || []" :key="manifest.resource" class="manifest-item"><strong>{{ manifest.resource }}</strong><p>状态：{{ manifest.status }} · 所需接口版本：{{ manifest.required_runtime_api ?? '未知' }}</p><pre>{{ manifest.bundle_id }}</pre></div><pre v-for="(content,index) in state.contents" :key="index" class="content-item">{{ content }}</pre><button v-if="state.contentNext != null" @click="controller.contents">读取更多内容</button></div>
            <div v-else class="detail-body"><p class="dim">当前扫描中的逻辑关系。未知依赖不等于缺失。</p><div v-for="edge in related" :key="edge.relation_id || JSON.stringify(edge)" class="manifest-item"><strong>{{ edge.kind }}</strong><p>{{ edge.resolution || edge.confidence }}</p><details><summary>关系与来源</summary><pre>{{ edge }}</pre></details></div><p v-if="!related.length">当前关系页暂无该组件的关系。</p><button v-if="state.graphNext != null" @click="controller.graph(true)">加载下一页关系</button></div>
          </template>
          <div v-else class="column-empty details-empty"><Package :size="40"/><h3>未选择组件</h3><p>点击右侧文件查看身份、内容与来源</p><small>Ctrl / Shift 多选不会更改游戏文件</small></div>
        </div>
      </aside>
      <div class="column-resizer" role="separator" aria-label="调整详情列宽" aria-orientation="vertical" tabindex="0" @pointerdown="resize(0,$event)" @pointermove="move" @pointerup="end" @pointercancel="end" @keydown="resizeKey(0,$event)"></div>
      <VirtualInventory :rows="allRows" :selected-ids="state.selectedIds" :focused-id="state.focusedId" :query="state.query" title="全部文件" @query="state.query = $event" @select="controller.select" @all="controller.selectAll"/>
      <div class="column-resizer" role="separator" aria-label="调整库存列宽" aria-orientation="vertical" tabindex="0" @pointerdown="resize(1,$event)" @pointermove="move" @pointerup="end" @pointercancel="end" @keydown="resizeKey(1,$event)"></div>
      <VirtualInventory :rows="candidates" :selected-ids="state.selectedIds" :focused-id="state.focusedId" title="预计加载" candidate @select="controller.select" @all="controller.selectAll"/>
      <div class="column-resizer" role="separator" aria-label="调整问题列宽" aria-orientation="vertical" tabindex="0" @pointerdown="resize(2,$event)" @pointermove="move" @pointerup="end" @pointercancel="end" @keydown="resizeKey(2,$event)"></div>
      <aside class="rim-panel diagnostics-column">
        <header class="column-heading sidebar-tabs"><button data-testid="diagnostics-tab" :class="{active: state.tab === 'issues'}" @click="state.tab = 'issues'"><CircleAlert :size="13"/>问题</button><button :class="{active: state.tab === 'evidence'}" @click="state.tab = 'evidence'"><FileText :size="13"/>证据</button><button :class="{active: state.tab === 'sessions'}" @click="state.tab = 'sessions'; controller.refreshSources()"><ListTree :size="13"/>会话</button></header>
        <div class="sidebar-scroll">
          <template v-if="state.tab === 'issues'">
            <div class="diagnostic-scope"><SelectMenu aria-label="诊断会话范围" :model-value="state.sessionId" :options="[{value:'',label:'全部历史日志'},...state.sessions.map(session=>({value:session.session_id,label:sessionLabel(session)}))]" @update:model-value="controller.setSession"/><button data-testid="refresh-diagnostics" title="重新读取诊断" :disabled="state.diagnosticBusy || !installation" @click="controller.diagnostics()"><RefreshCw :size="14"/></button></div>
            <p v-if="matchedHistoryDates.length" class="coverage-note" data-testid="historical-reference" :data-date="matchedHistoryDates.join(',')" title="原日志 SHA256 与历史参考日志一致">{{ matchedHistoryDates.join('、') }} 历史日志，非本次启动。</p>
            <p class="coverage-note" v-if="state.diagnosticSummary">{{ state.diagnosticCoverage === 'complete' ? '已有记录已分析' : '部分记录未能分析' }} · 不代表游戏已正常运行</p>
            <article v-for="item in state.diagnostics" :key="item.diagnostic_id" :class="['diagnostic-item',item.classification]" data-testid="diagnostic-item"><div class="diagnostic-top"><span class="diagnostic-class">{{ classifications[item.classification] || item.classification }}</span><small>{{ item.evidence_ids?.length ? '历史会话' : '静态观察' }}</small></div><h3>{{ ruleTitles[item.rule_id] || item.rule_id }}</h3><p v-if="item.identities?.length">{{ item.identities.map(identity => identity.melon_name || identity.assembly_name || identity.relative_path).join(' · ') }}</p><button v-for="entryId in item.entry_ids || []" :key="entryId" class="text-button" @click="controller.select(entryId)">定位组件 <ChevronRight :size="12"/></button><div class="evidence-links"><button v-for="(evidenceId,index) in item.evidence_ids || []" :key="evidenceId" data-testid="diagnostic-evidence" :data-evidence-id="evidenceId" @click="controller.readEvidence(evidenceId)"><FileText :size="12"/>原证据 {{ index+1 }}</button></div><details class="source-details"><summary>事实、推断与待复现</summary><pre>{{ item.facts }}</pre><p v-for="line in item.inferences" :key="line">{{ line }}</p><p v-for="line in item.reproduction" :key="line">{{ line }}</p><small>规则 {{ item.rule_id }} · {{ item.rule_version }} · 不执行自动修复</small></details></article>
            <div v-if="!state.diagnostics.length" class="column-empty"><ShieldCheck :size="30"/><p>{{ state.diagnosticBusy ? '正在读取规则与证据…' : '暂无可解释的问题' }}</p><small>没有提示不代表兼容或运行验证通过</small></div><button v-if="state.diagnosticNext != null" :disabled="state.diagnosticBusy" @click="controller.diagnostics(true)">下一页问题</button>
          </template>
          <template v-else-if="state.tab === 'evidence'"><div v-if="state.evidence" class="evidence-view"><strong>原始日志</strong><p>字节窗口 {{ state.evidenceParts[0]?.window_start }}–{{ state.evidence.window_end }} / {{ state.evidence.total }} · 不跟随当前同名文件</p><pre data-testid="evidence-window">{{ sourceText }}</pre><button v-if="state.evidenceNext != null" :disabled="state.evidenceBusy" @click="controller.readEvidence(state.evidence.evidence_id,true)">读取下一窗口</button><details class="source-details"><summary>原始证据 ID 与校验值</summary><code>{{ state.evidence.evidence_id }}</code><code>{{ state.evidence.excerpt_hash }}</code><p>来源 {{ state.evidence.source_id }} · epoch {{ state.evidence.epoch }}</p><p>窗口边界可能截断字符；连续读取后按原始字节合并。</p></details></div><div v-else class="column-empty"><FileText :size="30"/><p>{{ state.evidenceBusy ? '正在读取日志…' : '请从问题列表打开相关日志' }}</p></div></template>
          <template v-else><div class="session-list"><h3>历史运行记录</h3><p class="dim">这些记录不代表当前游戏状态。</p><button v-for="session in state.sessions" :key="session.session_id" @click="controller.setSession(session.session_id);state.tab='issues'">{{ sessionLabel(session) }} <ChevronRight :size="12"/></button><h3>日志来源</h3><p v-for="source in state.sources" :key="source.source_id">{{ source.relative_path }} · {{ source.kind }}</p><details class="source-details"><summary>独立历史参考</summary><p>2026年9月24日参考样本：45 个 DLL、43 个历史直接加载项。该数字不作为当前目录的期望值，也不用于改写当前扫描结果。</p></details></div></template>
        </div>
        <div class="base-actions" :class="{'has-active-task':state.busy}"><div class="action-grid"><button data-testid="scan-inventory" :disabled="!installation || state.busy" @click="controller.scan(false)"><ScanLine :size="14"/>扫描</button><button :disabled="!installation || state.diagnosticBusy" @click="state.tab='issues'; controller.diagnostics()" class="check-action"><ShieldCheck :size="14"/>{{ state.diagnosticBusy ? '分析中…' : '分析日志' }}</button></div><div class="action-grid secondary-actions"><button v-if="state.busy" data-testid="cancel-task" :disabled="!state.task || state.task.state === 'cancelling'" @click="controller.cancel"><X :size="14"/>取消扫描</button><button v-else :disabled="!installation" @click="controller.scan(true)">强制重扫</button></div></div>
      </aside>
    </main>
    <div class="workbench-status" data-testid="task-status" :data-state="state.task?.state || 'idle'" :data-task-id="state.task?.task_id || ''" :data-scan-status="state.task?.data?.status || state.inventory?.last_attempt?.status || state.inventory?.status || ''" role="status"><span>{{ taskLabel }}</span><span v-if="state.task?.total != null">{{ state.task.completed }} / {{ state.task.total }}</span><progress v-if="state.busy" :max="state.task?.total || 1" :value="state.task?.total ? state.task.completed : undefined"></progress><span class="status-message">{{ state.loading ? '正在读取库存分页…' : state.notice }}</span></div>
    <div v-if="state.error" class="workbench-error" role="alert"><CircleAlert :size="14"/><span>{{ state.error }}</span><button aria-label="关闭错误提示" @click="state.error=''">×</button></div>
  </div>
</template>
