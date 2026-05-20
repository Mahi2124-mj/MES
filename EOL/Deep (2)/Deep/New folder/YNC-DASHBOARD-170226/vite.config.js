import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  root: '.',  // स्पष्ट रूप से रूट सेट करें
  publicDir: 'public',  // पब्लिक फोल्डर सेट करें
  server: {
    port: 5173,
    host: true,
    open: true
  },
  build: {
    outDir: 'dist',
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html')  // स्पष्ट इनपुट
      }
    }
  }
})