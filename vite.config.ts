import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath } from 'node:url'

const srcDir = fileURLToPath(new URL('./src', import.meta.url))

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': srcDir } },
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    watch: {
      ignored: ['**/data/**', '**/audio/**', '**/backend/**', '**/outputs/**', '**/test_results/**'],
    },
  },
  preview: {
    host: '0.0.0.0',
    port: 4173,
  },
})
