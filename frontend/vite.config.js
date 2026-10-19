import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig({
  base: './', plugins: [vue()], publicDir: false,
  build: { copyPublicDir: false, modulePreload: false, cssCodeSplit: false,
    rollupOptions: { output: { inlineDynamicImports: true } },
  },
})
