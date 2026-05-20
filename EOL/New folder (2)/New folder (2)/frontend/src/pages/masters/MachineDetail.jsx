import { useState, useEffect, useMemo } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, Save, Plus, Camera as CameraIcon, Cpu, Activity, Lock } from 'lucide-react'
import { api } from '../../lib/api'
import { useToast } from '../../context/ToastContext'

/**
 * MachineDetail — view + camera-edit form for an existing MES machine.
 *
 * 2026-05-18 — Refactored per operator spec:
 *   "mes me machines set kri to cms me machine add nhi krunga m ok
 *    auto details vha aajaye bs camera details add and update ka
 *    option de ok"
 *
 * Behaviour:
 *   • All MES-owned fields (identity, PLC, trigger, advanced) are
 *     READ-ONLY display.  Edit them in MES Admin Panel only.
 *   • Camera section is the ONLY editable area — assign / change
 *     the bound NF2 camera here.
 *   • No create flow ("new" mode redirects back to the list).
 *   • No delete (MES owns lifecycle).
 *
 * On save, the existing /api/mes/machine PUT is reused with the full
 * record + new nf2_camera_id; the read-only fields round-trip
 * unchanged so MES doesn't notice anything.  CMS's
 * _sync_binding_from_machine then rewrites
 * camera_config_bindings.json so the recorder picks up the new camera
 * within seconds.
 */

// All MES fields stay as empty strings by default — user must fill in.
// No L108/L109/M100 placeholders, per user request 2026-05-13.
const EMPTY_FORM = {
  id:                  null,
  line_id:             '',
  machine_name:        '',
  plc_ip:              '',
  plc_port:            5002,
  protocol:            'MC4E',
  ok_bit_address:      '',
  ng_bit_address:      '',
  status_address:      '',
  model_address:       '',
  sensor_ok_address:   '',
  process_seq_address: '',
  override_address:    '',
  ideal_cycle_time:    '',
  max_allowed_cycle:   '',
  ok_ng_pulse_min_gap: '',
  parent_plc_id:       null,    // null = MAIN machine
  nf2_camera_id:       '',
  machine_seq:         '',
}

const EMPTY_CAMERA = {
  name: '', ip: '', port: 554, username: '', password: '', path: '/h264/ch1/main/av_stream',
}

