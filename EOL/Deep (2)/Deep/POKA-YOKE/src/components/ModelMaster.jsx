import { useState, useEffect } from 'react'

const EMPTY = { modelName: '', type: '', oldModelNo: '', model: '' }

const MODEL_TYPES = [
  '4 Way Inr LH', '4 Way Inr RH', '4 Way OTR',
  '6 Way Inr LH', '6 Way Inr RH', '6 Way OTR',
]

export default function ModelMaster() {
  const [models, setModels]     = useState([])
  const [form, setForm]         = useState(EMPTY)
  const [editId, setEditId]     = useState(null)
  const [showForm, setShowForm] = useState(false)
  const [search, setSearch]     = useState('')

  useEffect(() => { load() }, [])
  const load = () => fetch('/api/models').then(r => r.json()).then(setModels)

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const openAdd = () => { setForm(EMPTY); setEditId(null); setShowForm(true) }
  const openEdit = (m) => {
    setForm({ modelName: m.modelName, type: m.type, oldModelNo: m.oldModelNo, model: m.model })
    setEditId(m.id); setShowForm(true)
  }
  const close = () => { setShowForm(false); setEditId(null); setForm(EMPTY) }

  const save = async (e) => {
    e.preventDefault()
    const url    = editId ? `/api/models/${editId}` : '/api/models'
    const method = editId ? 'PUT' : 'POST'
    await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form) })
    close(); load()
  }

  const del = async (id) => {
    if (!confirm('Is model delete karna hai?')) return
    await fetch(`/api/models/${id}`, { method: 'DELETE' })
    load()
  }

  const filtered = models.filter(m =>
    !search || Object.values(m).some(v => String(v).toLowerCase().includes(search.toLowerCase()))
  )

  return (
    <div>
      <div className="stats-bar">
        <div className="stat-card">
          <span className="stat-label">Total Models</span>
          <span className="stat-value">{models.length}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Series</span>
          <span className="stat-value">{[...new Set(models.map(m => m.model).filter(Boolean))].length}</span>
        </div>
      </div>

      <div className="content-header">
        <h2>Model Master</h2>
        <div className="controls">
          <input className="inp inp-search" type="text" placeholder="Search..."
            value={search} onChange={e => setSearch(e.target.value)} />
          <button className="btn btn-primary" onClick={openAdd}>+ Add Model</button>
        </div>
      </div>

      {showForm && (
        <div className="overlay" onClick={close}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h3>{editId ? 'Edit Model' : 'Add New Model'}</h3>
            <form onSubmit={save}>
              <div className="form-grid">
                <div className="form-group" style={{ gridColumn: '1/-1' }}>
                  <label>Model Name *</label>
                  <input required value={form.modelName} onChange={e => set('modelName', e.target.value)}
                    placeholder="TRACK ASSY FRONT SEAT Y17 4 WAY INR LH" />
                </div>
                <div className="form-group">
                  <label>Type *</label>
                  <select required value={form.type} onChange={e => set('type', e.target.value)}>
                    <option value="">-- Select Type --</option>
                    {MODEL_TYPES.map(t => <option key={t}>{t}</option>)}
                    <option value="__custom__">Other (type below)</option>
                  </select>
                  {form.type === '__custom__' &&
                    <input placeholder="Enter type manually" onChange={e => set('type', e.target.value)} style={{ marginTop: 6 }} />}
                </div>
                <div className="form-group">
                  <label>Model Series *</label>
                  <input required value={form.model} onChange={e => set('model', e.target.value)}
                    placeholder="Y17, YCA, YJC..." />
                </div>
                <div className="form-group" style={{ gridColumn: '1/-1' }}>
                  <label>Old Model No</label>
                  <input value={form.oldModelNo} onChange={e => set('oldModelNo', e.target.value)}
                    placeholder="433140-14240-Y17" />
                </div>
              </div>
              <div className="form-actions">
                <button type="button" className="btn btn-secondary" onClick={close}>Cancel</button>
                <button type="submit" className="btn btn-primary">{editId ? 'Update' : 'Add'} Model</button>
              </div>
            </form>
          </div>
        </div>
      )}

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Model Name</th>
              <th>Type</th>
              <th>Old Model No</th>
              <th>Series</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0
              ? <tr><td colSpan="6" className="td-empty">No models found. Import from Excel ya manually add karo.</td></tr>
              : filtered.map((m, i) => (
                <tr key={m.id}>
                  <td className="muted sm">{i + 1}</td>
                  <td style={{ fontWeight: 500 }}>{m.modelName}</td>
                  <td><span className="badge b-blue">{m.type}</span></td>
                  <td className="mono">{m.oldModelNo}</td>
                  <td><span className="badge b-green">{m.model}</span></td>
                  <td>
                    <button className="btn-icon" title="Edit" onClick={() => openEdit(m)}>✏️</button>
                    <button className="btn-icon" title="Delete" onClick={() => del(m.id)}>🗑️</button>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
        <div className="table-footer">Showing {filtered.length} of {models.length} models</div>
      </div>
    </div>
  )
}
