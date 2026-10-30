<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import Icon from './GardenIcon.vue'
import { safeImage } from './nativeModel.js'
import { workshopImageCache } from './workshopImageCache.js'
const props = defineProps({ src:String, fallback:String, title:{type:String,default:'模组封面'}, loading:Boolean, zoom:Boolean, lazy:Boolean, bridge:Object, workshopId:String, refreshKey:[String,Number] })
const emit = defineEmits(['open'])
const failed=ref([]),loaded=ref(false)
const cached=ref('')
let epoch=0,timer=null,disposed=false
watch(()=>[props.workshopId,props.src,props.fallback,props.refreshKey],async([id,src])=>{const token=++epoch;clearTimeout(timer);cached.value='';if(!id||!props.bridge||src?.startsWith('data:'))return;const cache=workshopImageCache(props.bridge),version=JSON.stringify([props.src||'',props.fallback||'',props.refreshKey||'']);cached.value=cache.peek(id,version)?.data_url||'';let attempts=0;async function read(){try{const result=await cache.load(id,version);if(disposed||token!==epoch)return;if(result.data_url)cached.value=result.data_url;else if(result.state==='loading'&&++attempts<30)timer=setTimeout(read,1000)}catch{}}await read()},{immediate:true})
onUnmounted(()=>{disposed=true;++epoch;clearTimeout(timer)})
const candidates=computed(()=>[...new Set([props.src?.startsWith('data:')?props.src:cached.value,cached.value,props.src,props.fallback].map(safeImage).filter(Boolean))])
const current=computed(()=>candidates.value.find(url=>!failed.value.includes(url)))
watch(()=>[props.src,props.fallback],()=>{failed.value=[];loaded.value=false})
watch(current,()=>{loaded.value=false})
function reject(){if(current.value)failed.value.push(current.value)}
</script>
<template>
  <component :is="zoom?'button':'div'" class="cover native-cover" data-testid="mod-cover" :class="{zoomable:zoom&&current}" :aria-label="zoom?`查看完整封面：${title}`:undefined" :title="zoom&&current?'查看完整封面':undefined" :type="zoom?'button':undefined" @click="zoom&&current&&emit('open',current)">
    <img v-if="current" :key="current" :src="current" :alt="title" :loading="lazy?'lazy':'eager'" :class="{ready:loaded}" @load="loaded=true" @error="reject"/>
    <span v-if="!current||!loaded" class="cover-placeholder"><Icon name="sprout" size="lg"/><small>{{ current||loading?'封面加载中…':failed.length?'封面暂不可用':'暂无封面' }}</small></span>
    <span v-if="zoom&&current&&loaded" class="cover-zoom"><Icon name="expand" size="sm"/></span>
  </component>
</template>
