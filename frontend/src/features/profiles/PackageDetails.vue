<script setup>
import { computed, onMounted, ref } from 'vue'
import { File, X, ShieldCheck, CircleAlert } from 'lucide-vue-next'
const props = defineProps({ detail: { type: Object, required: true }, busy: Boolean })
const emit = defineEmits(['close', 'page'])
const drawer = ref(null)
const verified = computed(() => props.detail.integrity === 'verified')
const ownership = value => ({ exclusive: '独占', shared_identical: '共享', external: '外部文件', unknown: '归属待确认' })[value] || '归属待确认'
const folder = value => ({ mods: 'Mods', plugins: 'Plugins', user_libs: 'UserLibs', game_root: '游戏根目录' })[value] || value
const size = bytes => bytes < 1024 ? `${bytes} B` : bytes < 1024 ** 2 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`
onMounted(() => drawer.value.showModal())
</script>
<template>
  <dialog ref="drawer" class="package-drawer" data-testid="profile-package-details" aria-labelledby="package-detail-title" @cancel.prevent="emit('close')">
    <header><span>Mod 详情</span><button aria-label="关闭文件详情" @click="emit('close')"><X :size="20"/></button></header>
    <div class="drawer-content">
      <h2 id="package-detail-title">{{ detail.display_name || '未命名 Mod' }}</h2>
      <p class="version">版本 {{ detail.version_raw ?? '未知' }}</p>
      <div class="package-summary">
        <span><File :size="17"/>{{ detail.total }} 个文件</span>
        <span :class="verified ? 'verified' : 'failed'"><component :is="verified ? ShieldCheck : CircleAlert" :size="17"/>{{ verified ? '包库文件可用' : '文件校验失败' }}</span>
      </div>
      <h3>包含的文件</h3>
      <div class="detail-files">
        <article v-for="file in detail.files" :key="file.file_entry_id" data-testid="profile-detail-file">
          <div class="file-heading"><File :size="18"/><strong>{{ file.relative_path }}</strong><span class="file-size">{{ size(file.size) }}</span></div>
          <div class="file-meta"><span>{{ folder(file.target_root_kind) }}</span><span class="ownership-tag">{{ ownership(file.ownership) }}</span></div>
          <p v-if="file.conflicting_owner_releases?.length" class="failed">与 {{ file.conflicting_owner_releases.map(owner => owner.display_name).join('、') }} 的同名文件内容不同</p>
          <details v-if="file.shared_owner_releases?.length" class="additional"><summary>共享此文件的版本</summary><ul><li v-for="owner in file.shared_owner_releases" :key="owner.release_id">{{ owner.display_name }} · {{ owner.version_raw ?? '版本未知' }}</li></ul></details>
          <details class="additional"><summary>文件校验信息</summary><code>{{ file.source_hash }}</code><p>{{ file.lifecycle === 'immutable' ? '固定版本文件' : file.lifecycle }}</p></details>
        </article>
      </div>
      <nav v-if="detail.total > 20" aria-label="详情文件分页"><button data-testid="profile-detail-prev" :disabled="busy || detail.offset === 0" @click="emit('page', Math.max(0,detail.offset-20))">上一页</button><span>{{ detail.offset + 1 }}–{{ detail.offset + detail.files.length }} / {{ detail.total }}</span><button data-testid="profile-detail-next" :disabled="busy || detail.next_offset == null" @click="emit('page',detail.next_offset)">下一页</button></nav>
      <details class="advanced"><summary>版本标识与归属说明</summary><dl><dt>版本标识</dt><dd><code>{{ detail.release_id }}</code></dd><dt>清单校验值</dt><dd><code>{{ detail.manifest_hash }}</code></dd></dl><p v-for="warning in detail.warnings || []" :key="warning">{{ warning }}</p></details>
    </div>
    <footer>包库中的副本 · 未表示已应用到游戏</footer>
  </dialog>
</template>
<style scoped>
.package-drawer{position:fixed;inset:0 0 0 auto;margin:0;width:min(540px,100vw);max-width:100vw;height:100dvh;max-height:100dvh;padding:0;border:0;border-left:1px solid rgb(var(--rgb-border-base)/.2);border-radius:0;background:var(--bg-surface);color:var(--text-main);font-size:15px;line-height:1.6;box-shadow:-12px 0 36px #0003}
.package-drawer[open]{display:flex;flex-direction:column}.package-drawer::backdrop{background:#0004}header{display:flex;align-items:center;justify-content:space-between;padding:16px 24px;border-bottom:1px solid rgb(var(--rgb-border-base)/.16);font-weight:600}header button{width:36px;height:36px;padding:0}.drawer-content{padding:24px;overflow:auto;flex:1;min-height:0}.package-drawer h2{font-size:24px;line-height:1.4;font-weight:650;overflow-wrap:anywhere;margin:0}.version{font-size:16px;margin:8px 0 20px;color:var(--text-dim)}.package-summary{display:flex;gap:16px;flex-wrap:wrap;padding:14px 0;border-block:1px solid rgb(var(--rgb-border-base)/.16);margin-bottom:24px}.package-summary span{display:inline-flex;gap:7px;align-items:center}.verified{color:var(--accent-success)}.failed{color:var(--accent-danger);font-size:15px;margin:10px 0}.package-drawer h3{font-size:17px;margin:0 0 12px;font-weight:650}.detail-files article{padding:16px 0;border-bottom:1px solid rgb(var(--rgb-border-base)/.14)}.file-heading{display:flex;align-items:flex-start;gap:10px}.file-heading svg{margin-top:4px;color:var(--text-dim)}.file-heading strong{font-size:16px;font-weight:600;line-height:1.55;overflow-wrap:anywhere;flex:1;min-width:0;user-select:text}.file-size{font-size:14px;color:var(--text-dim);white-space:nowrap;margin-top:2px}.file-meta{display:flex;align-items:center;gap:12px;margin:8px 0 4px 28px;font-size:14px;color:var(--text-dim)}.ownership-tag{border:1px solid rgb(var(--rgb-border-base)/.22);padding:0 7px;border-radius:5px}.additional{font-size:14px;margin:10px 0 0 28px;color:var(--text-dim)}summary{cursor:pointer}summary:hover{color:var(--text-main)}ul{padding-left:20px;margin:8px 0}.advanced{margin-top:28px;border:1px solid rgb(var(--rgb-border-base)/.16);border-radius:8px;padding:12px 14px;font-size:14px;color:var(--text-dim)}.advanced p{margin-top:12px;line-height:1.7}dl{margin:12px 0}dt{font-weight:600;margin-top:10px}dd{margin:4px 0}.package-drawer code{font-size:13px;overflow-wrap:anywhere;user-select:text;margin-top:8px}nav{display:flex;align-items:center;justify-content:space-between;margin:18px 0;font-size:14px}footer{padding:14px 24px;border-top:1px solid rgb(var(--rgb-border-base)/.16);font-size:14px;color:var(--text-dim)}button{font:inherit;color:inherit;min-height:34px}button:focus-visible,summary:focus-visible{outline:2px solid var(--accent-primary);outline-offset:2px}
</style>
