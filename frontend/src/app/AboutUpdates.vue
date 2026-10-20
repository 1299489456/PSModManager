<script setup>
import { History } from 'lucide-vue-next'
import notes from '../../../docs/user/release-notes.md?raw'
import { releaseNotes } from './releaseNotes.js'
import '../shared/lib/richText.css'

defineProps({ version: { type: String, default: '' } })
const releases = releaseNotes(notes)
</script>

<template>
  <section id="info-updates-panel" class="updates" role="tabpanel" aria-labelledby="info-updates-tab" data-testid="about-updates">
    <header class="updates-heading"><h2><History :size="20" aria-hidden="true"/>更新记录</h2><span>已安装 v{{ version }}</span></header>
    <template v-for="release in releases" :key="release.version">
      <article v-if="release.version === version" class="garden-card current-release" data-testid="release-current">
        <header class="release-heading"><h3>v{{ release.version }}</h3><span class="release-date">{{ release.label }}</span><span class="tg on">当前版本</span></header>
        <div class="rich-text release-content" v-html="release.html"></div>
      </article>
      <details v-else class="garden-card past-release" :data-version="release.version" data-testid="release-history">
        <summary><span class="release-version">v{{ release.version }}</span><span class="release-date">{{ release.label }}</span></summary>
        <div class="rich-text release-content" v-html="release.html"></div>
      </details>
    </template>
  </section>
</template>

<style scoped>
.updates{display:flex;flex-direction:column;gap:16px;max-width:1000px}
.updates-heading,.release-heading{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.updates-heading{justify-content:space-between;padding:0 4px}
.updates-heading h2{display:flex;align-items:center;gap:9px;margin:0;font-size:19px;color:var(--g-ink)}
.updates-heading>span,.release-date{font-size:13px;color:var(--g-text-3)}
.release-heading h3,.release-version{margin:0;font-size:18px;font-weight:700;color:var(--g-ink);font-variant-numeric:tabular-nums}
.release-heading .tg{margin-left:auto}
.current-release{border-color:var(--g-leaf-2)}
.release-content{font-size:14px;color:var(--g-text-2);margin-top:18px}
.release-content :deep(h2){font-size:15px;margin-top:20px}
.release-content :deep(:not(pre)>code){display:inline}
.past-release>summary{cursor:pointer;color:var(--g-leaf-3)}
.past-release>summary .release-date{margin-left:14px}
.past-release[open]>summary{padding-bottom:2px}
</style>
