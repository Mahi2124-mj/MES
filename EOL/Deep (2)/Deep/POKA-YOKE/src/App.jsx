import { useState } from 'react'
import ModelMaster from './components/ModelMaster'
import PokaYokeMaster from './components/PokaYokeMaster'
import ConfigTab from './components/ConfigTab'
import MatrixTab from './components/MatrixTab'

const TABS = [
  { id: 'matrix',   label: '📊 Matrix' },
  { id: 'config',   label: '⚙️ Config' },
  { id: 'pokayoke', label: '🔍 Poka Yoke Master' },
  { id: 'model',    label: '🗂️ Model Master' },
]

export default function App() {
  const [activeTab, setActiveTab]   = useState('matrix')
  const [importing, setImporting]   = useState(false)
  const [msg, setMsg]               = useState(null)
  const [refreshKey, setRefreshKey] = useState(0)

  const handleImport = async () => {
    setImporting(true)
    setMsg(null)
    try {
      const res = await fetch('/api/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: 'C:/Users/vivek.kumar/Desktop/poka yoka metrix.xlsx' })
      })
      const data = await res.json()
      if (data.success) {
        setMsg({ type: 'success', text: `✅ Import successful — ${data.imported.models} models, ${data.imported.pokayokes} poka yokes, ${data.imported.assignments} assignments` })
        setRefreshKey(k => k + 1)
      } else {
        setMsg({ type: 'error', text: '❌ Error: ' + data.error })
      }
    } catch (err) {
      setMsg({ type: 'error', text: '❌ ' + err.message })
    }
    setImporting(false)
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-brand">
          <div className="brand-logo">PY</div>
          <div>
            <h1>Poka Yoke Management System</h1>
            <span className="company">Toyota Boshoku Device India Pvt. Ltd.</span>
          </div>
        </div>
        <div className="header-actions">
          <button onClick={handleImport} disabled={importing} className="btn btn-import">
            {importing ? '⏳ Importing...' : '📥 Import Excel'}
          </button>
          <a href="/api/export" className="btn btn-export">📤 Export Excel</a>
        </div>
      </header>

      {msg && (
        <div className={`alert alert-${msg.type}`}>
          <span>{msg.text}</span>
          <button onClick={() => setMsg(null)}>✕</button>
        </div>
      )}

      <nav className="tab-nav">
        {TABS.map(t => (
          <button key={t.id} className={`tab-btn ${activeTab === t.id ? 'active' : ''}`}
            onClick={() => setActiveTab(t.id)}>
            {t.label}
          </button>
        ))}
      </nav>

      <main className="app-main">
        {activeTab === 'matrix'   && <MatrixTab     key={refreshKey} />}
        {activeTab === 'config'   && <ConfigTab     key={refreshKey} />}
        {activeTab === 'pokayoke' && <PokaYokeMaster key={refreshKey} />}
        {activeTab === 'model'    && <ModelMaster   key={refreshKey} />}
      </main>
    </div>
  )
}