export default function MachineDetail() {
  const { machineId } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const isEdit = machineId && machineId !== 'new'

  // 2026-05-18 — Create flow disabled; bounce back to list.
  useEffect(() => {
    if (machineId === 'new') {
      toast.error('Add machines in MES Admin Panel — CMS only manages camera bindings.')
      navigate('/admin/machines', { replace: true })
    }
  }, [machineId])

  const [mesState, setMesState] = useState({ plants: [], zones: [], lines: [], machines: [] })
  const [cameras, setCameras] = useState([])
  const [form, setForm] = useState(EMPTY_FORM)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  // Inline "Add new camera" panel
  const [newCamOpen, setNewCamOpen] = useState(false)
  const [newCam, setNewCam] = useState(EMPTY_CAMERA)
  const [savingCam, setSavingCam] = useState(false)

  // ── Trigger type derived from parent_plc_id: null = MAIN, int = SUB ──
  const triggerType = form.parent_plc_id ? 'SUB' : 'MAIN'

  const load = async () => {
    setLoading(true)
    try {
      const [state, cams] = await Promise.all([api.getMesState(), api.getCameras()])
      setMesState(state || { plants: [], zones: [], lines: [], machines: [] })
      setCameras(cams || [])

      if (isEdit) {
        const m = (state?.machines || []).find(x => String(x.id) === String(machineId))
        if (m) {
          setForm({
            id:                  m.id,
            line_id:             m.line_id ?? '',
            machine_name:        m.machine_name ?? '',
            plc_ip:              m.plc_ip ?? '',
            plc_port:            m.plc_port ?? 5002,
            protocol:            m.protocol ?? 'MC4E',
            ok_bit_address:      m.ok_bit_address ?? '',
            ng_bit_address:      m.ng_bit_address ?? '',
            status_address:      m.status_address ?? '',
            model_address:       m.model_address ?? '',
            sensor_ok_address:   m.sensor_ok_address ?? '',
            process_seq_address: m.process_seq_address ?? '',
            override_address:    m.override_address ?? '',
            ideal_cycle_time:    m.ideal_cycle_time ?? '',
            max_allowed_cycle:   m.max_allowed_cycle ?? '',
            ok_ng_pulse_min_gap: m.ok_ng_pulse_min_gap ?? '',
            parent_plc_id:       m.parent_plc_id ?? null,
            nf2_camera_id:       m.nf2_camera_id ?? '',
            machine_seq:         m.machine_seq ?? '',
          })
        } else {
          toast.error(`Machine ${machineId} not found in MES`)
        }
      }
    } catch (e) {
      toast.error(e?.message || 'Failed to load MES data — is the MES backend running on :8080?')
    }
    setLoading(false)
  }

  useEffect(() => { load() }, [machineId])

  // Lines filtered to the selected line's zone (display helper for context)
  const lineRow      = useMemo(() => mesState.lines.find(l => l.id === form.line_id), [mesState.lines, form.line_id])
  const zoneOfLine   = useMemo(() => mesState.zones.find(z => z.id === lineRow?.zone_id), [mesState.zones, lineRow])
  // For SUB machines: dropdown of MAIN machines on the same line as candidates for parent_plc_id
  const parentCandidates = useMemo(() => (
    mesState.machines.filter(m => m.line_id === form.line_id && !m.parent_plc_id && m.id !== form.id)
  ), [mesState.machines, form.line_id, form.id])

  // ── Handlers ────────────────────────────────────────────────────────

  const onTriggerTypeChange = (next) => {
    if (next === 'MAIN') {
      setForm(f => ({ ...f, parent_plc_id: null }))
    } else {
      // SUB: clear ok/ng bits (they're MAIN-only).  parent_plc_id stays
      // empty until user picks one.
      setForm(f => ({ ...f, parent_plc_id: '', ok_bit_address: '', ng_bit_address: '' }))
    }
  }

  const saveNewCamera = async () => {
    if (!newCam.name.trim() || !newCam.ip.trim() || !newCam.username.trim() || !newCam.password.trim()) {
      toast.error('Camera name, IP, username, password are required')
      return
    }
    setSavingCam(true)
    try {
      const res = await api.createCamera({
        name:     newCam.name.trim(),
        ip:       newCam.ip.trim(),
        port:     Number(newCam.port) || 554,
        username: newCam.username,
        password: newCam.password,
        path:     newCam.path.trim() || '/h264/ch1/main/av_stream',
      })
      // Refresh camera list and auto-select the new one
      const refreshed = await api.getCameras()
      setCameras(refreshed)
      if (res?.id) {
        setForm(f => ({ ...f, nf2_camera_id: res.id }))
      }
      toast.success('Camera added')
      setNewCamOpen(false)
      setNewCam(EMPTY_CAMERA)
    } catch (e) {
      toast.error(e?.message || 'Failed to add camera')
    }
    setSavingCam(false)
  }

  // 2026-05-18 — Save now only round-trips the existing MES record
  // with the (possibly updated) nf2_camera_id.  All other fields are
  // read-only display and travel back unchanged, so MES sees a no-op
  // on its side while CMS's _sync_binding_from_machine reacts to the
  // camera change.  No validation needed — MES already validated the
  // record when admin saved it there.
  const save = async () => {
    if (!isEdit) return
    setSaving(true)
    try {
      const num = (v) => (v === '' || v === null || v === undefined) ? null : Number(v)
      const payload = {
        ...form,
        line_id:             Number(form.line_id),
        plc_port:            Number(form.plc_port) || 5002,
        ideal_cycle_time:    num(form.ideal_cycle_time),
        max_allowed_cycle:   num(form.max_allowed_cycle),
        ok_ng_pulse_min_gap: num(form.ok_ng_pulse_min_gap),
        parent_plc_id:       form.parent_plc_id ? Number(form.parent_plc_id) : null,
        machine_seq:         form.machine_seq === '' ? null : Number(form.machine_seq),
      }
      await api.saveMesMachine(payload)
      toast.success(form.nf2_camera_id ? 'Camera assignment updated' : 'Camera cleared')
      load()
    } catch (e) {
      toast.error(e?.message || 'Save failed — check MES connectivity')
    }
    setSaving(false)
  }

  if (loading) {
    return <div className="flex items-center justify-center h-64"><div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
  }

  return (
    <div className="space-y-4 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <button onClick={() => navigate('/admin/machines')} className="inline-flex items-center gap-1.5 text-sm text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white">
          <ArrowLeft size={16} /> Back to Machines
        </button>
        <div className="flex gap-2">
          <button onClick={save} disabled={saving || !isEdit} className="btn-primary inline-flex items-center gap-1.5">
            <Save size={14} /> {saving ? 'Saving...' : 'Save Camera Binding'}
          </button>
        </div>
      </div>

      <div>
        <h1 className="page-title">{form.machine_name || 'Machine'}</h1>
        <p className="page-subtitle">
          {zoneOfLine?.zone_name ? `${zoneOfLine.zone_name} → ${lineRow?.line_name} · ` : ''}
          Machine config is owned by MES Admin. Only the camera binding below is editable here.
        </p>
      </div>

      {/* Read-only banner — repeats the rule so admins don't fight the locked fields */}
      <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40 px-3 py-2 flex items-start gap-2 text-xs">
        <Lock size={14} className="flex-shrink-0 mt-0.5 text-slate-500" />
        <span className="text-slate-600 dark:text-slate-300">
          Identity, PLC connection, trigger, and advanced fields are <strong>read-only</strong> here —
          they sync automatically from MES Admin Panel.
          The <strong>Camera</strong> section below is the only editable area.
        </span>
      </div>

      {/* ── Section 1: Identity (READ-ONLY, MES-managed) ───────────── */}
      <Section title="Identity" icon={Cpu} readOnly>
        <div className="grid md:grid-cols-2 gap-3">
          <Field label="Machine name">
            <input className="input-field" value={form.machine_name} disabled readOnly />
          </Field>
          <Field label="Line">
            <input className="input-field"
              value={lineRow?.line_name || ''}
              disabled readOnly />
          </Field>
          <Field label="Sequence">
            <input className="input-field" value={form.machine_seq || ''} disabled readOnly />
          </Field>
        </div>
      </Section>

      {/* ── Section 2: PLC Connection (READ-ONLY) ──────────────────── */}
      <Section title="PLC Connection" icon={Activity} readOnly>
        <div className="grid md:grid-cols-3 gap-3">
          <Field label="PLC IP">
            <input className="input-field font-mono" value={form.plc_ip} disabled readOnly />
          </Field>
          <Field label="PLC Port">
            <input className="input-field font-mono" value={form.plc_port} disabled readOnly />
          </Field>
          <Field label="Protocol">
            <input className="input-field" value={form.protocol} disabled readOnly />
          </Field>
        </div>
      </Section>

      {/* ── Section 3: Trigger Configuration (READ-ONLY) ───────────── */}
      <Section title="Trigger Configuration" icon={Activity} readOnly>
        <div className="space-y-3">
          <div className="text-xs">
            Type: <strong className={triggerType === 'SUB' ? 'text-blue-700 dark:text-blue-300' : 'text-emerald-700 dark:text-emerald-300'}>{triggerType}</strong>
          </div>
          {triggerType === 'MAIN' ? (
            <div className="grid md:grid-cols-2 gap-3">
              <Field label="OK bit address">
                <input className="input-field font-mono" value={form.ok_bit_address} disabled readOnly />
              </Field>
              <Field label="NG bit address">
                <input className="input-field font-mono" value={form.ng_bit_address} disabled readOnly />
              </Field>
            </div>
          ) : (
            <div className="grid md:grid-cols-2 gap-3">
              <Field label="Parent MAIN machine">
                <input className="input-field"
                  value={parentCandidates.find(p => String(p.id) === String(form.parent_plc_id))?.machine_name
                         || mesState.machines.find(p => String(p.id) === String(form.parent_plc_id))?.machine_name
                         || ''}
                  disabled readOnly />
              </Field>
              <Field label="Rising-edge M-bit">
                <input className="input-field font-mono" value={form.process_seq_address} disabled readOnly />
              </Field>
            </div>
          )}
        </div>
      </Section>

      {/* ── Section 4: Camera ─────────────────────────────────────────── */}
      <Section title="Camera" icon={CameraIcon}>
        <div className="space-y-3">
          <div className="flex gap-2 items-end">
            <Field label="Assigned camera" className="flex-1">
              <select className="input-field" value={form.nf2_camera_id}
                onChange={e => setForm(f => ({ ...f, nf2_camera_id: e.target.value }))}>
                <option value="">— No camera —</option>
                {cameras.map(c => (
                  <option key={c.id} value={c.id}>
                    {c.ip}{c.port ? `:${c.port}` : ''} ({c.name})
                  </option>
                ))}
              </select>
            </Field>
            <button type="button" onClick={() => setNewCamOpen(o => !o)}
              className="btn-secondary inline-flex items-center gap-1 whitespace-nowrap">
              <Plus size={14} /> Add new
            </button>
          </div>

          {newCamOpen && (
            <div className="rounded-lg border border-dashed border-blue-300 dark:border-blue-700 bg-blue-50/30 dark:bg-blue-900/10 p-3 space-y-3">
              <p className="text-xs font-semibold text-blue-700 dark:text-blue-300">New camera credentials — will be encrypted at rest</p>
              <div className="grid md:grid-cols-2 gap-2">
                <Field label="Name *"><input className="input-field" value={newCam.name} onChange={e => setNewCam(c => ({ ...c, name: e.target.value }))} placeholder="e.g. Cell-3 Front" /></Field>
                <Field label="IP *"><input className="input-field font-mono" value={newCam.ip} onChange={e => setNewCam(c => ({ ...c, ip: e.target.value }))} placeholder="192.168.10.118" /></Field>
                <Field label="Port"><input type="number" className="input-field font-mono" value={newCam.port} onChange={e => setNewCam(c => ({ ...c, port: e.target.value }))} /></Field>
                <Field label="RTSP path"><input className="input-field font-mono" value={newCam.path} onChange={e => setNewCam(c => ({ ...c, path: e.target.value }))} /></Field>
                <Field label="Username *"><input className="input-field" value={newCam.username} onChange={e => setNewCam(c => ({ ...c, username: e.target.value }))} /></Field>
                <Field label="Password *"><input type="password" className="input-field" value={newCam.password} onChange={e => setNewCam(c => ({ ...c, password: e.target.value }))} /></Field>
              </div>
              <div className="flex justify-end gap-2">
                <button className="btn-secondary text-sm" onClick={() => { setNewCamOpen(false); setNewCam(EMPTY_CAMERA) }}>Cancel</button>
                <button className="btn-primary text-sm" onClick={saveNewCamera} disabled={savingCam}>
                  {savingCam ? 'Saving...' : 'Save Camera'}
                </button>
              </div>
            </div>
          )}
        </div>
      </Section>

      {/* ── Section 5: Advanced (READ-ONLY, MES-managed) ───────────── */}
      <Section title="Advanced" icon={Cpu} readOnly>
        <div className="grid md:grid-cols-3 gap-3">
          <Field label="Status address"><input className="input-field font-mono" value={form.status_address} disabled readOnly /></Field>
          <Field label="Model address"><input className="input-field font-mono" value={form.model_address} disabled readOnly /></Field>
          <Field label="Sensor-OK address"><input className="input-field font-mono" value={form.sensor_ok_address} disabled readOnly /></Field>
          <Field label="Override address"><input className="input-field font-mono" value={form.override_address} disabled readOnly /></Field>
          <Field label="Ideal cycle (s)"><input className="input-field" value={form.ideal_cycle_time} disabled readOnly /></Field>
          <Field label="Max cycle (s)"><input className="input-field" value={form.max_allowed_cycle} disabled readOnly /></Field>
          <Field label="Min OK/NG gap (s)"><input className="input-field" value={form.ok_ng_pulse_min_gap} disabled readOnly /></Field>
        </div>
      </Section>
    </div>
  )
}

// ── Small layout helpers ────────────────────────────────────────────

function Section({ title, icon: Icon, children, readOnly = false }) {
  return (
    <div className="bg-white dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-xl p-4 md:p-5">
      <h3 className="flex items-center gap-2 text-sm font-bold text-slate-700 dark:text-slate-200 uppercase tracking-wider mb-3">
        {Icon && <Icon size={14} />} {title}
        {readOnly && (
          <span className="ml-auto inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400 normal-case tracking-normal">
            <Lock size={10} /> MES-managed
          </span>
        )}
      </h3>
      {children}
    </div>
  )
}

function Field({ label, children, className = '' }) {
  return (
    <label className={`block ${className}`}>
      <span className="block text-xs font-semibold text-slate-600 dark:text-slate-300 mb-1">{label}</span>
      {children}
    </label>
  )
}
