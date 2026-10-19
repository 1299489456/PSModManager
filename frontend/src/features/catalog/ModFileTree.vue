<script setup>
import { File, Folder } from 'lucide-vue-next'
defineProps({ nodes: { type: Array, default: () => [] } })
const size = value => value == null ? '' : value < 1024 ? `${value} B` : value < 1048576 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1048576).toFixed(1)} MB`
</script>
<template>
  <ul class="file-tree">
    <li v-for="node in nodes" :key="node.path">
      <details v-if="!node.file" open><summary><Folder :size="15"/><span>{{ node.name }}</span></summary><ModFileTree :nodes="node.children"/></details>
      <div v-else class="tree-file" :title="node.path"><File :size="14"/><span>{{ node.name }}</span><small>{{ size(node.file.size) }}</small></div>
    </li>
  </ul>
</template>
<style scoped>
.file-tree{list-style:none;margin:0;padding:0;font-size:13px;line-height:1.6}.file-tree .file-tree{padding-left:16px;border-left:1px solid rgb(var(--rgb-border-base)/.12);margin-left:7px}summary,.tree-file{display:flex;align-items:center;gap:7px;min-height:30px;padding:3px 0}summary{cursor:pointer;list-style:none;color:var(--text-main)}summary::-webkit-details-marker{display:none}summary:before{content:'›';width:8px}details[open]>summary:before{content:'⌄'}svg{flex-shrink:0;color:var(--text-dim)}span{overflow-wrap:anywhere;min-width:0}small{margin-left:auto;font-size:12px;white-space:nowrap;color:var(--text-dim);padding-left:5px}.tree-file{padding-left:15px;color:var(--text-dim)}
</style>
