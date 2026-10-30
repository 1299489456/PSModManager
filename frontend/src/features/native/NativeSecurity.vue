<script setup>
import { computed, reactive, ref, watch } from 'vue'
import Icon from './GardenIcon.vue'
import { securityLabel, securityReason, nativeScanLabel } from './securityRuntime.js'
import { severityOf, findingCounts, visibleFindings, filterFiles } from './securityOverview.js'
import { sizeText, dateText, cap } from './nativeModel.js'
const props=defineProps({mod:Object,item:Object,detail:Object,loading:Boolean,scanning:Boolean,writeAllowed:Boolean,dirty:Boolean,tab:{type:String,default:'summary'}})
const emit=defineEmits(['action','tab','ai'])
const PAGE=60
const label=computed(()=>securityLabel(props.item)),report=computed(()=>props.detail?.report)
const reasons=computed(()=>['unscanned','scanning'].includes(props.item?.state)?[]:[...new Set([...(props.item?.blocking_reasons||[]),...(props.item?.review_reasons||report.value?.blocking_reasons||[])])].filter(reason=>reason!=='SECURITY_HIGH_RISK'))
const kindNames={pe:'可执行格式（含内嵌资源）',media:'图片与音频',managed:'托管程序集',managed_pe:'托管程序集',native:'原生程序',native_pe:'原生程序',image:'图片',audio:'音频',archive:'归档',script:'脚本',manifest:'清单',json:'JSON 数据',data:'数据',other:'其他',unknown:'未识别'}
const total=computed(()=>report.value?.stats?.total_bytes||0)
const composition=computed(()=>Object.entries(report.value?.stats?.by_kind||{}).map(([kind,value])=>({kind,label:kindNames[kind]||kind,bytes:typeof value==='number'?value:value.bytes??value.total_bytes,count:typeof value==='object'?value.count??value.file_count:null})).sort((a,b)=>(b.bytes||0)-(a.bytes||0)))
const antivirus=computed(()=>report.value?.antivirus)
const avLabel=computed(()=>({not_scanned:'尚未运行',scanning:'扫描中',clean:'未检出威胁',completed:'扫描完成',detected:'检出威胁',threat_found:'检出威胁',unavailable:'引擎不可用',failed:'扫描失败',timeout:'扫描超时',incomplete:'扫描未完成'})[antivirus.value?.state]||antivirus.value?.state||'尚未运行')
const avTone=computed(()=>({clean:'on',detected:'bad',threat_found:'bad',failed:'pend',timeout:'pend',incomplete:'pend',unavailable:'pend'})[antivirus.value?.state]||'off')
const changed=computed(()=>report.value?.diff)
const pathOf=file=>typeof file==='string'?file:file.path
const coverage=computed(()=>report.value?.coverage)
const coverageText=computed(()=>({complete:'已完成',partial:'部分完成',incomplete:'未完成'})[coverage.value?.status]||coverage.value?.status||'未知')
const nativeScan=computed(()=>report.value?.native_scan),nativeLabel=computed(()=>nativeScanLabel(nativeScan.value))
const findings=computed(()=>report.value?.findings||[]),counts=computed(()=>findingCounts(findings.value))
const top=computed(()=>visibleFindings(findings.value).slice(0,3))
const diffCount=computed(()=>(changed.value?.added?.length||0)+(changed.value?.removed?.length||0)+(changed.value?.modified?.length||0))
// List state for big reports: filters, page limits and expanded evidence reset when the report changes.
const filter=reactive({level:'all',query:'',files:''}),limit=reactive({findings:PAGE,files:PAGE,added:PAGE,removed:PAGE,modified:PAGE}),open=ref(new Set()),evidenceAll=ref(new Set())
watch(()=>report.value?.report_id,()=>{Object.assign(filter,{level:'all',query:'',files:''});Object.assign(limit,{findings:PAGE,files:PAGE,added:PAGE,removed:PAGE,modified:PAGE});open.value=new Set();evidenceAll.value=new Set()})
const shownFindings=computed(()=>visibleFindings(findings.value,filter))
const files=computed(()=>filterFiles(report.value?.files||[],filter.files))
// Templates auto-unwrap refs, so toggling takes the ref by name here rather than as an argument.
const flip=(set,id)=>{const next=new Set(set.value);next.has(id)?next.delete(id):next.add(id);set.value=next}
const toggleOpen=id=>flip(open,id),toggleEvidence=id=>flip(evidenceAll,id)
function showFinding(id){filter.level='all';filter.query='';const next=new Set(open.value);next.add(id);open.value=next;emit('tab','findings')}
const action=kind=>({kind})
const pct=bytes=>total.value?Math.max(.5,Math.min(100,(bytes||0)/total.value*100)):0
</script>
<template>
  <div class="native-security" data-testid="mod-security-detail">
    <!-- 概要 -->
    <template v-if="tab==='summary'">
      <div class="security-conclusion" :class="label.tone" role="status">
        <div class="security-conclusion-head"><span class="tg" :class="label.tone"><Icon name="shield" size="xs"/>{{ label.label }}</span><small v-if="item?.scanned_at&&item?.state!=='scanning'">检查于 {{ dateText(item.scanned_at) }}</small></div>
        <p>{{ label.next }}</p>
        <p v-if="item?.state==='stale'" class="muted">下面保留的是旧内容报告，不适用于当前文件。</p>
        <ul v-if="reasons.length"><li v-for="(reason,i) in reasons" :key="i">{{ securityReason(reason) }}</li></ul>
      </div>
      <div v-if="loading&&!report" class="garden-empty" role="status">正在读取本地检查报告…</div>
      <div v-else-if="!report" class="security-empty muted">检查后这里会显示风险证据、内容组成、与上次的变化和检查覆盖范围。普通启用、保存和启动不需要先检查。</div>
      <template v-if="report">
        <div class="security-metrics">
          <button class="security-metric" :class="counts.high?'bad':counts.review?'pend':''" @click="emit('tab','findings')"><small>风险证据</small><b>{{ counts.all }}</b><span>{{ counts.high }} 高风险 · {{ counts.review }} 需复核 · {{ counts.hint }} 提示</span></button>
          <button class="security-metric" :class="coverage?.status&&coverage.status!=='complete'?'pend':''" @click="emit('tab','coverage')"><small>检查覆盖</small><b>{{ coverageText }}</b><span>文件 {{ coverage?.files_hashed??'?' }}/{{ coverage?.files_total??'?' }} · 程序集 {{ coverage?.pe_scanned??'?' }}/{{ coverage?.pe_total??'?' }}</span></button>
          <button class="security-metric" @click="emit('tab','content')"><small>内容</small><b>{{ sizeText(report.stats?.total_bytes) }}</b><span>{{ report.stats?.file_count ?? report.files?.length }} 个文件</span></button>
          <button class="security-metric" :class="diffCount?'sky':''" @click="emit('tab','diff')"><small>与上次相比</small><b>{{ changed?.baseline_report_id?diffCount?`${diffCount} 处变化`:'没有变化':'首次检查' }}</b><span v-if="changed?.baseline_report_id">{{ changed.byte_delta<0?'−':'+' }}{{ sizeText(Math.abs(changed.byte_delta||0)) }}</span><span v-else>还没有比较基线</span></button>
        </div>
        <div class="sec"><h3>最值得关注的证据<span class="sp"></span><button v-if="findings.length" class="garden-link" @click="emit('tab','findings')">全部 {{ findings.length }} 项 →</button></h3>
          <p v-if="!findings.length" class="muted">当前规则没有命中；请同时查看检查覆盖。</p>
          <button v-for="finding in top" :key="finding.id" class="security-finding-line" @click="showFinding(finding.id)"><span class="tg" :class="severityOf(finding.severity).tone">{{ severityOf(finding.severity).label }}</span><b>{{ finding.title }}</b><code>{{ finding.evidence?.[0]?.path }}<template v-if="finding.evidence?.[0]?.method"> · {{ finding.evidence[0].method }}</template></code></button>
        </div>
        <div v-if="nativeScan" class="sec" data-testid="security-native-scan"><h3>游戏原生检查 <span class="tg" :class="nativeLabel.tone">{{ nativeLabel.label }}</span></h3><p v-if="nativeScan.error" class="care-note">{{ securityReason(nativeScan.error) }}</p><p v-if="nativeScan.undeclared?.length" class="care-note">清单未声明的权限：{{ nativeScan.undeclared.map(value=>cap(value).label).join('、') }}。</p><dl class="garden-facts"><dt>已检查方法</dt><dd>{{ nativeScan.methods_scanned??'未知' }}</dd><dt>检测权限</dt><dd>{{ Array.isArray(nativeScan.capabilities)?nativeScan.capabilities.length?nativeScan.capabilities.map(value=>cap(value).label).join('、'):'未检出权限':'未取得结果' }}</dd></dl><details v-if="nativeScan.hard_flags?.length||nativeScan.findings?.length" class="security-tech"><summary>原生检查证据 · {{ (nativeScan.hard_flags?.length||0)+(nativeScan.findings?.length||0) }} 项</summary><div v-for="(flag,i) in [...(nativeScan.hard_flags||[]),...(nativeScan.findings||[])]" :key="i" class="security-evidence"><b>{{ flag.kind }}</b><code>{{ flag.location }}</code><p>{{ flag.target }}</p></div></details></div>
      </template>
      <div class="sec security-defender-row"><h3>Microsoft Defender <span class="tg" :class="avTone">{{ avLabel }}</span><small v-if="antivirus?.checked_at">{{ dateText(antivirus.checked_at) }}</small><span class="sp"></span><button data-testid="security-defender" class="garden-btn soft xs" :disabled="scanning" @click="emit('action',action('security-defender'))">无处置扫描…</button></h3><p v-if="antivirus?.reason||antivirus?.message" class="muted">{{ securityReason(antivirus.reason||antivirus.message) }}</p><div v-if="antivirus?.detections?.length" class="care-note"><p v-for="(detection,i) in antivirus.detections" :key="i">{{ securityReason(detection) }}</p></div></div>
      <div class="sec"><h3>处理</h3><p class="muted">{{ mod.source==='workshop'?'工坊隔离只停用并阻止管理器启动它，文件仍在 Steam 工坊，不取消订阅。':'本地隔离先备份，再把包移出游戏扫描范围。' }}恢复不会自动启用或批准。</p>
        <div class="note-act"><button data-testid="security-disable" class="garden-btn sm" :disabled="!writeAllowed||dirty||scanning" @click="emit('action',action('security-disable'))">停用并查看影响…</button><button data-testid="security-quarantine" class="garden-btn clay sm" :disabled="!writeAllowed||dirty||scanning" @click="emit('action',action('security-quarantine'))">{{ mod.source==='workshop'?'逻辑隔离…':'备份并隔离…' }}</button><button class="garden-link" @click="emit('action',action('security-records'))">处置与恢复记录</button></div>
        <p v-if="dirty" class="muted">先保存或放弃模组页的启用草稿，再执行处置。</p><p v-else-if="!writeAllowed" class="muted">当前不能写入：等待游戏退出、其他操作和状态核对完成。</p></div>
    </template>

    <p v-else-if="!report" class="security-empty muted">{{ loading?'正在读取本地检查报告…':'还没有这份内容的检查报告。' }}</p>

    <!-- 风险证据 -->
    <template v-else-if="tab==='findings'">
      <div class="security-subtools"><div class="garden-chips sm"><button v-for="[key,text] in [['all','全部'],['high','高风险'],['review','需复核'],['hint','提示']]" :key="key" :class="{on:filter.level===key,care:key==='high'}" @click="filter.level=key;limit.findings=PAGE">{{ text }}<span>{{ counts[key] }}</span></button></div><label class="garden-search"><Icon name="search" size="sm"/><input v-model="filter.query" placeholder="搜索标题、文件、方法" aria-label="搜索风险证据" @input="limit.findings=PAGE"/></label></div>
      <p v-if="!shownFindings.length" class="muted">{{ findings.length?'没有匹配的证据。':'当前规则没有命中；请同时查看检查覆盖。' }}</p>
      <article v-for="finding in shownFindings.slice(0,limit.findings)" :key="finding.id" class="security-finding" :class="{open:open.has(finding.id)}">
        <button class="security-finding-head" :aria-expanded="open.has(finding.id)" @click="toggleOpen(finding.id)"><span class="tg" :class="severityOf(finding.severity).tone">{{ severityOf(finding.severity).label }}</span><span class="security-finding-title"><b>{{ finding.title }}</b><code v-if="finding.evidence?.[0]">{{ finding.evidence[0].path }}<template v-if="finding.evidence[0].method"> · {{ finding.evidence[0].method }}</template></code></span><small>{{ finding.evidence?.length||0 }} 处</small><Icon :name="open.has(finding.id)?'up':'down'" size="xs"/></button>
        <template v-if="open.has(finding.id)">
          <p>{{ securityReason(finding.basis) }}</p>
          <div v-for="(evidence,i) in (evidenceAll.has(finding.id)?finding.evidence:finding.evidence?.slice(0,5))" :key="i" class="security-evidence"><code>{{ evidence.path }}<template v-if="evidence.method"> · {{ evidence.method }}</template><template v-if="evidence.offset!=null"> · IL_{{ Number(evidence.offset).toString(16).padStart(4,'0') }}</template></code><p v-if="evidence.detail">{{ evidence.detail }}</p></div>
          <div class="note-act"><button v-if="finding.evidence?.length>5" class="garden-link" @click="toggleEvidence(finding.id)">{{ evidenceAll.has(finding.id)?'收起':`显示全部 ${finding.evidence.length} 处` }}</button><span class="sp"></span><small class="muted">规则 {{ finding.rule }} · {{ finding.id }}</small><button class="garden-btn soft xs" @click="emit('ai',[finding.id])"><Icon name="sprout" size="xs"/>AI 解读这条</button></div>
        </template>
      </article>
      <button v-if="shownFindings.length>limit.findings" class="garden-btn soft sm security-more" @click="limit.findings+=PAGE">再显示 {{ Math.min(PAGE,shownFindings.length-limit.findings) }} 项（共 {{ shownFindings.length }}）</button>
    </template>

    <!-- 内容 -->
    <template v-else-if="tab==='content'">
      <div class="sec"><h3>内容组成 <small>{{ report.stats?.file_count ?? report.files?.length }} 个文件 · {{ sizeText(report.stats?.total_bytes) }}</small></h3>
        <div class="security-stack" aria-hidden="true"><i v-for="(part,i) in composition" :key="part.kind" :class="'k'+i%6" :style="{width:pct(part.bytes)+'%'}" :title="part.label"></i></div>
        <div class="security-composition"><div v-for="(part,i) in composition" :key="part.kind"><span><i :class="'k'+i%6"></i>{{ part.label }}<small v-if="part.count!=null"> · {{ part.count }} 个</small></span><b>{{ sizeText(part.bytes) }}</b></div></div>
        <p v-if="report.stats?.embedded_resource_bytes" class="muted">其中程序集内嵌资源 {{ sizeText(report.stats.embedded_resource_bytes) }}（{{ report.stats.resource_count??'?' }} 项），已计入对应 DLL，不重复累计。资源体积大不等于代码行为多。</p></div>
      <div v-if="report.stats?.method_il_bytes!=null" class="sec"><h3>程序集组成</h3><dl class="garden-facts"><dt>方法 IL</dt><dd>{{ sizeText(report.stats.method_il_bytes) }}</dd><dt>元数据</dt><dd>{{ sizeText(report.stats.metadata_bytes) }}</dd><dt>内嵌资源</dt><dd>{{ sizeText(report.stats.embedded_resource_bytes) }}</dd><dt v-if="report.stats.duplicate_files?.length">重复内容</dt><dd v-if="report.stats.duplicate_files?.length">{{ report.stats.duplicate_files.length }} 组相同文件</dd></dl></div>
      <div class="sec"><h3>全部文件 <small>{{ files.length }}</small><span class="sp"></span><label class="garden-search sm"><Icon name="search" size="sm"/><input v-model="filter.files" placeholder="筛选路径" aria-label="筛选文件" @input="limit.files=PAGE"/></label></h3>
        <div class="security-files"><div v-for="file in files.slice(0,limit.files)" :key="pathOf(file)" class="security-file"><code>{{ pathOf(file) }}</code><span>{{ sizeText(file.size??file.size_bytes) }}</span></div></div>
        <button v-if="files.length>limit.files" class="garden-btn soft sm security-more" @click="limit.files+=PAGE*4">再显示 {{ Math.min(PAGE*4,files.length-limit.files) }} 个（共 {{ files.length }}）</button></div>
    </template>

    <!-- 变化 -->
    <template v-else-if="tab==='diff'">
      <p v-if="!changed?.baseline_report_id" class="security-empty muted">{{ changed?.baseline_error?'上次的报告无法读取，这次没有可比较的基线。':'首次检查，还没有可比较的内容基线。下次检查时会显示新增、删除和修改的文件。' }}</p>
      <template v-else>
        <div class="security-diff-counts"><span class="tg on">新增 {{ changed.added?.length||0 }}</span><span class="tg off">删除 {{ changed.removed?.length||0 }}</span><span class="tg pend">修改 {{ changed.modified?.length||0 }}</span><span class="muted">总量 {{ changed.byte_delta<0?'−':'+' }}{{ sizeText(Math.abs(changed.byte_delta||0)) }} · {{ changed.unchanged_count||0 }} 个文件未变</span></div>
        <div v-for="[key,title] in [['modified','修改的文件'],['added','新增的文件'],['removed','删除的文件']]" v-show="changed[key]?.length" :key="key" class="sec"><h3>{{ title }} <small>{{ changed[key]?.length }}</small></h3>
          <div class="security-files"><div v-for="file in changed[key].slice(0,limit[key])" :key="pathOf(file)" class="security-file"><code>{{ pathOf(file) }}</code><span v-if="file.before_size!=null&&file.after_size!=null">{{ sizeText(file.before_size) }} → {{ sizeText(file.after_size) }}</span><span v-else-if="file.size!=null">{{ sizeText(file.size) }}</span></div></div>
          <button v-if="changed[key].length>limit[key]" class="garden-btn soft sm security-more" @click="limit[key]+=PAGE*4">再显示（共 {{ changed[key].length }}）</button></div>
      </template>
    </template>

    <!-- 覆盖 -->
    <template v-else-if="tab==='coverage'">
      <div class="sec"><h3>检查覆盖 <span class="tg" :class="coverage?.status==='complete'?'on':'pend'">{{ coverageText }}</span></h3><dl class="garden-facts"><dt>完整读取</dt><dd>{{ coverage?.files_hashed??'未知' }} / {{ coverage?.files_total??'未知' }} 个文件</dd><dt>静态解析</dt><dd>{{ coverage?.pe_scanned??'未知' }} / {{ coverage?.pe_total??'未知' }} 个程序集</dd><dt>规则版本</dt><dd>{{ report.rule_version||'未知' }}</dd></dl>
        <ul v-if="coverage?.gaps?.length" class="security-gaps"><li v-for="(gap,i) in coverage.gaps" :key="i">{{ securityReason(gap) }}</li></ul></div>
      <div v-if="coverage?.limitations?.length" class="sec"><h3>这次检查不能说明什么</h3><ul class="security-gaps"><li v-for="limit_ in coverage.limitations" :key="limit_">{{ securityReason(limit_) }}</li></ul></div>
      <div v-if="report.assemblies?.length" class="sec"><h3>逐个程序集 <small>{{ report.assemblies.length }}</small></h3><div class="security-files"><div v-for="assembly in report.assemblies" :key="assembly.path" class="security-file"><code>{{ assembly.path }}</code><span :class="{warn:assembly.error}">{{ assembly.error?securityReason(assembly.error):({managed:'托管解析完成',native:'原生格式，未分析行为',hash_only:'仅读取指纹'})[assembly.status]||assembly.status }}</span></div></div></div>
      <details class="security-tech"><summary>内容指纹</summary><p class="muted">用于把报告绑定到这次的文件内容，不代表安全认证。</p><code class="security-hash">{{ item?.content_hash }}</code></details>
    </template>
  </div>
</template>
