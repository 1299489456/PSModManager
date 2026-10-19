<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ArrowLeft, CalendarClock, CloudDownload, Download, ExternalLink, FileArchive, Globe, KeyRound, Languages, Link2, Search, Sprout, ThumbsUp, X } from 'lucide-vue-next'
import { createNexusRuntime, VIEWS } from './nexusRuntime.js'
import BbcodeView from './BbcodeView.js'

const props = defineProps({ bridge: { type: Object, required: true }, active: Boolean })
const emit = defineEmits(['downloaded'])
const state = reactive({})
const runtime = createNexusRuntime(props.bridge, state, value => emit('downloaded', value))
const root = ref(null), sentinel = ref(null)
const configured = computed(() => Boolean(state.settings?.configured))
const accountLabel = computed(() => !state.settings ? '正在读取账户…' : !configured.value ? '未连接账户'
  : state.settings.account ? `${state.settings.account.name} · ${state.settings.account.premium ? 'Premium' : '普通账户'}` : '已连接')
const hasMore = computed(() => state.loaded && state.mods.length < state.total)
const detail = computed(() => state.detail)
const translation = computed(() => state.translation?.modId === detail.value?.mod_id ? state.translation : null)
const showTranslated = computed(() => Boolean(translation.value && state.showTranslation && (translation.value.pieces.description.length || translation.value.pieces.summary.length)))
const summaryText = computed(() => showTranslated.value && runtime.translated('summary') || detail.value?.summary || '')
// While a translation streams in, the translated sections replace the original as they arrive.
const descriptionText = computed(() => showTranslated.value && runtime.translated('description') || detail.value?.description || '')
const foreign = computed(() => {
  const text = `${detail.value?.summary || ''}\n${detail.value?.description || ''}`.replace(/\[[^\]]{0,200}\]|https?:\/\/\S+/g, ' ')
  const cjk = (text.match(/[㐀-鿿]/g) || []).length, latin = (text.match(/[A-Za-zÀ-ɏЀ-ӿ぀-ヿ]/g) || []).length
  return latin >= 12 && cjk < latin * 0.5 || latin >= 400
})
const fileLabels = { MAIN: '主文件', UPDATE: '更新', OPTIONAL: '可选', OLD_VERSION: '旧版本', MISCELLANEOUS: '其他' }
const ver = value => `v${String(value ?? '').trim().replace(/^v(?=\d)/i, '')}`
const number = value => value >= 10000 ? `${(value / 10000).toFixed(value >= 100000 ? 0 : 1)} 万` : String(value ?? 0)
const size = bytes => bytes == null ? '' : bytes < 1048576 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`
function ago(value) {
  const time = typeof value === 'number' ? value * 1000 : Date.parse(value)
  if (!Number.isFinite(time)) return ''
  const days = Math.floor((Date.now() - time) / 86400000)
  if (days < 1) { const hours = Math.floor((Date.now() - time) / 3600000); return hours < 1 ? '刚刚' : `${hours} 小时前` }
  return days < 30 ? `${days} 天前` : new Date(time).toLocaleDateString('zh-CN')
}
let searchTimer = 0, listScroll = 0, observer = null, opened = false
watch(() => state.query, () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { if (state.query.trim() !== state.appliedQuery && configured.value) void runtime.browse() }, 450) })
function searchNow() { clearTimeout(searchTimer); if (configured.value) void runtime.browse() }
function clearSearch() { state.query = ''; searchNow() }
async function openMod(mod) {
  listScroll = root.value?.scrollTop || 0
  void runtime.openMod(mod)
  await nextTick(); if (root.value) root.value.scrollTop = 0
}
async function back() {
  runtime.closeMod()
  await nextTick(); if (root.value) root.value.scrollTop = listScroll
}
function cardKey(event, mod) { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); void openMod(mod) } }
// Keep loading while the end of the grid is in view.
function watchEnd() {
  observer?.disconnect()
  if (!sentinel.value || !root.value) return
  observer = new IntersectionObserver(entries => {
    if (entries.some(entry => entry.isIntersecting) && hasMore.value && !state.listLoading && !state.listError && !state.detail) void runtime.more()
  }, { root: root.value, rootMargin: '0px 0px 600px 0px' })
  observer.observe(sentinel.value)
}
watch(() => [sentinel.value, state.mods.length, state.listLoading, state.detail], () => nextTick(watchEnd))
// The account is read the first time the page is shown; nothing is fetched from Nexus until the user asks.
watch(() => props.active, active => {
  if (active && !opened) { opened = true; void runtime.open() }
  if (!active && !state.busy) { state.apiKey = ''; state.nxmUrl = '' }
}, { immediate: true })
onMounted(watchEnd)
onUnmounted(() => { clearTimeout(searchTimer); observer?.disconnect(); void runtime.dispose() })
</script>

<template>
  <main ref="root" class="garden-nexus" data-testid="nexus-dialog" :aria-busy="state.busy || state.listLoading || state.detailLoading" aria-labelledby="nexus-title">
    <template v-if="!detail">
      <div class="garden-intro">
        <div><h1 id="nexus-title">Nexus</h1><p>{{ state.loaded ? `Probably Stolen 在 Nexus Mods 上${state.appliedQuery ? `有 ${state.total} 个 Mod 与“${state.appliedQuery}”有关` : `共有 ${state.total} 个 Mod`}，点开卡片查看详情、下载或翻译介绍。` : '浏览 Probably Stolen 在 Nexus Mods 上的全部 Mod，点开卡片查看详情、下载或翻译介绍。' }}</p></div>
        <div class="garden-intro-actions"><span class="account-pill" :class="{ ready: configured }"><i aria-hidden="true"></i>{{ accountLabel }}</span></div>
      </div>

      <details class="account garden-card" :open="state.settings && !configured" data-testid="nexus-account">
        <summary><KeyRound :size="17" aria-hidden="true"/><b>{{ configured ? 'Nexus 账户' : '连接你的 Nexus 账户' }}</b><span>{{ configured ? '更换或清除密钥' : '需要个人 API Key' }}</span></summary>
        <ol v-if="!configured" class="garden-stones steps"><li class="cur"><b>1</b>登录 Nexus 获取 Key</li><li><b>2</b>粘贴到下面</li><li><b>3</b>保存</li></ol>
        <p>密钥只用来访问 Nexus，不会发送给 AI。</p>
        <div class="key-row">
          <input v-model="state.apiKey" type="password" autocomplete="off" aria-label="Nexus 个人 API Key" placeholder="粘贴个人 API Key" :disabled="state.busy"/>
          <button class="garden-btn green" data-testid="nexus-save" :disabled="state.busy || !state.apiKey.trim()" @click="runtime.save">保存</button>
          <button class="garden-btn" data-testid="nexus-get-key" :disabled="state.busy" @click="runtime.openPage('keys')">登录并获取 Key<ExternalLink :size="13"/></button>
        </div>
        <div class="account-row">
          <label><input v-model="state.remember" type="checkbox" :disabled="state.busy"/>记住密钥（Windows 凭据）</label>
          <span class="spacer"></span>
          <button class="garden-link" :disabled="state.busy || !configured" @click="runtime.account">验证账户</button>
          <button class="garden-link danger" :disabled="state.busy || !configured" @click="runtime.clear">清除密钥</button>
        </div>
      </details>

      <div class="garden-toolbar">
        <div class="garden-chips" role="group" aria-label="排序">
          <button v-for="view in VIEWS" :key="view.value" :class="{ on: state.view === view.value }" :aria-pressed="state.view === view.value" :disabled="!configured" @click="runtime.setView(view.value)">{{ view.label }}</button>
        </div>
        <label class="garden-search" :class="{ active: state.query }"><Search :size="17" aria-hidden="true"/><input v-model="state.query" data-testid="nexus-search" type="search" aria-label="搜索 Nexus 上的 Mod" placeholder="搜索 Mod 名称" :disabled="!configured" @keydown.enter.prevent="searchNow"/><button v-if="state.query" type="button" class="search-clear" aria-label="清除搜索" @click="clearSearch"><X :size="15"/></button></label>
        <div class="garden-toolbar-end"><button class="garden-btn" data-testid="nexus-browse" :disabled="!configured || state.listLoading" @click="runtime.browse">{{ state.loaded ? '刷新' : '加载列表' }}</button></div>
      </div>

      <p v-if="state.busy" class="nexus-status" role="status" data-testid="nexus-progress"><Sprout :size="15" class="garden-seedling" aria-hidden="true"/>正在连接 Nexus…</p>
      <p v-else-if="state.notice" class="nexus-status" role="status">{{ state.notice }}</p>
      <p v-if="state.error" class="nexus-error" role="alert" data-testid="nexus-error">{{ state.error }}</p>

      <div v-if="state.mods.length || state.listLoading" class="nexus-grid" data-testid="nexus-grid">
        <article v-for="(mod, index) in state.mods" :key="mod.mod_id" class="nexus-card online-card" :style="{ '--i': index % 20 }" role="link" tabindex="0" :aria-label="`${mod.name}，查看详情`" data-testid="nexus-card" @click="openMod(mod)" @keydown="cardKey($event, mod)">
          <div class="cover">
            <img v-if="mod.thumbnail" :src="mod.thumbnail" alt="" loading="lazy" referrerpolicy="no-referrer"/>
            <span v-else class="cover-empty" aria-hidden="true"><Sprout :size="34"/></span>
            <span v-if="mod.category" class="cover-tag">{{ mod.category }}</span>
            <span v-if="mod.version" class="cover-version">{{ ver(mod.version) }}</span>
          </div>
          <div class="card-body">
            <h3>{{ mod.name }}</h3>
            <p class="byline"><img v-if="mod.avatar" :src="mod.avatar" alt="" loading="lazy" referrerpolicy="no-referrer"/><span>{{ mod.author || mod.uploader || '未知作者' }}</span></p>
            <p class="summary">{{ mod.summary }}</p>
          </div>
          <footer class="stats">
            <span title="下载次数"><Download :size="14" aria-hidden="true"/>{{ number(mod.downloads) }}</span>
            <span title="赞同数"><ThumbsUp :size="14" aria-hidden="true"/>{{ number(mod.endorsements) }}</span>
            <span class="when" :title="mod.updated">{{ ago(mod.updated) }}</span>
          </footer>
        </article>
        <template v-if="state.listLoading"><div v-for="n in (state.mods.length ? 4 : 8)" :key="`s${n}`" class="nexus-card skeleton" aria-hidden="true"><div class="cover"></div><div class="card-body"><i></i><i></i><i></i></div></div></template>
      </div>
      <div ref="sentinel" class="grid-end" aria-live="polite">
        <template v-if="state.listError"><span class="nexus-error inline">{{ state.listError }}</span><button class="garden-btn sm" @click="state.mods.length ? runtime.more() : runtime.browse()">重试</button></template>
        <span v-else-if="state.listLoading && state.mods.length">正在加载更多…</span>
        <button v-else-if="hasMore" class="garden-btn sm" @click="runtime.more">加载更多</button>
        <span v-else-if="state.loaded && state.mods.length">已显示全部 {{ state.mods.length }} 个 Mod</span>
      </div>
      <div v-if="!state.mods.length && !state.listLoading" class="garden-empty">
        <span class="garden-pot lg"><Globe :size="28"/></span>
        <template v-if="!configured"><b>先连接 Nexus 账户</b><span>在上方粘贴个人 API Key 后，就能浏览本游戏的全部 Mod。</span></template>
        <template v-else-if="state.loaded"><b>没有找到 Mod</b><span>没有名称包含“{{ state.appliedQuery }}”的 Mod，换个关键词试试。</span><button class="garden-link" @click="clearSearch">清除搜索</button></template>
        <template v-else><b>浏览 Nexus 上的 Mod</b><span>点击“加载列表”，或选择上方的排序方式开始浏览。</span><button class="garden-btn green" @click="runtime.browse">加载列表</button></template>
      </div>
    </template>

    <article v-else class="mod-page" data-testid="nexus-detail" :aria-busy="state.detailLoading">
      <button class="garden-btn soft sm back" data-testid="nexus-back" @click="back"><ArrowLeft :size="15"/>返回列表</button>
      <header class="hero" :style="detail.picture || detail.thumbnail ? { '--hero': `url(&quot;${detail.picture || detail.thumbnail}&quot;)` } : undefined">
        <div class="hero-image"><img v-if="detail.picture || detail.thumbnail" :src="detail.picture || detail.thumbnail" alt="" referrerpolicy="no-referrer"/><span v-else class="cover-empty" aria-hidden="true"><Sprout :size="44"/></span></div>
        <div class="hero-text">
          <p class="eyebrow"><span v-if="detail.category" class="tg on">{{ detail.category }}</span><span v-if="detail.version" class="tg off">{{ ver(detail.version) }}</span></p>
          <h1 id="nexus-title">{{ detail.name }}</h1>
          <p class="byline"><img v-if="detail.avatar" :src="detail.avatar" alt="" referrerpolicy="no-referrer"/><span>{{ detail.author || detail.uploader }}</span><span v-if="detail.uploader && detail.uploader !== detail.author" class="muted">· 上传者 {{ detail.uploader }}</span></p>
          <dl class="hero-stats">
            <div><dt>下载</dt><dd>{{ number(detail.downloads) }}</dd></div>
            <div><dt>赞同</dt><dd>{{ number(detail.endorsements) }}</dd></div>
            <div><dt>更新</dt><dd>{{ ago(detail.updated) || '—' }}</dd></div>
            <div v-if="detail.created"><dt>发布</dt><dd>{{ ago(detail.created) }}</dd></div>
          </dl>
          <p v-if="detail.tags?.length" class="tags"><span v-for="tag in detail.tags" :key="tag">{{ tag }}</span></p>
        </div>
      </header>

      <p v-if="state.detailError" class="nexus-error" role="alert">{{ state.detailError }} <button class="garden-link" @click="runtime.openMod(detail)">重试</button></p>
      <p v-if="state.error" class="nexus-error" role="alert" data-testid="nexus-error">{{ state.error }}</p>

      <div class="mod-columns">
        <section class="garden-card about" aria-labelledby="mod-about">
          <header class="about-head">
            <h2 id="mod-about">介绍</h2>
            <div v-if="detail.description != null" class="translate" data-testid="nexus-translate-bar">
              <template v-if="translation?.status === 'running'"><span class="working"><Languages :size="15" aria-hidden="true"/>AI 正在翻译{{ translation.total ? ` ${translation.done} / ${translation.total} 段` : '…' }}</span><button class="garden-link" @click="runtime.cancelTranslation">停止</button></template>
              <div v-if="translation && (translation.pieces.description.length || translation.pieces.summary.length)" class="segmented" role="group" aria-label="显示语言">
                <button :class="{ on: state.showTranslation }" :aria-pressed="state.showTranslation" @click="state.showTranslation = true">中文译文</button>
                <button :class="{ on: !state.showTranslation }" :aria-pressed="!state.showTranslation" @click="state.showTranslation = false">原文</button>
              </div>
              <button v-if="!translation || translation.status === 'error'" class="garden-btn sm" :class="{ green: foreign }" data-testid="nexus-translate" @click="runtime.translate"><Languages :size="15"/>{{ translation?.status === 'error' ? '重新翻译' : 'AI 翻译为中文' }}</button>
            </div>
          </header>
          <p v-if="translation?.status === 'error'" class="nexus-error inline">{{ translation.error }}</p>
          <p v-if="showTranslated" class="translated-note">以下为 AI 译文，专有名词保留原文；如有出入以原文为准。<template v-if="translation.truncated">介绍过长，只翻译了前面部分。</template></p>
          <p v-if="summaryText" class="lead">{{ summaryText }}</p>
          <div v-if="state.detailLoading && detail.description == null" class="loading-lines" aria-label="正在读取介绍"><i></i><i></i><i></i><i></i></div>
          <BbcodeView v-else-if="descriptionText" :source="descriptionText" :open="runtime.openLink" data-testid="nexus-description"/>
          <p v-else-if="!state.detailLoading" class="hint">作者没有填写介绍。</p>
          <p v-if="detail.description_truncated" class="hint">介绍较长，这里只显示前面部分。</p>
        </section>

        <aside class="mod-side">
          <section class="garden-card files" aria-labelledby="mod-files" data-testid="nexus-files">
            <h2 id="mod-files"><CloudDownload :size="17" aria-hidden="true"/>文件</h2>
            <p v-if="state.busy" class="working" role="status" data-testid="nexus-progress"><Sprout :size="15" class="garden-seedling" aria-hidden="true"/>{{ state.task?.stage === 'download' ? `正在下载${state.task.completed ? ` ${size(state.task.completed)}` : '…'}` : '正在连接 Nexus…' }}<button class="garden-link" @click="runtime.cancel">取消</button></p>
            <div v-if="detail.files == null" class="loading-lines short"><i></i><i></i></div>
            <article v-for="file in detail.files || []" :key="file.file_id" class="file-row">
              <span class="file-icon" aria-hidden="true"><FileArchive :size="18"/></span>
              <div><strong>{{ file.name }}</strong><small>{{ [fileLabels[file.category] || file.category, file.version && ver(file.version), size(file.size_bytes), file.uploaded && ago(file.uploaded)].filter(Boolean).join(' · ') }}</small></div>
              <button v-if="file.supported" class="garden-btn green sm primary" :disabled="state.busy || !configured || file.size_bytes > 67108864" :title="!configured ? '先连接 Nexus 账户' : file.size_bytes > 67108864 ? '超过 64 MB' : ''" @click="runtime.download(file)">下载</button>
              <span v-else class="tg off" :title="'PSMM 暂时只能直接导入 ZIP 或 DLL'">{{ (file.extension || '未知').toUpperCase() }} · 手动下载</span>
            </article>
            <p v-if="detail.files && !detail.files.length" class="hint">没有可下载的文件。</p>
            <details v-if="detail.files?.some(file => file.supported)" class="free-account">
              <summary>普通账户怎么下载？</summary>
              <p>Premium 账户可以直接下载。普通账户请在官网文件页点“使用管理器下载”，把得到的 nxm:// 链接粘贴到这里，再点“下载”。</p>
              <input v-model="state.nxmUrl" type="password" autocomplete="off" aria-label="Nexus 管理器下载链接" placeholder="nxm://probablystolen/mods/…/files/…?key=…" :disabled="state.busy"/>
              <button class="garden-link" @click="runtime.openPage('files', detail.mod_id)">打开官网文件页<ExternalLink :size="13"/></button>
            </details>
            <p v-if="detail.files?.some(file => !file.supported)" class="hint">7z、RAR 等压缩包请在官网下载并解压，再到模组页“导入”文件夹。</p>
          </section>

          <section v-if="detail.requirements?.length" class="garden-card requirements" aria-labelledby="mod-req">
            <h2 id="mod-req"><Link2 :size="17" aria-hidden="true"/>前置需求</h2>
            <ul><li v-for="item in detail.requirements" :key="item.name"><button v-if="item.url" class="garden-link" @click="runtime.openLink(item.url)">{{ item.name }}</button><span v-else>{{ item.name }}</span><small v-if="item.notes">{{ item.notes }}</small><span v-if="item.external" class="tg off">外部</span></li></ul>
          </section>

          <section class="garden-card facts" aria-label="基本信息">
            <dl class="garden-facts wide">
              <dt>Mod 编号</dt><dd>{{ detail.mod_id }}</dd>
              <dt>版本</dt><dd>{{ detail.version || '未标注' }}</dd>
              <dt>分类</dt><dd>{{ detail.category || '未分类' }}</dd>
              <dt><CalendarClock :size="14" aria-hidden="true"/> 更新</dt><dd>{{ detail.updated ? new Date(detail.updated).toLocaleString('zh-CN') : '—' }}</dd>
            </dl>
          </section>
        </aside>
      </div>

      <footer class="official" data-testid="nexus-official">
        <span>评论、图片、更新日志和作者的完整页面在 Nexus Mods 上。</span>
        <button class="garden-link" data-testid="nexus-official-link" @click="runtime.openPage('mod', detail.mod_id)">在 Nexus Mods 打开原页面<ExternalLink :size="13"/></button>
      </footer>
    </article>
  </main>
</template>

<style scoped>
.garden-nexus{position:relative;overflow:auto;padding:4px 12px 30px;animation:garden-fade-up .5s var(--g-ease)}
.account-pill{display:inline-flex;align-items:center;gap:8px;height:36px;padding:0 14px;border-radius:18px;border:1px solid #fde68a;background:#fffbeb;color:#92400e;font-size:13.5px;font-weight:600}
.account-pill i{width:8px;height:8px;border-radius:50%;background:#f59e0b}
.account-pill.ready{border-color:var(--g-line);background:#fff;color:var(--g-ink)}
.account-pill.ready i{background:var(--g-leaf-2);box-shadow:0 0 0 3px rgba(74,222,128,.25)}
.account{margin-bottom:14px;padding:12px 20px}
.account summary{display:flex;align-items:center;gap:10px;cursor:pointer;list-style:none;color:var(--g-ink)}
.account summary::-webkit-details-marker{display:none}
.account summary b{font-size:15px}
.account summary span{margin-left:auto;font-size:13px;color:var(--g-text-3)}
.account[open] summary{margin-bottom:12px}
.account>p{margin:0 0 10px;font-size:13.5px;color:var(--g-text-3)}
.steps{margin:4px 0 12px}
.key-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.key-row input,.free-account input{flex:1;min-width:220px;height:42px;padding:0 16px;border-radius:21px;border:1.5px solid var(--g-line-2);background:#fff;font:inherit;font-size:14px;color:var(--g-text)}
.account-row{display:flex;align-items:center;gap:16px;margin-top:12px;font-size:13.5px;color:var(--g-text-2)}
.account-row label{display:flex;align-items:center;gap:8px}
.account-row input{accent-color:var(--g-leaf-2)}
.account-row .spacer{flex:1}
.garden-link.danger{color:var(--g-rose)}
.nexus-status,.working{display:flex;align-items:center;gap:8px;margin:0 0 12px;font-size:13.5px;color:var(--g-leaf-3);font-weight:500}
.working .garden-link{margin-left:6px}
.nexus-error{margin:0 0 12px;padding:10px 14px;border-radius:16px;background:#fef2f2;border:1px solid #fecaca;color:#b91c1c;font-size:13.5px;overflow-wrap:anywhere}
.nexus-error.inline{display:inline-block;margin:0 8px 0 0;padding:6px 12px}

/* cards: cover first, like workshop tiles */
.nexus-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:18px}
.nexus-card{position:relative;display:flex;flex-direction:column;overflow:hidden;border-radius:22px;background:rgba(255,255,255,.94);border:1px solid var(--g-line);box-shadow:var(--g-sh);cursor:pointer;outline:none;transition:transform .45s var(--g-ease),box-shadow .4s,border-color .4s;animation:garden-fade-up .45s calc(var(--i,0) * 25ms) var(--g-ease) backwards}
.nexus-card:hover{transform:translateY(-4px);box-shadow:var(--g-sh-lg);border-color:#86efac}
.nexus-card:focus-visible{outline:3px solid rgba(34,197,94,.45);outline-offset:2px}
.cover{position:relative;aspect-ratio:16/9;background:linear-gradient(135deg,#dcfce7,#fef9c3);overflow:hidden}
.cover img{width:100%;height:100%;object-fit:cover;transition:transform .8s var(--g-ease)}
.nexus-card:hover .cover img{transform:scale(1.05)}
.cover-empty{position:absolute;inset:0;display:grid;place-items:center;color:var(--g-leaf-2);opacity:.6}
.cover-tag,.cover-version{position:absolute;top:10px;padding:3px 10px;border-radius:12px;font-size:11.5px;font-weight:700;backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px)}
.cover-tag{left:10px;background:rgba(20,83,45,.72);color:#ecfdf5}
.cover-version{right:10px;max-width:40%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;background:rgba(255,255,255,.85);color:var(--g-ink)}
.card-body{display:flex;flex-direction:column;gap:6px;padding:12px 14px 4px;min-height:0;flex:1}
.card-body h3{margin:0;font-size:15.5px;line-height:1.35;color:var(--g-text);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.byline{display:flex;align-items:center;gap:7px;margin:0;font-size:12.5px;color:var(--g-text-3);min-width:0}
.byline img{width:20px;height:20px;border-radius:50%;flex:none;background:#dcfce7}
.byline span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.summary{margin:2px 0 0;font-size:13px;line-height:1.6;color:var(--g-text-2);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.stats{display:flex;align-items:center;gap:14px;padding:10px 14px 12px;font-size:12.5px;color:var(--g-text-3)}
.stats span{display:inline-flex;align-items:center;gap:5px}
.stats .when{margin-left:auto}
.skeleton{cursor:default;animation:none}
.skeleton .cover,.skeleton i,.loading-lines i{background:linear-gradient(90deg,#ecfdf3 25%,#f7fef9 50%,#ecfdf3 75%);background-size:200% 100%;animation:nexus-shimmer 1.4s linear infinite}
.skeleton i{display:block;height:12px;border-radius:6px;margin:6px 0}
.skeleton i:first-child{width:70%;height:16px}.skeleton i:last-child{width:45%}
@keyframes nexus-shimmer{to{background-position:-200% 0}}
.grid-end{display:flex;align-items:center;justify-content:center;gap:10px;min-height:56px;margin-top:10px;font-size:13px;color:var(--g-text-3)}

/* detail page */
.mod-page{display:flex;flex-direction:column;gap:16px;animation:garden-fade-up .45s var(--g-ease)}
.back{align-self:flex-start}
.hero{position:relative;display:grid;grid-template-columns:minmax(260px,420px) minmax(0,1fr);gap:24px;padding:20px;border-radius:30px;overflow:hidden;border:1px solid var(--g-line);background:rgba(255,255,255,.92);box-shadow:var(--g-sh)}
.hero::before{content:"";position:absolute;inset:-40px;background:var(--hero, none) center/cover;filter:blur(40px) saturate(1.2);opacity:.28;pointer-events:none}
.hero>*{position:relative}
.hero-image{aspect-ratio:16/9;border-radius:20px;overflow:hidden;background:linear-gradient(135deg,#dcfce7,#fef9c3);box-shadow:0 14px 30px -18px rgba(20,83,45,.6)}
.hero-image img{width:100%;height:100%;object-fit:cover}
.hero-text{display:flex;flex-direction:column;gap:10px;min-width:0;justify-content:center}
.eyebrow{display:flex;gap:8px;margin:0}
.hero-text h1{margin:0;font-size:28px;font-weight:800;line-height:1.25;color:var(--g-ink);overflow-wrap:anywhere}
.hero-text .byline{font-size:14px}
.hero-text .byline img{width:26px;height:26px}
.muted{color:var(--g-text-3)}
.hero-stats{display:flex;flex-wrap:wrap;gap:10px;margin:4px 0 0}
.hero-stats div{min-width:84px;padding:8px 14px;border-radius:16px;background:rgba(240,253,244,.85);border:1px solid #dcfce7}
.hero-stats dt{font-size:12px;color:var(--g-text-3)}
.hero-stats dd{margin:2px 0 0;font-size:17px;font-weight:800;color:var(--g-ink)}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin:0}
.tags span{padding:3px 10px;border-radius:12px;background:#fff;border:1px solid var(--g-line);font-size:12px;color:var(--g-text-2)}
.mod-columns{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:16px;align-items:start}
.about{min-width:0;padding:20px 24px}
.about-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:10px}
.about-head h2,.mod-side h2{display:flex;align-items:center;gap:8px;margin:0;font-size:17px}
.translate{display:flex;align-items:center;gap:10px;margin-left:auto;flex-wrap:wrap}
.translate .working{margin:0}
.segmented{display:inline-flex;padding:3px;border-radius:18px;background:var(--g-mint);border:1px solid var(--g-line)}
.segmented button{height:28px;padding:0 12px;border:0;border-radius:14px;background:transparent;color:var(--g-text-2);font:inherit;font-size:12.5px;cursor:pointer}
.segmented button.on{background:#fff;color:var(--g-ink);font-weight:700;box-shadow:0 2px 6px -3px rgba(20,83,45,.4)}
.translated-note{margin:0 0 10px;padding:8px 12px;border-radius:12px;background:#fffbeb;border:1px solid #fde68a;color:#92400e;font-size:12.5px}
.lead{margin:0 0 14px;padding-left:14px;border-left:4px solid var(--g-leaf);font-size:15px;line-height:1.7;color:var(--g-text);font-weight:500}
.hint{margin:8px 0 0;font-size:13px;color:var(--g-text-3);line-height:1.6}
.loading-lines i{display:block;height:13px;margin:10px 0;border-radius:7px}
.loading-lines i:nth-child(2n){width:86%}.loading-lines.short i{height:30px;border-radius:12px}
.mod-side{display:flex;flex-direction:column;gap:14px;position:sticky;top:0}
.mod-side .garden-card{padding:16px 18px}
.mod-side h2{margin-bottom:10px}
.file-row{display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px dashed var(--g-line)}
.file-row:last-of-type{border-bottom:0}
.file-icon{flex:none;width:36px;height:36px;display:grid;place-items:center;border-radius:12px;background:#dcfce7;color:var(--g-leaf-3)}
.file-row>div{min-width:0;flex:1}
.file-row strong{display:block;font-size:14px;overflow-wrap:anywhere}
.file-row small{display:block;margin-top:2px;font-size:12px;color:var(--g-text-3)}
.file-row .tg{flex:none;font-size:11px}
.free-account{margin-top:10px;font-size:13px;color:var(--g-text-2)}
.free-account summary{cursor:pointer;color:var(--g-leaf-3);font-weight:600}
.free-account p{margin:8px 0;line-height:1.6;color:var(--g-text-3)}
.free-account input{width:100%;box-sizing:border-box;margin-bottom:8px}
.requirements ul{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:8px}
.requirements li{display:flex;flex-wrap:wrap;align-items:center;gap:6px;font-size:13.5px}
.requirements small{flex-basis:100%;color:var(--g-text-3)}
.facts dt{display:flex;align-items:center;gap:4px}
.official{display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:10px;padding:14px 18px;border-radius:20px;border:1px dashed var(--g-line-2);color:var(--g-text-3);font-size:13px}

/* BBCode content */
.about :deep(.bb-content){font-size:14.5px;line-height:1.8;color:var(--g-text);overflow-wrap:anywhere}
.about :deep(.bb-heading){margin:18px 0 6px;font-size:17px;color:var(--g-ink)}
.about :deep(.bb-list){margin:6px 0 10px;padding-left:22px}
.about :deep(.bb-list li){margin:3px 0}
.about :deep(.bb-list li::marker){color:var(--g-leaf-2)}
.about :deep(.bb-quote){margin:10px 0;padding:8px 14px;border-left:4px solid var(--g-line-2);background:var(--g-mint);border-radius:0 12px 12px 0;color:var(--g-text-2)}
.about :deep(.bb-code){margin:10px 0;padding:12px 14px;border-radius:12px;background:#f6f8f6;border:1px solid #e2ebe5;font:12.5px/1.6 Consolas,monospace;white-space:pre-wrap;overflow:auto}
.about :deep(.bb-line){height:1px;margin:18px 0;border:0;background:linear-gradient(90deg,transparent,var(--g-line-2),transparent)}
.about :deep(.bb-table){margin:10px 0;overflow:auto}
.about :deep(.bb-table table){border-collapse:collapse;min-width:60%;font-size:13.5px}
.about :deep(.bb-table th),.about :deep(.bb-table td){padding:7px 12px;border:1px solid #dcfce7;text-align:left;vertical-align:top}
.about :deep(.bb-table th){background:var(--g-mint);color:var(--g-ink)}
.about :deep(.bb-img){display:block;max-width:100%;max-height:520px;margin:10px auto;border-radius:14px}
.about :deep(.bb-link){color:var(--g-leaf-3);text-decoration:underline;text-decoration-color:rgba(34,197,94,.4);text-underline-offset:3px;cursor:pointer}
.about :deep(.bb-center){text-align:center}.about :deep(.bb-right){text-align:right}
.about :deep(.bb-u){text-decoration:underline}
.about :deep(.bb-spoiler){margin:8px 0;padding:8px 12px;border-radius:12px;border:1px solid var(--g-line)}
.about :deep(.bb-spoiler summary){cursor:pointer;color:var(--g-leaf-3);font-weight:600}

@media (max-width:1100px){.mod-columns{grid-template-columns:1fr}.mod-side{position:static}.hero{grid-template-columns:1fr}}
@media (prefers-reduced-motion:reduce){.nexus-card,.mod-page{animation:none}.nexus-card,.cover img{transition:none}.skeleton .cover,.skeleton i,.loading-lines i{animation:none}}
</style>
