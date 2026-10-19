import { createRequire } from 'node:module'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
const [major, minor] = process.versions.node.split('.').map(Number)
if (!((major === 20 && minor >= 19) || (major === 22 && minor >= 12) || major > 22)) {
  console.error(`Node ${process.versions.node} 不满足 Vite 7 要求；需要 Node 20.19+ 或 22.12+。不会自动安装。`)
  process.exit(1)
}
const require = createRequire(import.meta.url)
for (const name of ['vite', 'vue', '@vitejs/plugin-vue']) {
  const packagePath = fileURLToPath(new URL(`../../node_modules/${name}/package.json`, import.meta.url))
  if (!existsSync(packagePath)) {
    console.error(`缺少本地依赖 ${name}，请先准备 frontend/node_modules；不会自动安装。`)
    process.exit(1)
  }
  require.resolve(name)
}
console.log(`环境检查通过：Node ${process.versions.node}；3 个本地构建依赖可用；未安装或同步依赖。`)
