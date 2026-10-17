// Builds the AI Elements island into the app's static files: app/static/ui/luna-ui.js (an ES module) and luna-ui.css.
// The app loads both from index.html; app/static/js/app.js talks to it through window.LunaUI (src/luna-ui.tsx).
import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(import.meta.dirname, './src') } },
  base: '/static/ui/',
  build: {
    outDir: path.resolve(import.meta.dirname, '../app/static/ui'),
    emptyOutDir: true,
    cssCodeSplit: false,
    rollupOptions: {
      input: path.resolve(import.meta.dirname, 'src/luna-ui.tsx'),
      output: { entryFileNames: 'luna-ui.js', chunkFileNames: 'chunks/[name]-[hash].js', assetFileNames: 'luna-ui[extname]' },
    },
  },
})
