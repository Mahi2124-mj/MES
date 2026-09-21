import axios from 'axios'

const TOKEN_KEY = 'tb-ems-token'

const http = axios.create({ baseURL: '/api' })

http.interceptors.request.use(cfg => {
  const t = localStorage.getItem(TOKEN_KEY)
  if (t) cfg.headers.Authorization = `Bearer ${t}`
  return cfg
})

http.interceptors.response.use(
  r => r,
  err => {
    if (err.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem('tb-ems-user')
      window.location.reload()
    }
    return Promise.reject(err)
  }
)

function unwrap(r) { return r.data?.data ?? r.data }

export const api = {
  // Auth
  login: body => http.post('/auth/login', body).then(unwrap),

  // Overview
  getOverview: () => http.get('/overview').then(unwrap),
  getHierarchy: () => http.get('/hierarchy').then(unwrap),

  // Lines
  getLines: () => http.get('/masters/lines').then(unwrap),
  createLine: body => http.post('/masters/lines', body).then(unwrap),
  updateLine: (zoneId, lineId, body) => http.patch(`/masters/lines/${zoneId}/${lineId}`, body).then(unwrap),
  deleteLine: (zoneId, lineId) => http.delete(`/masters/lines/${zoneId}/${lineId}`).then(unwrap),

  // Zones
  getZones: () => http.get('/masters/zones').then(unwrap),
  createZone: body => http.post('/masters/zones', body).then(unwrap),
  updateZone: (zoneId, body) => http.patch(`/masters/zones/${zoneId}`, body).then(unwrap),
  deleteZone: zoneId => http.delete(`/masters/zones/${zoneId}`).then(unwrap),

  // Machines
  getMachines: () => http.get('/masters/machines').then(unwrap),
  createMachine: body => http.post('/masters/machines', body).then(unwrap),
  updateMachine: (zoneId, lineId, machineId, body) => http.patch(`/masters/machines/${zoneId}/${lineId}/${machineId}`, body).then(unwrap),
  deleteMachine: (zoneId, lineId, machineId) => http.delete(`/masters/machines/${zoneId}/${lineId}/${machineId}`).then(unwrap),
  assignCamera: (zoneId, lineId, machineId, body) => http.patch(`/masters/machines/${zoneId}/${lineId}/${machineId}/camera`, body).then(unwrap),

  // Cameras
  getCameras: () => http.get('/masters/cameras').then(unwrap),
  createCamera: body => http.post('/masters/cameras', body).then(unwrap),
  updateCamera: (id, body) => http.patch(`/masters/cameras/${id}`, body).then(unwrap),
  deleteCamera: id => http.delete(`/masters/cameras/${id}`).then(unwrap),

  // PLCs
  getPlcs: () => http.get('/masters/plcs').then(unwrap),
  createPlc: body => http.post('/masters/plcs', body).then(unwrap),
  updatePlc: (id, body) => http.patch(`/masters/plcs/${id}`, body).then(unwrap),
  deletePlc: id => http.delete(`/masters/plcs/${id}`).then(unwrap),

  // Camera Grid + Cycle
  getCameraGrid: () => http.get('/camera-grid').then(unwrap),
  getCycleStatus: () => http.get('/cycle/status').then(unwrap),
  triggerCycle: body => http.post('/cycle/trigger', body).then(unwrap),
  getCycleHistory: params => {
    const qs = new URLSearchParams(params).toString()
    return http.get(`/cycle/history${qs ? `?${qs}` : ''}`).then(unwrap)
  },

  // Shifts
  getShifts: () => http.get('/masters/shifts').then(unwrap),
  saveShift: body => http.post('/masters/shifts', body).then(unwrap),
  deleteShift: id => http.delete(`/masters/shifts/${id}`).then(unwrap),

  // Camera Config Bindings
  getCameraConfigs: () => http.get('/camera-configs').then(unwrap),
  saveCameraConfig: body => http.post('/camera-configs', body).then(unwrap),
  deleteCameraConfig: id => http.delete(`/camera-configs/${id}`).then(unwrap),

  // PLC Config (legacy single — kept for backward compat)
  getPlcConfig: () => http.get('/plc-config').then(unwrap),
  savePlcConfig: body => http.post('/plc-config', body).then(unwrap),

  // System Settings (video storage path on external HDD, etc.)
  getSettings: () => http.get('/settings').then(unwrap),
  saveSettings: body => http.post('/settings', body).then(unwrap),

  // Sync zones/lines/machines from MES Postgres into the CMS local store
  syncFromMes: () => http.post('/sync/from-mes').then(unwrap),
}

// Camera stream base URL.  In production set VITE_STREAM_BASE_URL
// (typically the same origin as the API host) — falls back to relative
// path "" so the browser uses the page's own origin.  Earlier this was
// hardcoded to http://127.0.0.1:5000 which broke video on every
// non-localhost deployment.
const STREAM_BASE_URL = import.meta.env.VITE_STREAM_BASE_URL || ''

export function streamUrl(cameraId) {
  if (!cameraId) return null
  return `${STREAM_BASE_URL}/live_feed/${cameraId}`
}

export function frameUrl(cameraId, stamp = '') {
  if (!cameraId) return null
  return `${STREAM_BASE_URL}/camera_frame/${cameraId}${stamp ? `?t=${stamp}` : ''}`
}

export { STREAM_BASE_URL }
