import { useState, useEffect } from 'react'

const EMPTY = { pyNo: '', description: '', modelType: '', typeSide: '', dBit: '', desiredValue: '', machineFixture: '' }

const MODEL_TYPES = ['4 WAY INNER', '4 WAY OUTER', '6 WAY INNER', '6 WAY OUTER']
const SIDES = ['LH', 'RH', 'LH/RH BOTH', 'BOTH', 'ALL']

export default function PokaYokeMaster() {
  const [list, setList]         = useState([])
  const [form, setForm]         = useState(EMPTY)
  const [editId, setEditId]     = useState(null)
  const [showForm, setShowForm] = useState(false)
  const [search, setSearch]     = useState('')
  const [filterType, setFilter] = useState('')
  const [filterSide, setFilterSide] = useState('')

  useEffect(() => { load() }, [])
  const load = () => fetch('/api/pokayokes').then(r => r.json()).then(setList)

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const openAdd  = () => { setForm(EMPTY); setEditId(null); setShowForm(true) }
  const openEdit = (p) => {
    setForm({
      pyNo: p.pyNo, description: p.description, modelType: p.modelType,
      typeSide: p.typeSide || '', dBit: p.dBit || '',
      desiredValue: p.desiredValue ?? '', machineFixture: p.machineFixture
    })
    setEditId(p.id); setShowForm(true)
  }
  const close = () => { setShowForm(false); setEditId(null); setForm(EMPTY) }

  const save = async (e) => {
    e.preventDefault()
    const url    = editId ? `/api/pokayokes/${editId}` : '/api/pokayokes'
    const method = editId ? 'PUT' : 'POST'
    await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form) })
    close(); load()
  }

  const del = async (id) => {
    if (!confirm('Is Poka Yoke ko delete karna hai?')) return
    await fetch(`/api/pokayokes/${id}`, { method: 'DELETE' })
    load()
  }

  const filtered = list.filter(p => {
    const s = search.toLowerCase()
    return (
      (!search      || Object.values(p).some(v => String(v).toLowerCase().includes(s))) &&
      (!filterType  || p.modelType === filterType) &&
      (!filterSide  || p.typeSide  === filterSide)
    )
  })

  // Stats
  const lhCount   = list.filter(p => p.typeSide === 'LH').length
  const rhCount   = list.filter(p => p.typeSide === 'RH').length
  const bothCount = list.filter(p => !['LH','RH'].includes(p.typeSide)).length

  const sideBadge = (s) => {
    if (s === 'LH')   return 'b-blue'
    if (s === 'RH')   return 'b-green'
    if (s === 'BOTH' || s === 'LH/RH BOTH') return 'b-amber'
    return 'b-gray'
  }

  const valBadge = (v) => {
    if (v == 0) return 'b-red'
    if (v == 1) return 'b-green'
    if (v == 2) return 'b-blue'
    return 'b-gray'
  }

  return (
    <div>
      <div className="stats-bar">
        <div className="stat-card">
          <span className="stat-label">Total PY</span>
          <span className="stat-value">{list.length}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">LH Side</span>
          <span className="stat-value" style={{ color: '#1e40af' }}>{lhCount}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">RH Side</span>
          <span className="stat-value" style={{ color: '#065f46' }}>{rhCount}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">BOTH/ALL</span>
          <span className="stat-value" style={{ color: '#92400e' }}>{bothCount}</span>
        </div>
      </div>

      <div className="content-header">
        <h2>Poka Yoke Master</h2>
        <div className="controls">
          <input className="inp inp-search" type="text" placeholder="Search..."
            value={search} onChange={e => setSearch(e.target.value)} />
          <select className="sel sel-filter" value={filterType} onChange={e => setFilter(e.target.value)}>
            <option value="">All Types</option>
            {MODEL_TYPES.map(t => <option key={t}>{t}</option>)}
          </select>
          <select className="sel" value={filterSide} onChange={e => setFilterSide(e.target.value)}>
            <option value="">LH + RH both</option>
            <option value="LH">LH only</option>
            <option value="RH">RH only</option>
            <option value="BOTH">BOTH</option>
            <option value="LH/RH BOTH">LH/RH BOTH</option>
            <option value="ALL">ALL</option>
          </select>
          <button className="btn btn-primary" onClick={openAdd}>+ Add Poka Yoke</button>
        </div>
      </div>

      {showForm && (
        <div className="overlay" onClick={close}>
          <div className="modal modal-wide" onClick={e => e.stopPropagation()}>
            <h3>{editId ? 'Edit Poka Yoke' : 'Add New Poka Yoke'}</h3>
            <form onSubmit={save}>
              <div className="form-grid">
                <div className="form-group">
                  <label>Poka Yoke No *</label>
                  <input required value={form.pyNo} onChange={e => set('pyNo', e.target.value)}
                    placeholder="TBDI-PE-PY-6041" className="mono" />
                </div>
                <div className="form-group">
                  <label>Model Type</label>
                  <select value={form.modelType} onChange={e => set('modelType', e.target.value)}>
                    <option value="">-- Select --</option>
                    {MODEL_TYPES.map(t => <option key={t}>{t}</option>)}
                  </select>
                </div>
                <div className="form-group" style={{ gridColumn: '1/-1' }}>
                  <label>Description *</label>
                  <input required value={form.description} onChange={e => set('description', e.target.value)}
                    placeholder="Detect harness brkt pop rivet operation miss" />
                </div>
                <div className="form-group">
                  <label>Side (LH / RH / BOTH) *</label>
                  <select required value={form.typeSide} onChange={e => set('typeSide', e.target.value)}>
                    <option value="">-- Select Side --</option>
                    {SIDES.map(s => <option key={s}>{s}</option>)}
                  </select>
                </div>
                <div className="form-group">
                  <label>D Bit from PLC</label>
                  <input value={form.dBit} onChange={e => set('dBit', e.target.value)}
                    placeholder="D.041" className="mono" />
                </div>
                <div className="form-group">
                  <label>Desired Value (0 / 1 / 2)</label>
                  <select value={form.desiredValue} onChange={e => set('desiredValue', e.target.value)}>
                    <option value="">-- Select --</option>
                    <option value="0">0 — Miss / Not OK</option>
                    <option value="1">1 — Present / OK</option>
                    <option value="2">2 — Completed</option>
                  </select>
                </div>
                <div className="form-group">
                  <label>Machine / Fixture</label>
                  <input value={form.machineFixture} onChange={e => set('machineFixture', e.target.value)}
                    placeholder="FINAL INSPECTION MACHINE" />
                </div>
              </div>
              <div className="form-actions">
                <button type="button" className="btn btn-secondary" onClick={close}>Cancel</button>
                <button type="submit" className="btn btn-primary">{editId ? 'Update' : 'Add'} Poka Yoke</button>
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
              <th>PY No</th>
              <th>Description</th>
              <th>Model Type</th>
              <th>Side</th>
              <th>D Bit (PLC)</th>
              <th>Desired Value</th>
              <th>Machine / Fixture</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0
              ? <tr><td colSpan="9" className="td-empty">No poka yokes found.</td></tr>
              : filtered.map((p, i) => (
                <tr key={p.id}>
                  <td className="sm" style={{ color: 'var(--muted)' }}>{i + 1}</td>
                  <td className="py-no">{p.pyNo}</td>
                  <td className="sm">{p.description}</td>
                  <td><span className={`badge ${p.modelType?.includes('4') ? 'b-blue' : 'b-purple'}`}>{p.modelType}</span></td>
                  <td><span className={`badge ${sideBadge(p.typeSide)}`}>{p.typeSide || '—'}</span></td>
                  <td className="bit-cell">{p.dBit || '—'}</td>
                  <td>
                    {p.desiredValue !== '' && p.desiredValue !== undefined
                      ? <span className={`badge b-lg ${valBadge(p.desiredValue)}`}>{p.desiredValue}</span>
                      : <span style={{ color: 'var(--muted)' }}>—</span>}
                  </td>
                  <td className="sm">{p.machineFixture}</td>
                  <td>
                    <button className="btn-icon" onClick={() => openEdit(p)}>✏️</button>
                    <button className="btn-icon" onClick={() => del(p.id)}>🗑️</button>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
        <div className="table-footer">Showing {filtered.length} of {list.length} poka yokes</div>
      </div>
    </div>
  )
}
