<script setup>
// Explicit local UI benchmark. These generated rows never enter product services.
import { computed, nextTick, onUnmounted, reactive, ref } from 'vue'
import VirtualInventory from './VirtualInventory.vue'
import { indexEntries, filterEntries } from './controller.js'
import { applyTheme, themeChoices } from './theme.js'
const state = reactive({ entries: indexEntries(Array.from({length:5000}, (_, index) => ({ entry_id:`perf-${index}`, relative_path:`Mods/中文 模组 ${index}%.dll`, assembly_name:`Assembly${index}`, assembly_version:'1.0.0.0', presence:'present', tags:[], prediction:{status:index%2?'candidate':'excluded'} }))), query:'', selectedIds:[], focusedId:null })
const filtered = computed(() => filterEntries(state.entries,state.query))
const candidates = computed(() => filterEntries(state.entries,'','candidate'))
const busy = ref(false), result = ref(null)
function select(id) { state.selectedIds=[id];state.focusedId=id }
const painted = async () => { await nextTick();await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame) }
async function benchmark() {
  if(busy.value)return
  busy.value=true;result.value=null;window.__psmmUiBenchmark=null
  try {
    const samples=[];const input=document.querySelector('[data-testid=inventory-search]')
    const viewport=document.querySelector('[data-testid=inventory-list]')
    if(viewport.clientHeight<160)throw new Error('Benchmark viewport too short')
    for(let index=0;index<60;index++) {
      const query=index%4===0?'':index%4===1?'中文':index%4===2?`Assembly${index%10}`:`${index}%`
      const started=performance.now()
      input.value=query;input.dispatchEvent(new Event('input',{bubbles:true}))
      await painted()
      const elapsed=performance.now()-started
      const expected=filterEntries(state.entries,query).length
      const count=Number(document.querySelector('.inventory-column .count-pill').textContent)
      const rendered=document.querySelectorAll('[data-testid=inventory-row]').length
      if(count!==expected || rendered>50)throw new Error('Rendered result count or virtualization bound differs')
      samples.push({query,elapsed_ms:elapsed,result_count:count,rendered_rows:rendered})
    }
    input.value='';input.dispatchEvent(new Event('input',{bubbles:true}));await painted()
    viewport.scrollTop=viewport.scrollHeight;viewport.dispatchEvent(new Event('scroll'));await painted()
    const last=document.querySelector('[data-testid=inventory-row][data-entry-id="perf-4999"]')
    if(!last)throw new Error('Virtual last row is not reachable')
    last.click();await painted()
    if(!last.classList.contains('selected'))throw new Error('Visible row selection did not render')
    viewport.focus();viewport.dispatchEvent(new KeyboardEvent('keydown',{key:'Home',bubbles:true,cancelable:true}));await painted()
    if(state.focusedId!=='perf-0'||viewport.scrollTop!==0)throw new Error('Virtual keyboard navigation failed')
    const sorted=samples.map(item=>item.elapsed_ms).sort((a,b)=>a-b),p95=sorted[Math.ceil(sorted.length*.95)-1]
    result.value={status:p95<=200?'PASS':'FAIL',sample_count:5000,iterations:samples.length,p95_ms:p95,max_rendered_rows:Math.max(...samples.map(item=>item.rendered_rows)),samples,
      viewport:{width:innerWidth,height:innerHeight,devicePixelRatio,list_height:viewport.clientHeight},user_agent:navigator.userAgent,
      hardware_concurrency:navigator.hardwareConcurrency,scope:'Synthetic UI fixture; production VirtualInventory + filterEntries; DOM input to Vue commit and two animation frames. Not an inventory-service test.',scroll_and_keyboard:'PASS'}
  } catch(error) { result.value={status:'FAIL',error:String(error)} }
  finally { busy.value=false;window.__psmmUiBenchmark=JSON.parse(JSON.stringify(result.value)) }
}
onUnmounted(()=>{delete window.__psmmUiBenchmark})
</script>
<template>
  <div class="desktop-shell ui-performance" data-testid="ui-performance-fixture">
    <header class="topbar"><strong>界面性能测试</strong><span>5000 个合成文件 · 不读取游戏</span><select aria-label="性能测试主题" @change="applyTheme($event.target.value)"><option v-for="theme in themeChoices" :key="theme.id" :value="theme.id">{{theme.name}}</option></select><button data-testid="run-ui-performance" :disabled="busy" @click="benchmark">{{busy?'正在测试…':'开始测试'}}</button></header>
    <main class="performance-columns"><VirtualInventory :rows="filtered" :selected-ids="state.selectedIds" :focused-id="state.focusedId" :query="state.query" title="合成文件" @query="state.query=$event" @select="select" @all="state.selectedIds=filtered.map(row=>row.entry_id)"/><VirtualInventory :rows="candidates" :selected-ids="state.selectedIds" :focused-id="state.focusedId" title="合成候选" candidate @select="select" @all="state.selectedIds=candidates.map(row=>row.entry_id)"/></main>
    <footer class="statusbar" data-testid="ui-performance-result">{{result?`${result.status} · p95 ${result.p95_ms?.toFixed(2)}ms · 实际渲染最多 ${result.max_rendered_rows} 行`:'测试包含真实输入、页面更新、滚动和键盘操作；不代表游戏扫描验证。'}}</footer>
  </div>
</template>
