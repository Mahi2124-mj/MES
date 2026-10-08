// Where this app runs (2026-10-08).
//   LAN dev server (vite, :5575) → served at "/", CMS API at "/api".
//   Inside the MES (mes.tbdi.in/cms/, built with `--base /cms/`) → the MES
//   server forwards "/cms-api/*" to the CMS, so every CMS URL gets that prefix.
// Every absolute URL in the app goes through API_BASE / cmsUrl.
const UNDER_MES = import.meta.env.BASE_URL.startsWith('/cms')

export const API_BASE = UNDER_MES ? '/cms-api/api' : '/api'
export const cmsUrl = (path) => (UNDER_MES ? '/cms-api' : '') + path
export const ROUTER_BASE = import.meta.env.BASE_URL.replace(/\/$/, '')
