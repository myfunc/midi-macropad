import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '10.0.0.27',
    port: 5173,
    proxy: {
      '/api': 'http://10.0.0.27:8741',
      '/ws': {
        target: 'ws://10.0.0.27:8741',
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
