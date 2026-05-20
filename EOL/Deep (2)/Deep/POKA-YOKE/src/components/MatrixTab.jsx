import { useState, useEffect } from 'react'

export default function MatrixTab() {
  const [assignments, setAssignments] = useState([])
  const [search,       setSearch]      = useState('')
  const [filterType,   setFilterType]  = useState('')
  const [filterSeries, setFilterSeries] = useState('')
  const [selectedModel, setSelectedModel] = useState(null)

  useEffect(() => {
    fetch('/api/assignments').then(r => r.json()).then(setAssignments)
  }, [])

  const uniqueTypes  = [...new Set(assignments.map(a => a.modelType))].filter(Boolean).sort()
  const uniqueSeries = [...new Set(assignments.map(a => a.modelSeries))].filter(Boolean).sort()

  // Filter assignments
  const filtered = assignments.filter(a => {
    const s = search.toLowerCase()
    return (
      (!search       || Object.values(a).some(v => String(v).toLowerCase().includes(s))) &&
      (!filterType   || a.modelType   === filterType) &&
      (!filterSeries || a.modelSeries === filterSeries)
    )
  })

  // Group by model
  const grouped = {}
  filtered.forEach(a => {
    if (!grouped[a.modelName]) grouped[a.modelName] = []
    grouped[a.modelName].push(a)
  })

  const bitValClass = (v) => {
    if (v == 0) return 'bv-0'
    if (v == 1) return 'bv-1'
    return 'bv-2'
  }

  const sideBadge = (s) => {
    if (!s) return 'b-gray'
    if (s === 'LH') return 'b-blue'
    if (s === 'RH') return 'b-green'
    return 'b-amber'
  }

  // Detail view for a selected model
  if (selectedModel) {
    const items = assignments.filter(a => a.modelName === selectedModel)
    return (
      <div>
        <div className="detail-header">
          <span className="detail-back" onClick={() => setSelectedModel(null)}>← Back to Matrix</span>
          <span className="detail-name">{selectedModel}</span>
          {items[0] && (
            <span className="detail-meta">
              {items[0].modelType} &nbsp;|&nbsp; Series: {items[0].modelSeries} &nbsp;|&nbsp; {items[0].oldModelNo}
            </span>
          )}
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>PY No</th>
                <th>Poka Yoke Description</th>
                <th>Side</th>
                <th>D Bit (PLC)</th>
                <th>Desired Value</th>
                <th>Machine / Fixture</th>
              </tr>
            </thead>
            <tbody>
              {items.map((a, i) => (
                <tr key={a.id}>
                  <td className="sm" style={{ color: 'var(--muted)' }}>{i + 1}</td>
                  <td className="py-no">{a.pyNo}</td>
                  <td>{a.pyName}</td>
                  <td><span className={`badge ${sideBadge(a.typeSide)}`}>{a.typeSide}</span></td>
                  <td className="bit-cell">{a.dBit}</td>
                  <td>
                    <span className={`badge b-lg ${a.desiredValue == 0 ? 'b-red' : a.desiredValue == 1 ? 'b-green' : 'b-blue'}`}>
                      {a.desiredValue}
                    </span>
                  </td>
                  <td className="sm">{a.machineFixture}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="table-footer">{items.length} poka yoke checks for this model</div>
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="stats-bar">
        <div className="stat-card">
          <span className="stat-label">Models</span>
          <span className="stat-value">{Object.keys(grouped).length}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Total Checks</span>
          <span className="stat-value">{filtered.length}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Unique PY</span>
          <span className="stat-value">{[...new Set(filtered.map(a => a.pyNo))].length}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Bits Used</span>
          <span className="stat-value">{[...new Set(filtered.map(a => a.dBit).filter(Boolean))].length}</span>
        </div>
      </div>

      <div className="content-header">
        <h2>Poka Yoke Matrix</h2>
        <div className="controls">
          <input className="inp inp-search" type="text" placeholder="Search model / PY / bit..."
            value={search} onChange={e => setSearch(e.target.value)} />
          <select className="sel sel-filter" value={filterType} onChange={e => setFilterType(e.target.value)}>
            <option value="">All Types</option>
            {uniqueTypes.map(t => <option key={t}>{t}</option>)}
          </select>
          <select className="sel sel-filter" value={filterSeries} onChange={e => setFilterSeries(e.target.value)}>
            <option value="">All Series</option>
            {uniqueSeries.map(s => <option key={s}>{s}</option>)}
          </select>
        </div>
      </div>

      {Object.keys(grouped).length === 0 ? (
        <div className="empty-state">
          <p><strong>Data nahi hai.</strong></p>
          <p>Upar "📥 Import Excel" button se Excel se import karo,<br />ya Config tab me manually assignment add karo.</p>
        </div>
      ) : (
        <div className="matrix-grid">
          {Object.entries(grouped).map(([modelName, items]) => (
            <div key={modelName} className="model-card">
              <div className="model-card-head" onClick={() => setSelectedModel(modelName)}>
                <div>
                  <div className="model-card-name">{modelName}</div>
                  <div className="model-card-meta">
                    {items[0]?.modelType && <span className={`badge ${items[0].modelType?.includes('4') ? 'b-blue' : 'b-purple'}`} style={{ marginRight: 6 }}>{items[0].modelType}</span>}
                    <span className="badge b-gray">{items[0]?.modelSeries}</span>
                    &nbsp; {items[0]?.oldModelNo && <span style={{ color: 'var(--muted)', fontSize: '.75rem' }}>{items[0].oldModelNo}</span>}
                  </div>
                </div>
                <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span className="badge b-gray">{items.length} checks</span>
                  <span className="model-card-link">View Details →</span>
                </div>
              </div>
              <div className="model-card-bits">
                {items.map(a => (
                  <span key={a.id} className="bit-pill" title={`${a.pyNo}: ${a.pyName}`}>
                    <span className="bit-pill-name">{a.dBit || '—'}</span>
                    <span className={`bit-pill-val ${bitValClass(a.desiredValue)}`}>{a.desiredValue}</span>
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {Object.keys(grouped).length > 0 && (
        <div style={{ marginTop: 12, fontSize: '.8rem', color: 'var(--muted)', textAlign: 'right' }}>
          {Object.keys(grouped).length} models &nbsp;·&nbsp; {filtered.length} total checks shown
        </div>
      )}
    </div>
  )
}
