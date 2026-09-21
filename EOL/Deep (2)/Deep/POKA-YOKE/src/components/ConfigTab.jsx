import { useState, useEffect } from 'react'

const EMPTY = {
  pyNo: '', pyName: '', typeSide: '', modelType: '', modelName: '',
  type2: '', oldModelNo: '', modelSeries: '', dBit: '', desiredValue: '', machineFixture: ''
}

export default function ConfigTab() {
  const [assignments, setAssignments] = useState([])
  const [models,      setModels]      = useState([])
  const [pokayokes,   setPokayokes]   = useState([])
  const [form,        setForm]        = useState(EMPTY)
  const [showForm,    setShowForm]    = useState(false)

  // Filters inside the form (cascading)
  const [pyFilterType, setPyFilterType] = useState('')
  const [pyFilterSide, setPyFilterSide] = useState('')
  const [mFilterType,  setMFilterType]  = useState('')
  const [mFilterSeries,setMFilterSeries]= useState('')

  // Table filters
  const [search,      setSearch]      = useState('')
  const [filterModel, setFilterModel] = useState('')
  const [filterType,  setFilterType]  = useState('')

  useEffect(() => { loadAll() }, [])

  const loadAll = async () => {
    const [a, m, p] = await Promise.all([
      fetch('/api/assignments').then(r => r.json()),
      fetch('/api/models').then(r => r.json()),
      fetch('/api/pokayokes').then(r => r.json()),
    ])
    setAssignments(a); setModels(m); setPokayokes(p)
  }

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  // ─── Cascading PY filter ───────────────────────────────
  const pyTypes  = [...new Set(pokayokes.map(p => p.modelType).filter(Boolean))].sort()
  const pySides  = [...new Set(
    pokayokes
      .filter(p => !pyFilterType || p.modelType === pyFilterType)
      .map(p => p.typeSide).filter(Boolean)
  )].sort()

  const filteredPY = pokayokes.filter(p =>
    (!pyFilterType || p.modelType === pyFilterType) &&
    (!pyFilterSide || p.typeSide  === pyFilterSide)
  )

  // ─── Cascading Model filter ────────────────────────────
  const mTypes   = [...new Set(models.map(m => m.type).filter(Boolean))].sort()
  const mSeries  = [...new Set(
    models
      .filter(m => !mFilterType || m.type === mFilterType)
      .map(m => m.model).filter(Boolean)
  )].sort()

  const filteredModels = models.filter(m =>
    (!mFilterType   || m.type  === mFilterType) &&
    (!mFilterSeries || m.model === mFilterSeries)
  )

  // ─── PY selected ──────────────────────────────────────
  const onPYChange = (pyId) => {
    if (!pyId) { set('pyNo', ''); set('pyName', ''); return }
    const py = pokayokes.find(p => String(p.id) === String(pyId))
    if (!py) return
    setForm(f => ({
      ...f,
      pyNo:           py.pyNo,
      pyName:         py.description    || '',
      modelType:      py.modelType      || '',
      typeSide:       py.typeSide       || '',
      dBit:           py.dBit           || '',
      desiredValue:   py.desiredValue != null ? String(py.desiredValue) : '',
      machineFixture: py.machineFixture || ''
    }))
  }

  // ─── Model selected ───────────────────────────────────
  const onModelChange = (modelId) => {
    if (!modelId) { set('modelName', ''); return }
    const m = models.find(m => String(m.id) === String(modelId))
    if (!m) return
    setForm(f => ({
      ...f,
      modelName:   m.modelName  || '',
      type2:       m.type       || '',
      oldModelNo:  m.oldModelNo || '',
      modelSeries: m.model      || ''
    }))
  }

  const openForm = () => {
    setForm(EMPTY)
    setPyFilterType(''); setPyFilterSide('')
    setMFilterType('');  setMFilterSeries('')
    setShowForm(true)
  }
  const closeForm = () => { setShowForm(false); setForm(EMPTY) }

  const save = async (e) => {
    e.preventDefault()
    if (!form.pyNo)      return alert('Poka Yoke select karo')
    if (!form.modelName) return alert('Model select karo')
    await fetch('/api/assignments', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form)
    })
    closeForm(); loadAll()
  }

  const del = async (id) => {
    if (!confirm('Is assignment ko delete karna hai?')) return
    await fetch(`/api/assignments/${id}`, { method: 'DELETE' })
    loadAll()
  }

  // ─── Table filters ────────────────────────────────────
  const uniqueModels     = [...new Set(assignments.map(a => a.modelName))].filter(Boolean).sort()
  const uniqueModelTypes = [...new Set(assignments.map(a => a.modelType))].filter(Boolean).sort()

  const filtered = assignments.filter(a => {
    const s = search.toLowerCase()
    return (
      (!search      || Object.values(a).some(v => String(v).toLowerCase().includes(s))) &&
      (!filterModel || a.modelName === filterModel) &&
      (!filterType  || a.modelType === filterType)
    )
  })

  const sideBadge  = (s) => s === 'LH' ? 'b-blue' : s === 'RH' ? 'b-green' : s === 'BOTH' || s === 'LH/RH BOTH' ? 'b-amber' : 'b-gray'
  const valBadge   = (v) => v == 0 ? 'b-red' : v == 1 ? 'b-green' : 'b-blue'

  // find selected PY id for controlled select
  const selectedPYId = form.pyNo
    ? (pokayokes.find(p => p.pyNo === form.pyNo && p.typeSide === form.typeSide)?.id || '')
    : ''

  const selectedModelId = form.modelName
    ? (models.find(m => m.modelName === form.modelName)?.id || '')
    : ''

  return (
    <div>
      <div className="stats-bar">
        <div className="stat-card"><span className="stat-label">Total Assignments</span><span className="stat-value">{assignments.length}</span></div>
        <div className="stat-card"><span className="stat-label">Models Configured</span><span className="stat-value">{[...new Set(assignments.map(a=>a.modelName))].length}</span></div>
        <div className="stat-card"><span className="stat-label">Unique PY</span><span className="stat-value">{[...new Set(assignments.map(a=>a.pyNo))].length}</span></div>
      </div>

      <div className="content-header">
        <h2>Config — Poka Yoke Assignments</h2>
        <div className="controls">
          <input className="inp inp-search" type="text" placeholder="Search..."
            value={search} onChange={e => setSearch(e.target.value)} />
          <select className="sel sel-filter" value={filterType} onChange={e => setFilterType(e.target.value)}>
            <option value="">All Types</option>
            {uniqueModelTypes.map(t => <option key={t}>{t}</option>)}
          </select>
          <select className="sel sel-wide" value={filterModel} onChange={e => setFilterModel(e.target.value)}>
            <option value="">All Models</option>
            {uniqueModels.map(m => <option key={m}>{m}</option>)}
          </select>
          <button className="btn btn-primary" onClick={openForm}>+ Add Assignment</button>
        </div>
      </div>

      {/* ─── ADD FORM MODAL ─── */}
      {showForm && (
        <div className="overlay" onClick={closeForm}>
          <div className="modal modal-wide" onClick={e => e.stopPropagation()}>
            <h3>Add Poka Yoke Assignment</h3>
            <form onSubmit={save}>

              {/* ── POKA YOKE SECTION ── */}
              <div className="section-label">🔍 Poka Yoke Select Karo</div>
              <div className="filter-row">
                <div className="form-group">
                  <label>Type Filter</label>
                  <select className="sel" value={pyFilterType}
                    onChange={e => { setPyFilterType(e.target.value); setPyFilterSide(''); set('pyNo',''); set('pyName','') }}>
                    <option value="">Sab Types</option>
                    {pyTypes.map(t => <option key={t}>{t}</option>)}
                  </select>
                </div>
                <div className="form-group">
                  <label>Side Filter</label>
                  <select className="sel" value={pyFilterSide}
                    onChange={e => { setPyFilterSide(e.target.value); set('pyNo',''); set('pyName','') }}>
                    <option value="">LH + RH sab</option>
                    {pySides.map(s => <option key={s}>{s}</option>)}
                  </select>
                </div>
                <div className="form-group" style={{ flex: 2 }}>
                  <label>Poka Yoke * {filteredPY.length > 0 && <span style={{color:'var(--muted)',fontWeight:400}}>({filteredPY.length} milein)</span>}</label>
                  <select required value={selectedPYId} onChange={e => onPYChange(e.target.value)}>
                    <option value="">-- PY Select Karo --</option>
                    {filteredPY.map(p => (
                      <option key={p.id} value={p.id}>
                        [{p.typeSide || 'ALL'}] {p.pyNo} — {p.description}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Selected PY info */}
              {form.pyNo && (
                <div className="info-strip" style={{ marginBottom: 14 }}>
                  <span><strong>PY:</strong> {form.pyNo}</span>
                  <span><strong>Side:</strong> {form.typeSide || '—'}</span>
                  <span><strong>D Bit:</strong> <b style={{color:'#7c3aed'}}>{form.dBit || '—'}</b></span>
                  <span><strong>Value:</strong> {form.desiredValue !== '' ? form.desiredValue : '—'}</span>
                  <span><strong>Machine:</strong> {form.machineFixture || '—'}</span>
                </div>
              )}

              <div className="form-divider" />

              {/* ── MODEL SECTION ── */}
              <div className="section-label">🗂️ Model Select Karo</div>
              <div className="filter-row">
                <div className="form-group">
                  <label>Type Filter</label>
                  <select className="sel" value={mFilterType}
                    onChange={e => { setMFilterType(e.target.value); setMFilterSeries(''); set('modelName','') }}>
                    <option value="">Sab Types</option>
                    {mTypes.map(t => <option key={t}>{t}</option>)}
                  </select>
                </div>
                <div className="form-group">
                  <label>Series Filter</label>
                  <select className="sel" value={mFilterSeries}
                    onChange={e => { setMFilterSeries(e.target.value); set('modelName','') }}>
                    <option value="">Sab Series</option>
                    {mSeries.map(s => <option key={s}>{s}</option>)}
                  </select>
                </div>
                <div className="form-group" style={{ flex: 2 }}>
                  <label>Model * {filteredModels.length > 0 && <span style={{color:'var(--muted)',fontWeight:400}}>({filteredModels.length} milein)</span>}</label>
                  <select required value={selectedModelId} onChange={e => onModelChange(e.target.value)}>
                    <option value="">-- Model Select Karo --</option>
                    {filteredModels.map(m => (
                      <option key={m.id} value={m.id}>{m.modelName}</option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Selected Model info */}
              {form.modelName && (
                <div className="info-strip" style={{ marginBottom: 14 }}>
                  <span><strong>Type:</strong> {form.type2 || '—'}</span>
                  <span><strong>Old Model No:</strong> {form.oldModelNo || '—'}</span>
                  <span><strong>Series:</strong> {form.modelSeries || '—'}</span>
                </div>
              )}

              <div className="form-divider" />

              {/* ── OVERRIDE FIELDS ── */}
              <div className="section-label">⚙️ Override (Optional)</div>
              <div className="form-grid">
                <div className="form-group">
                  <label>D Bit from PLC</label>
                  <input value={form.dBit} onChange={e => set('dBit', e.target.value)}
                    placeholder="D.041" className="mono" />
                </div>
                <div className="form-group">
                  <label>Desired Value</label>
                  <select value={form.desiredValue} onChange={e => set('desiredValue', e.target.value)}>
                    <option value="">-- Select --</option>
                    <option value="0">0 — Miss / Not OK</option>
                    <option value="1">1 — Present / OK</option>
                    <option value="2">2 — Completed</option>
                  </select>
                </div>
                <div className="form-group" style={{ gridColumn: '1/-1' }}>
                  <label>Machine / Fixture</label>
                  <input value={form.machineFixture} onChange={e => set('machineFixture', e.target.value)}
                    placeholder="FINAL INSPECTION MACHINE" />
                </div>
              </div>

              <div className="form-actions">
                <button type="button" className="btn btn-secondary" onClick={closeForm}>Cancel</button>
                <button type="submit" className="btn btn-primary"
                  disabled={!form.pyNo || !form.modelName}>
                  Add Assignment
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ─── TABLE ─── */}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th><th>PY No</th><th>Poka Yoke</th><th>Side</th>
              <th>Model Name</th><th>Type</th><th>Series</th>
              <th>D Bit</th><th>Value</th><th>Machine</th><th></th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0
              ? <tr><td colSpan="11" className="td-empty">No assignments found.</td></tr>
              : filtered.map((a, i) => (
                <tr key={a.id}>
                  <td className="sm" style={{color:'var(--muted)'}}>{i+1}</td>
                  <td className="py-no">{a.pyNo}</td>
                  <td className="sm">{a.pyName}</td>
                  <td><span className={`badge ${sideBadge(a.typeSide)}`}>{a.typeSide}</span></td>
                  <td className="sm" style={{maxWidth:220,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}} title={a.modelName}>{a.modelName}</td>
                  <td><span className={`badge ${a.modelType?.includes('4') ? 'b-blue':'b-purple'}`}>{a.modelType}</span></td>
                  <td><span className="badge b-gray">{a.modelSeries}</span></td>
                  <td className="bit-cell">{a.dBit}</td>
                  <td><span className={`badge b-lg ${valBadge(a.desiredValue)}`}>{a.desiredValue}</span></td>
                  <td className="sm">{a.machineFixture}</td>
                  <td><button className="btn-icon" onClick={()=>del(a.id)} title="Delete">🗑️</button></td>
                </tr>
              ))}
          </tbody>
        </table>
        <div className="table-footer">Showing {filtered.length} of {assignments.length} assignments</div>
      </div>
    </div>
  )
}
