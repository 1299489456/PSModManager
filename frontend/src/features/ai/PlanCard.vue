<script setup>
import { diagnosticMarkdown } from './diagnosticMarkdown.js'
import '../../shared/lib/richText.css'
defineProps({ suggestion: { type: Object, required: true }, busy: Boolean, active: Boolean, cancelling: Boolean })
defineEmits(['execute', 'cancel'])
</script>

<template>
  <article class="suggestion" data-testid="ai-suggestion" :data-status="suggestion.status" :data-suggestion-id="suggestion.suggestion_id" :data-release-id="suggestion.release_id" :data-action="suggestion.action">
    <strong data-testid="ai-suggestion-name">{{ suggestion.display_name || '未命名 Mod' }}</strong>
    <small>版本 {{ suggestion.version_raw ?? '未知' }}{{ suggestion.kind === 'native_mod' ? ' · 原生模组' : ' · 旧版建议' }}</small>
    <p class="impact">{{ suggestion.kind === 'native_mod' ? '打开模组详情核对，再由你确认启停与保存。不会代为批准。' : '旧版操作不再执行，请在原生模组列表重新核对。' }}</p>
    <div class="rich-text" data-testid="ai-suggestion-reason" v-html="diagnosticMarkdown(suggestion.reason)"></div>
    <div class="actions"><button data-testid="ai-suggestion-action" :disabled="busy || suggestion.kind !== 'native_mod'" @click="$emit('execute', suggestion.suggestion_id)">在模组列表中查看 / 确认</button><span v-if="active" role="status" data-testid="ai-suggestion-progress">正在核对模组…</span><button v-if="active" data-testid="ai-action-cancel" :disabled="cancelling" @click="$emit('cancel')">取消</button></div>
    <p v-if="suggestion.result" class="result" data-testid="ai-action-notice" role="status">{{ suggestion.result }}</p>
    <p v-if="suggestion.error" class="error" data-testid="ai-action-error" role="alert">{{ suggestion.error }}</p>
  </article>
</template>

<style scoped>
.suggestion { border:1px solid rgb(var(--rgb-border-base)/.22); border-radius:9px; padding:16px; margin:12px 0; }.suggestion strong { display:block; font-size:16px; }.suggestion small { display:block; color:rgb(var(--rgb-text-dim)); margin-top:6px; }.suggestion p { white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.65; }.actions { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }button { color:inherit; font:inherit; border:1px solid rgb(var(--rgb-accent-primary)/.45); border-radius:8px; background:rgb(var(--rgb-accent-primary)/.12); padding:8px 12px; cursor:pointer; }button:disabled { opacity:.5; cursor:not-allowed; }button:focus-visible { outline:2px solid rgb(var(--rgb-accent-primary)); outline-offset:2px; }.result { color:rgb(var(--rgb-accent-success)); }.error { color:rgb(var(--rgb-accent-danger)); }
</style>
