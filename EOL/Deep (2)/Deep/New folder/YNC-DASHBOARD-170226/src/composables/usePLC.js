import { ref, onMounted, onUnmounted } from 'vue'

export function usePLC() {
  const plcConnected = ref(false)
  const plcData = ref({})
  const plcStatus = ref('disconnected')

  // PLC configuration
  const config = {
    pollingInterval: 2000,
    reconnectInterval: 1000
  }

  // Simulate PLC data
  const simulatePLCData = () => {
    return {
      D6000: Math.floor(Math.random() * 1000), // Production count
      status: 'running', // running, breakdown, quality, etc.
      cycleTime: 15.6 + (Math.random() * 2 - 1), // 14.6 to 16.6
      timestamp: new Date().toISOString()
    }
  }

  // Connect to PLC
  const connect = () => {
    console.log('🔌 Connecting to PLC...')
    plcStatus.value = 'connecting'
    
    // Simulate connection delay
    setTimeout(() => {
      plcConnected.value = true
      plcStatus.value = 'connected'
      console.log('✅ PLC Connected')
      
      // Start polling
      startPolling()
    }, 1000)
  }

  // Disconnect from PLC
  const disconnect = () => {
    console.log('🔌 Disconnecting from PLC...')
    plcConnected.value = false
    plcStatus.value = 'disconnected'
    stopPolling()
  }

  // Poll PLC for data
  const pollPLC = () => {
    if (!plcConnected.value) return
    
    const data = simulatePLCData()
    plcData.value = data
    
    // Emit event for other components
    window.dispatchEvent(new CustomEvent('plc-data-update', {
      detail: data
    }))
  }

  // Start polling
  const startPolling = () => {
    if (window.plcPollInterval) {
      clearInterval(window.plcPollInterval)
    }
    
    window.plcPollInterval = setInterval(pollPLC, config.pollingInterval)
    pollPLC() // Initial poll
  }

  // Stop polling
  const stopPolling = () => {
    if (window.plcPollInterval) {
      clearInterval(window.plcPollInterval)
      window.plcPollInterval = null
    }
  }

  // Get PLC state
  const getState = () => {
    return {
      connected: plcConnected.value,
      status: plcStatus.value,
      data: plcData.value
    }
  }

  // Send command to PLC
  const sendCommand = (command, value) => {
    if (!plcConnected.value) {
      console.error('❌ PLC not connected')
      return false
    }
    
    console.log(`📤 Sending command to PLC: ${command} = ${value}`)
    
    // Simulate command sending
    return new Promise((resolve) => {
      setTimeout(() => {
        console.log(`✅ Command executed: ${command} = ${value}`)
        resolve(true)
      }, 500)
    })
  }

  // Initialize
  onMounted(() => {
    connect()
    
    onUnmounted(() => {
      disconnect()
    })
  })

  return {
    plcConnected,
    plcData,
    plcStatus,
    connect,
    disconnect,
    getState,
    sendCommand
  }
}






