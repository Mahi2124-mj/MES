<template>
  <div class="table-right">
    <table class="loss-table">
      <thead>
        <tr>
          <th>LOSS PARAMETER</th>
          <th>TIME</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td class="loss-name">Machine Breakdown</td>
          <td class="loss-time" id="breakdown-timer">
            {{ formatLossTime(lossTimes.breakdown) }}
          </td>
        </tr>
        <tr>
          <td class="loss-name">Quality</td>
          <td class="loss-time" id="quality-timer">
            {{ formatLossTime(lossTimes.quality) }}
          </td>
        </tr>
        <tr>
          <td class="loss-name">Material</td>
          <td class="loss-time" id="material-timer">
            {{ formatLossTime(lossTimes.material) }}
          </td>
        </tr>
        <tr>
          <td class="loss-name">Setup Time</td>
          <td class="loss-time" id="setup-timer">
            {{ formatLossTime(lossTimes.setup) }}
          </td>
        </tr>

         <tr>
            <td class="loss-name">Change Over</td>
            <td class="loss-time" id="changeover-timer">
              {{ formatLossTime(lossTimes.changeOver) }}
            </td>
          </tr>

          <!-- ✅ NEW LOSS 2: Speed Loss -->
          <tr>
            <td class="loss-name">Speed Loss</td>
            <td class="loss-time" id="speedloss-timer">
              {{ formatLossTime(lossTimes.speed) }}
            </td>
          </tr>
        <tr>
          <td class="loss-name">Others</td>
          <td class="loss-time" id="others-timer">
            {{ formatLossTime(lossTimes.others) }}
          </td>
        </tr>

        <tr class="loss-total-row">
          <td class="loss-name total-loss">TOTAL LOSS</td>
          <td class="loss-time total-time" id="total-loss-timer">
            {{ formatLossTime(lossTimes.total) }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
const props = defineProps({
  lossTimes: {
    type: Object,
    required: true,
    default: () => ({
      breakdown: '00:00:00',
      quality: '00:00:00',
      material: '00:00:00',
      setup: '00:00:00',
      others: '00:00:00',
      total: '00:00:00'
    })
  }
})

// Format loss time - accept both seconds and HH:MM:SS
const formatLossTime = (timeValue) => {
  if (!timeValue && timeValue !== 0) return '00:00:00'
  
  // If already in HH:MM:SS format (string with colons)
  if (typeof timeValue === 'string' && timeValue.includes(':')) {
    return timeValue
  }
  
  // If it's a number (seconds)
  const seconds = Number(timeValue)
  if (!isNaN(seconds)) {
    const hours = Math.floor(seconds / 3600)
    const minutes = Math.floor((seconds % 3600) / 60)
    const secs = seconds % 60
    
    return [
      hours.toString().padStart(2, '0'),
      minutes.toString().padStart(2, '0'),
      secs.toString().padStart(2, '0')
    ].join(':')
  }
  
  return '00:00:00'
}
</script>