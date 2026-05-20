import { ref, onMounted, onUnmounted } from 'vue'

export function useDateTime() {
  const currentDate = ref('')
  const currentTime = ref('')

  function updateDateTime() {
    const now = new Date()
    
    // Format date as DD/MM/YYYY
    const day = now.getDate().toString().padStart(2, '0')
    const month = (now.getMonth() + 1).toString().padStart(2, '0')
    const year = now.getFullYear()
    currentDate.value = `${day}/${month}/${year}`
    
    // Format time as HH:MM:SS AM/PM
    let hours = now.getHours()
    const minutes = now.getMinutes().toString().padStart(2, '0')
    const seconds = now.getSeconds().toString().padStart(2, '0')
    const ampm = hours >= 12 ? 'PM' : 'AM'
    hours = hours % 12
    hours = hours ? hours : 12
    currentTime.value = `${hours.toString().padStart(2, '0')}:${minutes}:${seconds} ${ampm}`
  }

  onMounted(() => {
    updateDateTime()
    const interval = setInterval(updateDateTime, 1000)
    
    onUnmounted(() => {
      clearInterval(interval)
    })
  })

  return {
    currentDate,
    currentTime,
    updateDateTime
  }
}