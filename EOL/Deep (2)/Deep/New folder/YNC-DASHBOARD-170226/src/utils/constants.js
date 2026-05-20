// Constants for the dashboard

// Button colors with fixed IDs
export const BUTTON_COLORS = {
  'breakdown': { color: '#FF0000', id: 1 },
  'quality': { color: '#FFFF00', id: 2 },
  'model-change': { color: '#0000FF', id: 3 },
  'material-shortage': { color: '#FFA500', id: 4 },
  'others-losses': { color: '#00FFFF', id: 5 }
}

// A Shift Hours
export const A_SHIFT_HOURS = [
  { start: '08:30', end: '09:30', label: '08:30-09:30' },
  { start: '09:30', end: '10:00', label: '09:30-10:00' },
  { start: '10:10', end: '10:30', label: '10:10-10:30' },
  { start: '10:30', end: '11:30', label: '10:30-11:30' },
  { start: '11:30', end: '12:00', label: '11:30-12:00' },
  { start: '12:35', end: '13:05', label: '12:35-13:05' },
  { start: '13:05', end: '14:05', label: '13:05-14:05' },
  { start: '14:05', end: '14:30', label: '14:05-14:30' },
  { start: '14:40', end: '15:05', label: '14:40-15:05' },
  { start: '15:05', end: '16:05', label: '15:05-16:05' },
  { start: '16:05', end: '17:15', label: '16:05-17:15' },
  { start: '17:15', end: '18:30', label: '17:15-18:30'}
]

export const A_SHIFT_BREAKS = [
  { start: '10:00', end: '10:10', type: 'tea', label: 'TEA' },
  { start: '12:00', end: '12:35', type: 'lunch', label: 'LUNCH' },
  { start: '14:30', end: '14:40', type: 'tea', label: 'TEA' }
]

// B Shift Hours
export const B_SHIFT_HOURS = [
  { start: '18:30', end: '19:30', label: '18:30-19:30' },
  { start: '19:30', end: '20:00', label: '19:30-20:00' },
  { start: '20:10', end: '20:30', label: '20:10-20:30' },
  { start: '20:30', end: '21:30', label: '20:30-21:30' },
  { start: '21:30', end: '22:00', label: '21:30-22:00' },
  { start: '22:35', end: '23:05', label: '22:35-23:05' },
  { start: '23:05', end: '00:05', label: '23:05-00:05' },
  { start: '00:05', end: '01:00', label: '00:05-01:00' },
  { start: '01:10', end: '02:05', label: '01:10-02:05' },
  { start: '02:05', end: '03:15', label: '02:05-03:15' }
]

export const B_SHIFT_BREAKS = [
  { start: '20:00', end: '20:10', type: 'tea', label: 'TEA' },
  { start: '22:00', end: '22:35', type: 'tea', label: 'DINNER' },
  { start: '01:00', end: '01:10', type: 'tea', label: 'TEA' }
]

// OEE Grade Thresholds
export const OEE_GRADES = {
  EXCELLENT: { min: 90, color: '#27ae60' },
  GOOD: { min: 80, color: '#2ecc71' },
  AVERAGE: { min: 70, color: '#f39c12' },
  FAIR: { min: 60, color: '#e67e22' },
  POOR: { min: 0, color: '#e74c3c' }
}

// Default cycle time target
export const CYCLE_TIME_TARGET = 15.6 // seconds

// Shift duration in seconds (for progress bar)
export const SHIFT_DURATION = 8 * 60 * 60 // 8 hours in seconds