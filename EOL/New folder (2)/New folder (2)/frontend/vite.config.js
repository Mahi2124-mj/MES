import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5575,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5555',
        changeOrigin: true,
      },
      '/live_feed': {
        target: 'http://127.0.0.1:5555',
        changeOrigin: true,
        // MJPEG streams: disable response buffering so frames reach the browser in real-time
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            proxyRes.headers['cache-control'] = 'no-cache, no-store'
            proxyRes.headers['x-accel-buffering'] = 'no'
          })
        },
      },
      '/camera_frame': {
        target: 'http://127.0.0.1:5555',
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            proxyRes.headers['cache-control'] = 'no-cache, no-store'
          })
        },
      },
    },
  },
});
