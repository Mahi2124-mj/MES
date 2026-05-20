import { createApp } from 'vue'
import App from './App.vue'
import './styles/main.css'

// Initialize service worker for PWA
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js')
      .then(registration => {
        console.log('✅ PWA Service Worker registered:', registration.scope)
      })
      .catch(error => {
        console.log('❌ Service Worker registration failed:', error)
      })
  })
}

createApp(App).mount('#app')