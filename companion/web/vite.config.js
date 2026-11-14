import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// Builds into the Python package so `orbit-notes serve` serves it without Node.
export default defineConfig({
  plugins: [svelte()],
  base: '/ui/',
  build: { outDir: '../src/orbit_notes/web', emptyOutDir: true },
  // `npm run dev`: proxy the API to a running `orbit-notes serve`.
  server: { proxy: { '/ui/api': 'http://localhost:8765' } },
})
