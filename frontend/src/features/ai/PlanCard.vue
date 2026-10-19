<script setup>
import { modActionText } from '../catalog/modManagerRuntime.js'
defineProps({ suggestion: { type: Object, required: true }, busy: Boolean, active: Boolean, cancelling: Boolean })
defineEmits(['execute', 'cancel'])
</script>

<template>
  <article class="suggestion" data-testid="ai-suggestion" :data-status="suggestion.status" :data-suggestion-id="suggestion.suggestion_id" :data-release-id="suggestion.release_id" :data-action="suggestion.action">
    <strong data-testid="ai-suggestion-name">{{ suggestion.display_name || '未命名 Mod' }}</strong>
    <small>版本 {{ suggestion.version_raw ?? '未知' }}{{ suggestion.kind === 'workspace' ? ' · 当前 Mod 列表' : ' · 包库版本' }}</small>
    <p class="impact">{{ suggestion.action === 'disable' ? '停用后下次启动游戏不再加载；恢复副本保留，可重新启用。' : suggestion.action === 'install' ? '将此版本安装并启用到当前游戏。' : '将此 Mod 加入当前游戏的加载列表。' }}</p>
    <p data-testid="ai-suggestion-reason">{{ suggestion.reason }}</p>
    <div class="actions"><button data-testid="ai-suggestion-action" :disabled="busy || suggestion.status === 'completed'" @click="$emit('execute', suggestion.suggestion_id)">{{ suggestion.status === 'completed' ? '已完成' : modActionText(suggestion.action) }}</button><span v-if="active" role="status" data-testid="ai-suggestion-progress">{{ cancelling ? '正在取消，请等待文件状态核对…' : '正在核对并执行…' }}</span><button v-if="active" data-testid="ai-action-cancel" :disabled="cancelling" @click="$emit('cancel')">取消</button></div>
    <p v-if="suggestion.result" class="result" data-testid="ai-action-notice" role="status">{{ suggestion.result }}</p>
    <p v-if="suggestion.error" class="error" data-testid="ai-action-error" role="alert">{{ suggestion.error }}</p>
  </article>
</template>

<style scoped>
.suggestion { border:1px solid rgb(var(--rgb-border-base)/.22); border-radius:9px; padding:16px; margin:12px 0; }.suggestion strong { display:block; font-size:16px; }.suggestion small { display:block; color:rgb(var(--rgb-text-dim)); margin-top:6px; }.suggestion p { white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.65; }.actions { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }button { color:inherit; font:inherit; border:1px solid rgb(var(--rgb-accent-primary)/.45); border-radius:8px; background:rgb(var(--rgb-accent-primary)/.12); padding:8px 12px; cursor:pointer; }button:disabled { opacity:.5; cursor:not-allowed; }button:focus-visible { outline:2px solid rgb(var(--rgb-accent-primary)); outline-offset:2px; }.result { color:rgb(var(--rgb-accent-success)); }.error { color:rgb(var(--rgb-accent-danger)); }
</style>
