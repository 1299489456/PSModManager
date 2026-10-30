<script setup>
defineProps({ name: String, size: { type: String, default: '' } })
</script>
<template><svg class="i" :class="size" aria-hidden="true"><use :href="`#i-${name}`"/></svg></template>
