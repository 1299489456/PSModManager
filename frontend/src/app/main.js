import { createApp, h, onUnmounted, ref } from 'vue'
import App from './App.vue'
import PerformanceWorkbench from '../features/catalog/PerformanceWorkbench.vue'
import './desktop.css'
import './garden.css'
import { applyGardenTheme } from './gardenTheme.js'
import { installDialogDismiss } from './dialogDismiss.js'
applyGardenTheme()
installDialogDismiss()
createApp({setup(){
  const performanceMode=ref(location.hash==='#ui-performance-synthetic')
  const change=()=>{performanceMode.value=location.hash==='#ui-performance-synthetic'}
  window.addEventListener('hashchange',change)
  onUnmounted(()=>window.removeEventListener('hashchange',change))
  return()=>h(performanceMode.value?PerformanceWorkbench:App)
}}).mount('#app')
