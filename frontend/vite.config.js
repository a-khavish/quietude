/*
 * Quietude - a personal assistant that runs on your own machine.
 * Copyright (C) 2026 Khavish Auckaloo
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// The dev server proxies the API to the Flask backend, so `npm run dev`
// gives hot reload against a live Quietude. In production Flask serves
// dist/ itself and there is no proxy - which is the only difference
// between the two paths.
const BACKEND = process.env.QUIETUDE_BACKEND || 'http://127.0.0.1:5000'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    // Quietude is loaded once from loopback, so a couple of extra requests
    // cost nothing and a readable build is worth more than shaving them.
    chunkSizeWarningLimit: 1200,
    assetsInlineLimit: 0,
  },
})
