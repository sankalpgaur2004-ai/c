import React, { useState } from 'react'
import { X, ChevronLeft, Database, FileText, LayoutDashboard, ChevronRight, CheckCircle2, Loader2, AlertCircle, Upload, Link, Table2, ClipboardPaste, Cloud, Globe } from 'lucide-react'
import axios from 'axios'
import { cn } from './lib/utils'
import { API_BASE } from './config'
import { useNotebook } from './NotebookContext'

type View = 'menu' | 'database' | 'document' | 'dashboard' | 'paste' | 'website' | 'sharepoint-db' | 'sharepoint-doc'
type DbType = 'sqlite' | 'postgresql' | 'mysql' | 'csv' | 'databricks' | 'snowflake' | 'sharepoint'
type DbStep = 'form' | 'tables' | 'done'

interface Props { onClose: () => void; mandatory?: boolean }

const AddSourcesOverlay: React.FC<Props> = ({ onClose, mandatory = false }) => {
  const [view, setView] = useState<View>('menu')
  const goBack = () => setView('menu')

  return (
    /* ── Backdrop ── */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(15,23,42,0.45)', backdropFilter: 'blur(2px)' }}
      onClick={e => { if (!mandatory && e.target === e.currentTarget) onClose() }}
    >
      {/* ── Modal panel ── */}
      <div className={cn(
        'relative w-full max-w-lg bg-white rounded-2xl shadow-lg',
        'flex flex-col overflow-hidden animate-slide-up',
        'max-h-[85vh]',
      )}>

        {/* Header */}
        <div className="flex items-center gap-3 px-6 py-4 border-b border-border shrink-0">
          {view !== 'menu' && (
            <button onClick={goBack} className="p-1.5 rounded-lg text-ink-faint hover:bg-[#2D5A8C]-muted hover:text-[#2D5A8C] transition-colors">
              <ChevronLeft size={16} />
            </button>
          )}
          <div className="flex-1">
            <h2 className="text-base font-semibold text-[#2D5A8C]">
              {view === 'menu'           && 'Add sources'}
              {view === 'database'       && 'Connect a database'}
              {view === 'document'       && 'Upload documents'}
              {view === 'paste'          && 'Paste text'}
              {view === 'website'        && 'Scrape a website'}
              {view === 'dashboard'      && 'Connect a dashboard'}
              {view === 'sharepoint-db'  && 'SharePoint — Data files'}
              {view === 'sharepoint-doc' && 'SharePoint — Documents'}
            </h2>
            <p className="text-xs text-ink-faint mt-0.5">
              {view === 'menu' && mandatory && (
                <span className="text-sm text-red-600 font-medium">
                  Add at least one source to start working with this project
                </span>
              )}
              {view === 'menu'           && !mandatory && 'Choose what you want to connect to this notebook'}
              {view === 'database'       && 'Connect your database to start querying with AI'}
              {view === 'document'       && 'Upload PDF, Word or PowerPoint files'}
              {view === 'paste'          && 'Type or paste any text to add it as a source'}
              {view === 'website'        && 'Enter a URL — we scrape and index the content'}
              {view === 'dashboard'      && 'Enter your Power BI credentials to connect'}
              {view === 'sharepoint-db'  && 'Browse and import CSV / Excel files from SharePoint'}
              {view === 'sharepoint-doc' && 'Browse and import documents from SharePoint'}
            </p>
          </div>
          {!mandatory && (
            <button onClick={onClose} className="p-1.5 rounded-lg text-ink-faint hover:bg-[#2D5A8C]-muted hover:text-[#2D5A8C] transition-colors">
              <X size={16} />
            </button>
          )}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto">
          {view === 'menu'           && <SourceMenu onSelect={setView} />}
          {view === 'database'       && <AddDatabaseFlow onDone={onClose} />}
          {view === 'document'       && <AddDocumentFlow onDone={onClose} onPaste={() => setView('paste')} onWebsite={() => setView('website')} onSharePoint={() => setView('sharepoint-doc')} />}
          {view === 'paste'          && <PasteTextFlow onDone={onClose} />}
          {view === 'website'        && <AddWebsiteFlow onDone={onClose} />}
          {view === 'dashboard'      && <AddDashboardFlow onDone={onClose} />}
          {view === 'sharepoint-db'  && <SharePointDbFlow onDone={onClose} />}
          {view === 'sharepoint-doc' && <SharePointDocFlow onDone={onClose} />}
        </div>
      </div>
    </div>
  )
}

/* ── Source menu ─────────────────────────────────────────────────── */
const SourceMenu: React.FC<{ onSelect: (v: View) => void }> = ({ onSelect }) => (
  <div className="p-6 flex flex-col gap-3">
    {[
      {
        key: 'database' as View,
        icon: <Database size={24} />,
        label: 'Database',
        desc: 'SQLite, PostgreSQL, MySQL, Snowflake, Databricks, CSV',
        color: 'bg-[#2D5A8C]-muted text-[#2D5A8C]',
      },
      {
        key: 'document' as View,
        icon: <FileText size={24} />,
        label: 'Documents',
        desc: 'Upload files or paste text directly',
        color: 'bg-tertiary-muted text-tertiary',
      },
      {
        key: 'website' as View,
        icon: <Globe size={24} />,
        label: 'Website',
        desc: 'Scrape any public URL and query its content with AI',
        color: 'bg-tertiary-muted text-tertiary',
      },
      {
        key: 'dashboard' as View,
        icon: <LayoutDashboard size={24} />,
        label: 'Dashboard',
        desc: 'Embed and query a Power BI report with AI',
        color: 'bg-[#2D5A8C]-muted text-[#2D5A8C]',
      },
    ].map(opt => (
      <button
        key={opt.key}
        onClick={() => onSelect(opt.key)}
        className={cn(
          'flex items-center gap-4 p-4 rounded-xl text-left w-full',
          'border border-border hover:border-[#2D5A8C] hover:shadow-md',
          'transition-all duration-200 group bg-white',
        )}
      >
        <div className={cn('w-12 h-12 rounded-xl flex items-center justify-center shrink-0', opt.color)}>
          {opt.icon}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-ink">{opt.label}</p>
          <p className="text-xs text-ink-faint mt-0.5">{opt.desc}</p>
        </div>
        <ChevronRight size={16} className="text-ink-faint group-hover:text-[#2D5A8C] shrink-0 transition-colors" />
      </button>
    ))}
  </div>
)

/* ══════════════════════════════════════════════════════════════════
   ADD DATABASE FLOW
══════════════════════════════════════════════════════════════════ */
const AddDatabaseFlow: React.FC<{ onDone: () => void }> = ({ onDone }) => {
  const { refreshSources, notebook } = useNotebook()
  const [activeTab, setActiveTab] = useState<DbType>('sqlite')
  const [step, setStep]           = useState<DbStep>('form')
  const [loading, setLoading]     = useState(false)
  const [error, setError]         = useState<string | null>(null)
  const [lastAlias, setLastAlias] = useState('')

  const [aliasInput, setAliasInput]   = useState('')
  const [sqlitePath, setSqlitePath]   = useState('')
  const [csvFile, setCsvFile]         = useState<File | null>(null)
  const [dbForm, setDbForm]           = useState({ host: 'localhost', port: '3306', user: 'root', password: '', database: '' })
  const [databricksForm, setDatabricksForm] = useState({ server_hostname: '', http_path: '', access_token: '', schema: 'default' })
  const [snowflakeForm, setSnowflakeForm]   = useState({ account: '', user: '', password: '', warehouse: 'COMPUTE_WH', database: '', schema: 'PUBLIC' })
  const [allTables, setAllTables]     = useState<string[]>([])
  const [selected, setSelected]       = useState<string[]>([])
  const [schemaReady, setSchemaReady]     = useState(false)
  const [schemaStep, setSchemaStep]       = useState('Starting…')
  const [schemaPercent, setSchemaPercent] = useState(0)

  // ── Poll schema progress once tables are saved and generation kicks off ──
  React.useEffect(() => {
    if (step !== 'done' || !lastAlias) return
    let cancelled = false

    const interval = setInterval(async () => {
      if (cancelled) { clearInterval(interval); return }
      try {
        const res = await axios.get(`${API_BASE}/api/datasources/${lastAlias}/schema/progress`, { withCredentials: true })
        const { status, step: s, percent } = res.data
        if (!cancelled) {
          // 'idle' means no generation was ever queued — the backend takes a
          // fast path when every selected table was already fully described,
          // so there's nothing to wait for.
          if (status === 'idle') {
            setSchemaReady(true)
            clearInterval(interval)
            return
          }
          setSchemaStep(s || 'Building schema…')
          setSchemaPercent(percent || 0)
          if (status === 'done' || status === 'error') {
            setSchemaReady(true)
            clearInterval(interval)
          }
        }
      } catch { /* ignore poll errors */ }
    }, 2000)

    return () => { cancelled = true; clearInterval(interval) }
  }, [step, lastAlias])

  const onConnected = (tables: string[], alias: string) => {
    setLastAlias(alias)
    const namespaced = tables.map(t => `${alias}.${t}`)
    setAllTables(namespaced)
    setSelected(namespaced)
    setStep('tables')
  }

  const connectSQLite = async () => {
    setLoading(true); setError(null)
    try {
      const r = await axios.post(`${API_BASE}/api/datasources/connect-sqlite`,
        { db_path: sqlitePath, alias: aliasInput || undefined, notebook_id: notebook?.id },
        { withCredentials: true })
      onConnected(r.data.tables, r.data.alias)
    } catch (e: any) { setError(e.response?.data?.detail || 'Connection failed') }
    finally { setLoading(false) }
  }

  const connectDB = async (type: 'postgresql' | 'mysql') => {
    setLoading(true); setError(null)
    try {
      const ep = type === 'postgresql' ? 'connect-postgresql' : 'connect-mysql'
      const r = await axios.post(`${API_BASE}/api/datasources/${ep}`,
        { host: dbForm.host, port: parseInt(dbForm.port), user: dbForm.user, password: dbForm.password, database: dbForm.database, alias: aliasInput || undefined, notebook_id: notebook?.id },
        { withCredentials: true })
      onConnected(r.data.tables, r.data.alias)
    } catch (e: any) { setError(e.response?.data?.detail || 'Connection failed') }
    finally { setLoading(false) }
  }

  const connectCSV = async () => {
    if (!csvFile) return
    setLoading(true); setError(null)
    const fd = new FormData()
    fd.append('file', csvFile)
    if (aliasInput) fd.append('alias', aliasInput)
    if (notebook?.id) fd.append('notebook_id', notebook.id)
    try {
      const r = await axios.post(`${API_BASE}/api/datasources/connect-csv`, fd, { withCredentials: true })
      onConnected(r.data.tables, r.data.alias)
    } catch (e: any) { setError(e.response?.data?.detail || 'Upload failed') }
    finally { setLoading(false) }
  }

  const connectDatabricks = async () => {
    setLoading(true); setError(null)
    try {
      const r = await axios.post(`${API_BASE}/api/datasources/connect-databricks`,
        { ...databricksForm, alias: aliasInput || undefined, notebook_id: notebook?.id },
        { withCredentials: true })
      onConnected(r.data.tables, r.data.alias)
    } catch (e: any) { setError(e.response?.data?.detail || 'Connection failed') }
    finally { setLoading(false) }
  }

  const connectSnowflake = async () => {
    setLoading(true); setError(null)
    try {
      const r = await axios.post(`${API_BASE}/api/datasources/connect-snowflake`,
        { ...snowflakeForm, alias: aliasInput || undefined, notebook_id: notebook?.id },
        { withCredentials: true })
      onConnected(r.data.tables, r.data.alias)
    } catch (e: any) { setError(e.response?.data?.detail || 'Connection failed') }
    finally { setLoading(false) }
  }

  const handleSaveTables = async () => {
    if (selected.length === 0) return
    setLoading(true); setError(null)
    try {
      const params = notebook?.id ? `?notebook_id=${notebook.id}` : ''
      await axios.post(`${API_BASE}/api/datasources/update-table-filter${params}`,
        { tables: selected },
        { withCredentials: true })
      await refreshSources()
      setStep('done')
    } catch (e: any) { setError(e.response?.data?.detail || 'Failed to save tables') }
    finally { setLoading(false) }
  }

  const toggleTable = (t: string) =>
    setSelected(s => s.includes(t) ? s.filter(x => x !== t) : [...s, t])

  if (step === 'done') return (
    <div className="flex flex-col items-center justify-center gap-4 p-10 text-center">
      <div className="w-14 h-14 rounded-full bg-[#2D5A8C]-muted flex items-center justify-center">
        <CheckCircle2 size={32} className="text-[#2D5A8C]" />
      </div>
      <div>
        <p className="text-base font-semibold text-ink">Database connected!</p>
        <p className="text-sm text-ink-muted mt-1">{lastAlias} is ready to query in the chat.</p>
      </div>
      {!schemaReady && (
        <div className="w-full max-w-xs bg-surface rounded-xl border border-border p-4 flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <Loader2 size={14} className="animate-spin text-[#2D5A8C] shrink-0" />
            <span className="text-xs font-medium text-ink">Building AI schema…</span>
          </div>
          <p className="text-[11px] text-ink-faint text-left">{schemaStep}</p>
          <div className="w-full bg-border rounded-full h-1.5 overflow-hidden">
            <div
              className="bg-[#2D5A8C] h-1.5 rounded-full transition-all duration-500"
              style={{ width: `${schemaPercent}%` }}
            />
          </div>
          <p className="text-[10px] text-ink-faint text-right">{schemaPercent}%</p>
        </div>
      )}
      {schemaReady && (
        <div className="flex items-center gap-1.5 text-xs text-green-600 bg-green-50 border border-green-200 rounded-lg px-3 py-1.5">
          <CheckCircle2 size={12} />
          Schema ready — AI can now query this source
        </div>
      )}
      <button onClick={onDone} className="btn-md btn-primary mt-2">Done</button>
    </div>
  )

  if (step === 'tables') return (
    <div className="p-6 flex flex-col gap-4">
      <div>
        <p className="text-sm font-semibold text-ink">Select tables to include</p>
        <p className="text-xs text-ink-faint mt-0.5">Only selected tables will be available for querying.</p>
      </div>
      <div className="flex gap-3 text-xs">
        <button onClick={() => setSelected([...allTables])} className="text-[#2D5A8C] hover:underline">Select all</button>
        <span className="text-ink-faint">·</span>
        <button onClick={() => setSelected([])} className="text-[#2D5A8C] hover:underline">Deselect all</button>
        <span className="ml-auto text-ink-faint">{selected.length} / {allTables.length} selected</span>
      </div>
      <div className="flex flex-col gap-1.5 max-h-64 overflow-y-auto pr-1">
        {allTables.map(t => (
          <label key={t} className={cn(
            'flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer border transition-colors duration-150',
            selected.includes(t) ? 'border-[#2D5A8C] bg-[#2D5A8C]-muted' : 'border-border hover:border-[#2D5A8C]/40',
          )}>
            <input type="checkbox" checked={selected.includes(t)} onChange={() => toggleTable(t)} className="accent-secondary w-3.5 h-3.5" />
            <Table2 size={13} className="text-ink-faint shrink-0" />
            <span className="text-xs text-ink">{t}</span>
          </label>
        ))}
      </div>
      {error && <ErrorBanner msg={error} />}
      <div className="flex gap-2 pt-1">
        <button onClick={() => setStep('form')} className="btn-md btn-outline flex-1">Back</button>
        <button onClick={handleSaveTables} disabled={loading || selected.length === 0} className="btn-md btn-primary flex-1">
          {loading ? <Loader2 size={14} className="animate-spin" /> : `Save ${selected.length} table${selected.length !== 1 ? 's' : ''}`}
        </button>
      </div>
    </div>
  )

  return (
    <div className="p-6 flex flex-col gap-4">
      {/* Optional alias */}
      <div>
        <label className="text-xs font-medium text-ink-muted mb-1.5 block">
          Connection name <span className="text-ink-faint font-normal">(optional — auto-generated if blank)</span>
        </label>
        <input value={aliasInput} onChange={e => setAliasInput(e.target.value)}
          placeholder='e.g. "sales_db"' className="input text-sm" />
      </div>

      {/* DB type tabs */}
      <div>
        <label className="text-xs font-medium text-ink-muted mb-1.5 block">Database type</label>
        <div className="flex flex-wrap gap-2">
          {([
            { key: 'sqlite',     label: 'SQLite',     icon: '/sqlite.svg' },
            { key: 'postgresql', label: 'PostgreSQL', icon: '/postgre.svg' },
            { key: 'mysql',      label: 'MySQL',      icon: '/mysql.png' },
            { key: 'databricks', label: 'Databricks', icon: '/databricks.png' },
            { key: 'snowflake',  label: 'Snowflake',  icon: '/snowflake.png' },
            { key: 'csv',        label: 'CSV',        icon: null },
            { key: 'sharepoint', label: 'SharePoint', icon: '/sharepoint.svg' },
          ] as { key: DbType; label: string; icon: string | null }[]).map(({ key, label, icon }) => (
            <button key={key} onClick={() => { setActiveTab(key); setError(null) }}
              className={cn(
                'flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium border transition-all duration-150',
                activeTab === key
                  ? 'bg-[#2D5A8C] text-white border-[#2D5A8C] shadow-sm'
                  : 'bg-white text-ink border-border hover:border-[#2D5A8C] hover:bg-[#2D5A8C]-muted'
              )}>
              {icon
                ? <img src={icon} alt={label} className={cn("object-contain", label === 'SharePoint' ? "w-6 h-6" : "w-4 h-4")} />
                : <span className="w-4 h-4 flex items-center justify-center text-[10px] font-bold">CSV</span>
              }
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* SQLite */}
      {activeTab === 'sqlite' && (
        <>
          <InfoBanner msg="Enter the full file path to your .db or .sqlite file on the server." />
          <Field label="Database file path" value={sqlitePath} onChange={setSqlitePath} placeholder="/path/to/database.db" />
          <ConnectBtn loading={loading} onClick={connectSQLite} label="Connect SQLite" />
        </>
      )}

      {/* PostgreSQL / MySQL */}
      {(activeTab === 'postgresql' || activeTab === 'mysql') && (
        <>
          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-2">
              <Field label="Host" value={dbForm.host} onChange={v => setDbForm(p => ({ ...p, host: v }))} placeholder="localhost" />
            </div>
            <Field label="Port" value={dbForm.port} onChange={v => setDbForm(p => ({ ...p, port: v }))} placeholder={activeTab === 'postgresql' ? '5432' : '3306'} />
          </div>
          <Field label="Database name" value={dbForm.database} onChange={v => setDbForm(p => ({ ...p, database: v }))} placeholder="my_database" />
          <div className="grid grid-cols-2 gap-3">
            <Field label="User" value={dbForm.user} onChange={v => setDbForm(p => ({ ...p, user: v }))} placeholder="root" />
            <Field label="Password" value={dbForm.password} onChange={v => setDbForm(p => ({ ...p, password: v }))} placeholder="••••••••" type="password" />
          </div>
          <ConnectBtn loading={loading} onClick={() => connectDB(activeTab as 'postgresql' | 'mysql')}
            label={`Connect ${activeTab === 'postgresql' ? 'PostgreSQL' : 'MySQL'}`} />
        </>
      )}

      {/* Databricks */}
      {activeTab === 'databricks' && (
        <>
          <InfoBanner msg="Find these in Databricks → SQL Warehouses → your warehouse → Connection details." />
          <Field label="Server hostname" value={databricksForm.server_hostname} onChange={v => setDatabricksForm(p => ({ ...p, server_hostname: v }))} placeholder="adb-xxx.azuredatabricks.net" />
          <Field label="HTTP path" value={databricksForm.http_path} onChange={v => setDatabricksForm(p => ({ ...p, http_path: v }))} placeholder="/sql/1.0/warehouses/xxx" />
          <Field label="Access token" value={databricksForm.access_token} onChange={v => setDatabricksForm(p => ({ ...p, access_token: v }))} placeholder="dapixxxxxxxxxxxxxxxx" type="password" />
          <Field label="Schema" value={databricksForm.schema} onChange={v => setDatabricksForm(p => ({ ...p, schema: v }))} placeholder="default" />
          <ConnectBtn loading={loading} disabled={!databricksForm.server_hostname || !databricksForm.access_token}
            onClick={connectDatabricks} label="Connect Databricks" />
        </>
      )}

      {/* Snowflake */}
      {activeTab === 'snowflake' && (
        <>
          <InfoBanner msg="Account format: orgname-accountname — found in your Snowflake URL." />
          <Field label="Account" value={snowflakeForm.account} onChange={v => setSnowflakeForm(p => ({ ...p, account: v }))} placeholder="myorg-myaccount" />
          <div className="grid grid-cols-2 gap-3">
            <Field label="User" value={snowflakeForm.user} onChange={v => setSnowflakeForm(p => ({ ...p, user: v }))} placeholder="myuser" />
            <Field label="Password" value={snowflakeForm.password} onChange={v => setSnowflakeForm(p => ({ ...p, password: v }))} placeholder="••••••••" type="password" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Warehouse" value={snowflakeForm.warehouse} onChange={v => setSnowflakeForm(p => ({ ...p, warehouse: v }))} placeholder="COMPUTE_WH" />
            <Field label="Schema" value={snowflakeForm.schema} onChange={v => setSnowflakeForm(p => ({ ...p, schema: v }))} placeholder="PUBLIC" />
          </div>
          <Field label="Database" value={snowflakeForm.database} onChange={v => setSnowflakeForm(p => ({ ...p, database: v }))} placeholder="MY_DATABASE" />
          <ConnectBtn loading={loading} disabled={!snowflakeForm.account || !snowflakeForm.user}
            onClick={connectSnowflake} label="Connect Snowflake" />
        </>
      )}

      {/* CSV */}
      {activeTab === 'csv' && (
        <>
          <InfoBanner msg="Upload a CSV file — it will be converted into a queryable table." />
          <div>
            <label className="text-xs font-medium text-ink-muted mb-1.5 block">Select CSV file</label>
            <input type="file" accept=".csv" onChange={e => setCsvFile(e.target.files?.[0] || null)} className="input text-xs py-1.5" />
            {csvFile && <p className="text-xs text-ink-muted mt-1">{csvFile.name} — {(csvFile.size/1024).toFixed(1)} KB</p>}
          </div>
          <ConnectBtn loading={loading} disabled={!csvFile} onClick={connectCSV} label="Upload & Connect" />
        </>
      )}

      {/* SharePoint */}
      {activeTab === 'sharepoint' && (
        <SharePointDbFlow onDone={onDone} />
      )}

      {error && <ErrorBanner msg={error} />}
    </div>
  )
}

/* ══════════════════════════════════════════════════════════
   ADD DOCUMENT FLOW — drop zone + two bottom pill buttons
══════════════════════════════════════════════════════════ */
const AddDocumentFlow: React.FC<{ onDone: () => void; onPaste: () => void; onWebsite: () => void; onSharePoint: () => void }> = ({ onDone, onPaste, onWebsite, onSharePoint }) => {
  const [dragging, setDragging] = useState(false)
  const [droppedFile, setDroppedFile] = useState<File | null>(null)

  if (droppedFile) return <UploadFileFlow onDone={onDone} preloadedFile={droppedFile} />

  return (
    <div className="p-6 flex flex-col gap-4">
      {/* ── Drop zone ── */}
      <div
        onDragOver={e => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={e => { e.preventDefault(); setDragging(false); const f = e.dataTransfer.files[0]; if (f) setDroppedFile(f) }}
        className={cn(
          'rounded-2xl border-2 border-dashed flex flex-col items-center justify-center gap-2 text-center transition-colors duration-150',
          'py-14 px-6',
          dragging ? 'border-[#2D5A8C] bg-[#2D5A8C]-muted' : 'border-border bg-surface',
        )}
      >
        <p className="text-base font-medium text-ink">or drop your files</p>
        <p className="text-xs text-ink-faint">PDF, Word, PowerPoint, CSV, TXT</p>
      </div>

      {/* ── Bottom pill buttons ── */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => document.getElementById('doc-input-trigger')?.click()}
          className="flex items-center gap-2 px-4 py-2.5 rounded-full border border-border bg-white hover:border-[#2D5A8C] hover:bg-[#2D5A8C]-muted transition-all text-sm font-medium text-ink"
        >
          <Upload size={15} className="text-ink-faint" />
          Upload files
        </button>
        <button
          onClick={onPaste}
          className="flex items-center gap-2 px-4 py-2.5 rounded-full border border-border bg-white hover:border-[#2D5A8C] hover:bg-[#2D5A8C]-muted transition-all text-sm font-medium text-ink"
        >
          <ClipboardPaste size={15} className="text-ink-faint" />
          Paste text
        </button>
        <button
          onClick={onWebsite}
          className="flex items-center gap-2 px-4 py-2.5 rounded-full border border-border bg-white hover:border-[#2D5A8C] hover:bg-[#2D5A8C]-muted transition-all text-sm font-medium text-ink"
        >
          <Globe size={15} className="text-ink-faint" />
          Website URL
        </button>
        <button
          onClick={onSharePoint}
          className="flex items-center gap-2 px-4 py-2.5 rounded-full border border-border bg-white hover:border-[#2D5A8C] hover:bg-[#2D5A8C]-muted transition-all text-sm font-medium text-ink"
        >
          <img src="/sharepoint.svg" alt="SharePoint" className="w-6 h-6 object-contain" />
          SharePoint
        </button>
      </div>

      {/* Hidden file input */}
      <input
        id="doc-input-trigger"
        type="file"
        accept=".pdf,.doc,.docx,.ppt,.pptx,.csv,.txt"
        className="hidden"
        onChange={e => { const f = e.target.files?.[0]; if (f) setDroppedFile(f) }}
      />
    </div>
  )
}

/* ── Upload file sub-flow (extracted from original AddDocumentFlow) ── */
const UploadFileFlow: React.FC<{ onDone: () => void; preloadedFile?: File | null }> = ({ onDone, preloadedFile }) => {
  const { refreshSources, notebook } = useNotebook()
  const [file, setFile]           = useState<File | null>(preloadedFile ?? null)
  const [category, setCategory]   = useState('general')
  const [description, setDesc]    = useState('')
  const [uploading, setUploading] = useState(false)
  const [error, setError]         = useState<string | null>(null)
  const [done, setDone]           = useState(false)

  const handleUpload = async () => {
    if (!file) { setError('Please select a file'); return }
    setUploading(true); setError(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('category', category)
      fd.append('description', description)
      if (notebook?.id) fd.append('notebook_id', notebook.id)
      await axios.post(`${API_BASE}/api/documents/upload`, fd, { withCredentials: true })
      await refreshSources()
      setDone(true)
    } catch (e: any) { setError(e.response?.data?.detail || 'Upload failed') }
    finally { setUploading(false) }
  }

  if (done) return (
    <div className="flex flex-col items-center justify-center gap-4 p-10 text-center">
      <div className="w-14 h-14 rounded-full bg-[#2D5A8C]-muted flex items-center justify-center">
        <CheckCircle2 size={32} className="text-[#2D5A8C]" />
      </div>
      <div>
        <p className="text-base font-semibold text-ink">Document uploaded!</p>
        <p className="text-sm text-ink-muted mt-1">{file?.name} has been indexed and is ready.</p>
      </div>
      <button onClick={onDone} className="btn-md btn-primary mt-2">Done</button>
    </div>
  )

  return (
    <div className="p-6 flex flex-col gap-4">
      {file && (
        <div className="flex items-center gap-3 px-4 py-3 bg-[#2D5A8C]-muted rounded-xl border border-[#2D5A8C]/20">
          <FileText size={16} className="text-[#2D5A8C] shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium text-ink truncate">{file.name}</p>
            <p className="text-[10px] text-ink-faint">{(file.size/1024).toFixed(1)} KB</p>
          </div>
          <button onClick={() => setFile(null)} className="text-ink-faint hover:text-tertiary transition-colors">
            <X size={14} />
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 gap-3">
        <div>
          <label className="text-xs font-medium text-ink-muted mb-1.5 block">Category</label>
          <select value={category} onChange={e => setCategory(e.target.value)} className="input text-sm">
            {['general','business_rules','policy','definition','column_definitions'].map(c => (
              <option key={c} value={c}>{c.replace(/_/g,' ')}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs font-medium text-ink-muted mb-1.5 block">Description <span className="text-ink-faint font-normal">(optional)</span></label>
          <textarea value={description} onChange={e => setDesc(e.target.value)} rows={2}
            placeholder="Briefly describe this document…" className="input text-sm resize-none" />
        </div>
      </div>

      {error && <ErrorBanner msg={error} />}
      <button onClick={handleUpload} disabled={uploading || !file} className="btn-md btn-primary w-full">
        {uploading ? <><Loader2 size={14} className="animate-spin" /> Uploading…</> : 'Upload document'}
      </button>
    </div>
  )
}

/* ══════════════════════════════════════════════════════════════════
   PASTE TEXT FLOW
══════════════════════════════════════════════════════════════════ */
const PasteTextFlow: React.FC<{ onDone: () => void }> = ({ onDone }) => {
  const { refreshSources, notebook } = useNotebook()
  const [title, setTitle]         = useState('')
  const [text, setText]           = useState('')
  const [category, setCategory]   = useState('general')
  const [description, setDesc]    = useState('')
  const [uploading, setUploading] = useState(false)
  const [error, setError]         = useState<string | null>(null)
  const [done, setDone]           = useState(false)

  const handleInsert = async () => {
    if (!text.trim()) { setError('Please paste or type some text'); return }
    setUploading(true); setError(null)
    try {
      await axios.post(`${API_BASE}/api/documents/upload-text`, {
        title: title.trim() || 'Pasted Text',
        text,
        category,
        description,
        notebook_id: notebook?.id,
      }, { withCredentials: true })
      await refreshSources()
      setDone(true)
    } catch (e: any) { setError(e.response?.data?.detail || 'Upload failed') }
    finally { setUploading(false) }
  }

  if (done) return (
    <div className="flex flex-col items-center justify-center gap-4 p-10 text-center">
      <div className="w-14 h-14 rounded-full bg-[#2D5A8C]-muted flex items-center justify-center">
        <CheckCircle2 size={32} className="text-[#2D5A8C]" />
      </div>
      <div>
        <p className="text-base font-semibold text-ink">Text added!</p>
        <p className="text-sm text-ink-muted mt-1">Your text has been indexed and is ready to query.</p>
      </div>
      <button onClick={onDone} className="btn-md btn-primary mt-2">Done</button>
    </div>
  )

  return (
    <div className="p-6 flex flex-col gap-4">
      <Field label="Title" value={title} onChange={setTitle} placeholder="e.g. Q3 Meeting Notes" />

      <div>
        <label className="text-xs font-medium text-ink-muted mb-1.5 block">
          Text <span className="text-ink-faint font-normal">(paste or type here)</span>
        </label>
        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          rows={8}
          placeholder="Paste your text here…"
          className="input text-sm resize-none w-full"
          style={{ minHeight: 180 }}
          autoFocus
        />
        <p className="text-[10px] text-ink-faint mt-1">{text.length.toLocaleString()} characters</p>
      </div>

      <div>
        <label className="text-xs font-medium text-ink-muted mb-1.5 block">Category</label>
        <select value={category} onChange={e => setCategory(e.target.value)} className="input text-sm">
          {['general','business_rules','policy','definition','column_definitions'].map(c => (
            <option key={c} value={c}>{c.replace(/_/g,' ')}</option>
          ))}
        </select>
      </div>

      <Field label="Description" value={description} onChange={setDesc} placeholder="Optional note about this text…" />

      {error && <ErrorBanner msg={error} />}

      <button onClick={handleInsert} disabled={uploading || !text.trim()} className="btn-md btn-primary w-full">
        {uploading ? <><Loader2 size={14} className="animate-spin" /> Inserting…</> : 'Insert text'}
      </button>
    </div>
  )
}

/* ══════════════════════════════════════════════════════════════════
   ADD DASHBOARD FLOW  ← replaced
══════════════════════════════════════════════════════════════════ */
const AddDashboardFlow: React.FC<{ onDone: () => void }> = ({ onDone }) => {
  const { refreshSources, notebook } = useNotebook()

  const [mode, setMode] = useState<'credentials' | 'pbix'>('credentials')

  const [name, setName]               = useState('Autolus')
  const [connecting, setConnecting]   = useState(false)
  const [error, setError]             = useState<string | null>(null)
  const [done, setDone]               = useState(false)
  const [dashboardId, setDashboardId] = useState<number | null>(null)
  const [schemaReady, setSchemaReady] = useState(false)
  const [schemaStep, setSchemaStep]   = useState<string>('Preparing schema…')
  const [schemaPercent, setSchemaPercent] = useState(0)

  // ── PBIX upload state ──────────────────────────────────────────────────
  const [pbixFile, setPbixFile]           = useState<File | null>(null)
  const [pbixAlias, setPbixAlias]         = useState('')
  const [pbixUploading, setPbixUploading] = useState(false)
  const [pbixError, setPbixError]         = useState<string | null>(null)
  const [pbixDone, setPbixDone]           = useState(false)
  const [pbixReused, setPbixReused]       = useState(false)

  // ── Load credentials from environment ────────────────────────────────
  const [creds, setCreds] = useState({
    azure_client_id:     '',
    azure_client_secret: '',
    azure_tenant_id:     '',
    workspace_id:        '',
    report_id:           '',
    dataset_id:          '',
    workspace_name:      'Agentic BI',
    dataset_name:        'Field Dashboard',
  })

  const setField = (key: string, value: string) =>
    setCreds(prev => ({ ...prev, [key]: value }))

  // ── Poll schema progress after connect ────────────────────────────────
  React.useEffect(() => {
    if (!done || !dashboardId) return
    let cancelled = false

    const poll = async () => {
      try {
        // First check if schema already exists
        const existsRes = await axios.get(`${API_BASE}/api/powerbi/${dashboardId}/schema/exists`, { withCredentials: true })
        if (existsRes.data.exists) {
          if (!cancelled) { setSchemaReady(true); setSchemaStep('Schema ready'); setSchemaPercent(100) }
          return
        }
      } catch { /* ignore, fall through to progress poll */ }

      const interval = setInterval(async () => {
        if (cancelled) { clearInterval(interval); return }
        try {
          const res = await axios.get(`${API_BASE}/api/powerbi/${dashboardId}/schema/progress`, { withCredentials: true })
          const { status, step, percent } = res.data
          if (!cancelled) {
            setSchemaStep(step || 'Building schema…')
            setSchemaPercent(percent || 0)
            if (status === 'done') {
              setSchemaReady(true)
              clearInterval(interval)
            }
          }
        } catch { /* ignore poll errors */ }
      }, 2500)

      return () => clearInterval(interval)
    }

    poll()
    return () => { cancelled = true }
  }, [done, dashboardId])

  const handlePbixUpload = async () => {
    if (!pbixFile) { setPbixError('Please select a .pbix file'); return }
    if (!pbixFile.name.toLowerCase().endsWith('.pbix')) {
      setPbixError('File must be a .pbix'); return
    }

    setPbixUploading(true); setPbixError(null)
    try {
      const fd = new FormData()
      fd.append('file', pbixFile)
      if (notebook?.id) fd.append('notebook_id', notebook.id)
      if (pbixAlias.trim()) fd.append('alias', pbixAlias.trim())

      const res = await axios.post(`${API_BASE}/api/datasources/pbix-upload`, fd, { withCredentials: true })
      await refreshSources()
      setPbixReused(Boolean(res.data.reused_import && res.data.reused_schema))
      setPbixDone(true)
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      setPbixError(
        typeof detail === 'string' ? detail
        : Array.isArray(detail) ? detail.map((d: any) => d.msg).join(', ')
        : 'Upload failed. Please check the file and try again.'
      )
    } finally {
      setPbixUploading(false)
    }
  }

  const handleConnect = async () => {
    if (!name.trim()) { setError('Dashboard name is required'); return }
    const missing = Object.entries(creds)
      .filter(([, v]) => !v.trim() || v.startsWith('YOUR_'))
      .map(([k]) => k)
    if (missing.length) {
      setError(`Please fill in all fields. Missing: ${missing.join(', ')}`)
      return
    }

    setConnecting(true); setError(null)
    try {
      const payload: any = { name: name.trim(), config: creds }
      if (notebook?.id) payload.notebook_id = notebook.id

      const res = await axios.post(`${API_BASE}/api/dashboards/powerbi`, payload, { withCredentials: true })
      await refreshSources()
      setDashboardId(res.data.id)
      setDone(true)
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      setError(
        typeof detail === 'string' ? detail
        : Array.isArray(detail) ? detail.map((d: any) => d.msg).join(', ')
        : 'Connection failed. Please check your credentials.'
      )
    } finally {
      setConnecting(false)
    }
  }

  if (done) return (
    <div className="flex flex-col items-center justify-center gap-4 p-10 text-center">
      <div className="w-14 h-14 rounded-full bg-[#2D5A8C]-muted flex items-center justify-center">
        <CheckCircle2 size={32} className="text-[#2D5A8C]" />
      </div>
      <div>
        <p className="text-base font-semibold text-ink">Dashboard connected!</p>
        <p className="text-sm text-ink-muted mt-1">Your Power BI report is ready to preview and query.</p>
      </div>
      {!schemaReady && (
        <div className="w-full max-w-xs bg-surface rounded-xl border border-border p-4 flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <Loader2 size={14} className="animate-spin text-[#2D5A8C] shrink-0" />
            <span className="text-xs font-medium text-ink">Building AI schema…</span>
          </div>
          <p className="text-[11px] text-ink-faint text-left">{schemaStep}</p>
          <div className="w-full bg-border rounded-full h-1.5 overflow-hidden">
            <div
              className="bg-[#2D5A8C] h-1.5 rounded-full transition-all duration-500"
              style={{ width: `${schemaPercent}%` }}
            />
          </div>
          <p className="text-[10px] text-ink-faint text-right">{schemaPercent}%</p>
        </div>
      )}
      {schemaReady && (
        <div className="flex items-center gap-1.5 text-xs text-green-600 bg-green-50 border border-green-200 rounded-lg px-3 py-1.5">
          <CheckCircle2 size={12} />
          Schema ready — AI can now query this dashboard
        </div>
      )}
      <button onClick={onDone} className="btn-md btn-primary mt-2">Done</button>
    </div>
  )

  if (pbixDone) return (
    <div className="flex flex-col items-center justify-center gap-4 p-10 text-center">
      <div className="w-14 h-14 rounded-full bg-[#2D5A8C]-muted flex items-center justify-center">
        <CheckCircle2 size={32} className="text-[#2D5A8C]" />
      </div>
      <div>
        <p className="text-base font-semibold text-ink">PBIX file uploaded!</p>
        <p className="text-sm text-ink-muted mt-1">
          {pbixReused
            ? 'This matches a file already processed before — reused the existing schema, no rebuild needed.'
            : 'Your data model is extracted and ready to query.'}
        </p>
      </div>
      <button onClick={onDone} className="btn-md btn-primary mt-2">Done</button>
    </div>
  )

  return (
    <div className="p-6 flex flex-col gap-4">

      {/* Mode toggle */}
      <div className="flex gap-1 p-1 bg-surface rounded-xl border border-border">
        <button
          onClick={() => setMode('credentials')}
          className={cn(
            'flex-1 text-xs font-medium py-2 rounded-lg transition-colors',
            mode === 'credentials' ? 'bg-white shadow-sm text-[#2D5A8C]' : 'text-ink-faint hover:text-ink-muted',
          )}
        >
          Enter credentials
        </button>
        <button
          onClick={() => setMode('pbix')}
          className={cn(
            'flex-1 text-xs font-medium py-2 rounded-lg transition-colors',
            mode === 'pbix' ? 'bg-white shadow-sm text-[#2D5A8C]' : 'text-ink-faint hover:text-ink-muted',
          )}
        >
          Upload .pbix file
        </button>
      </div>

      {mode === 'pbix' ? (
        <>
          <InfoBanner msg="Upload a Power BI file directly — no credentials needed. Tables, measures, and relationships are extracted straight from the file." />

          <div>
            <label className="text-xs font-medium text-ink-muted mb-1.5 block">Select .pbix file</label>
            <input
              type="file"
              accept=".pbix"
              onChange={e => setPbixFile(e.target.files?.[0] || null)}
              className="input text-xs py-1.5"
            />
            {pbixFile && (
              <p className="text-xs text-ink-muted mt-1">
                {pbixFile.name} — {(pbixFile.size / (1024 * 1024)).toFixed(1)} MB
              </p>
            )}
          </div>

          <Field
            label="Dashboard name (optional)"
            value={pbixAlias}
            onChange={setPbixAlias}
            placeholder="Defaults to the file name"
          />

          {pbixError && <ErrorBanner msg={pbixError} />}

          <button
            onClick={handlePbixUpload}
            disabled={pbixUploading || !pbixFile}
            className="btn-md btn-primary w-full"
          >
            {pbixUploading
              ? <><Loader2 size={14} className="animate-spin" /> Uploading &amp; analyzing…</>
              : <><Upload size={14} /> Upload &amp; connect</>}
          </button>
          {pbixUploading && (
            <p className="text-[11px] text-ink-faint text-center">
              This can take a minute for larger files — extracting tables and generating descriptions.
            </p>
          )}
        </>
      ) : (
        <>
          <InfoBanner msg="Enter your Azure AD service principal credentials. The report will be embedded and queryable via AI." />

          {/* Dashboard name */}
          <Field label="Dashboard name" value={name} onChange={setName} placeholder="e.g. Sales Overview" />

          {/* Azure AD */}
          <p className="text-xs font-semibold text-ink-muted uppercase tracking-wider mt-1">Azure AD</p>
          <Field label="Tenant ID"     value={creds.azure_tenant_id}     onChange={v => setField('azure_tenant_id', v)}     placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" />
          <Field label="Client ID"     value={creds.azure_client_id}     onChange={v => setField('azure_client_id', v)}     placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" />
          <Field label="Client Secret" value={creds.azure_client_secret} onChange={v => setField('azure_client_secret', v)} placeholder="your-client-secret" type="password" />

          {/* Power BI IDs */}
          <p className="text-xs font-semibold text-ink-muted uppercase tracking-wider mt-1">Power BI</p>
          <Field label="Workspace ID"   value={creds.workspace_id}   onChange={v => setField('workspace_id', v)}   placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" />
          <Field label="Report ID"      value={creds.report_id}      onChange={v => setField('report_id', v)}      placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" />
          <Field label="Dataset ID"     value={creds.dataset_id}     onChange={v => setField('dataset_id', v)}     placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" />
          <Field label="Workspace Name" value={creds.workspace_name} onChange={v => setField('workspace_name', v)} placeholder="e.g. Agentic BI" />
          <Field label="Dataset Name"   value={creds.dataset_name}   onChange={v => setField('dataset_name', v)}   placeholder="e.g. Commercial Leadership Dashboard 1" />

          {error && <ErrorBanner msg={error} />}

          <button onClick={handleConnect} disabled={connecting} className="btn-md btn-primary w-full">
            {connecting ? <><Loader2 size={14} className="animate-spin" /> Connecting…</> : 'Connect dashboard'}
          </button>
        </>
      )}
    </div>
  )
}

/* ══════════════════════════════════════════════════════════════════
   ADD WEBSITE FLOW — URL only, no depth/category options
══════════════════════════════════════════════════════════════════ */
type WebsiteStep = 'form' | 'scraping' | 'done'

const AddWebsiteFlow: React.FC<{ onDone: () => void }> = ({ onDone }) => {
  const { refreshSources, notebook } = useNotebook()

  const [step, setStep]     = useState<WebsiteStep>('form')
  const [url, setUrl]       = useState('')
  const [error, setError]   = useState<string | null>(null)
  const [result, setResult] = useState<{
    domain: string; page_title: string; chunk_count: number
  } | null>(null)

  const handleScrape = async () => {
    const trimmed = url.trim()
    if (!trimmed) { setError('Please enter a URL'); return }
    setError(null)
    setStep('scraping')
    try {
      const res = await axios.post(
        `${API_BASE}/api/documents/scrape-url`,
        { url: trimmed, notebook_id: notebook?.id },
        { withCredentials: true }
      )
      setResult({
        domain:      res.data.domain,
        page_title:  res.data.page_title,
        chunk_count: res.data.chunk_count,
      })
      await refreshSources()
      setStep('done')
    } catch (e: any) {
      setError(e.response?.data?.detail || 'Scraping failed — check the URL and try again')
      setStep('form')
    }
  }

  /* ── Done ── */
  if (step === 'done' && result) return (
    <div className="flex flex-col items-center justify-center gap-4 p-10 text-center">
      <div className="w-14 h-14 rounded-full bg-[#2D5A8C]-muted flex items-center justify-center">
        <CheckCircle2 size={32} className="text-[#2D5A8C]" />
      </div>
      <div>
        <p className="text-base font-semibold text-ink">Website indexed!</p>
        <p className="text-sm text-ink-muted mt-1 max-w-xs">
          "{result.page_title}" has been scraped and is ready to query.
        </p>
      </div>
      <div className="flex items-center gap-3 text-xs text-ink-faint">
        <span className="flex items-center gap-1">
          <Globe size={11} className="text-[#2D5A8C]" />
          {result.domain}
        </span>
        <span>·</span>
        <span>{result.chunk_count} chunks indexed</span>
      </div>
      <button onClick={onDone} className="btn-md btn-primary mt-2">Done</button>
    </div>
  )

  /* ── Scraping ── */
  if (step === 'scraping') return (
    <div className="flex flex-col items-center justify-center gap-5 p-12 text-center">
      <div className="relative w-14 h-14">
        <div className="w-14 h-14 rounded-full border-4 border-[#2D5A8C]/20" />
        <div className="absolute inset-0 rounded-full border-4 border-t-[#2D5A8C] animate-spin" />
        <Globe size={18} className="absolute inset-0 m-auto text-[#2D5A8C]" />
      </div>
      <div>
        <p className="text-sm font-semibold text-ink">Scraping website…</p>
        <p className="text-xs text-ink-muted mt-1">Extracting and indexing content. This takes a few seconds.</p>
      </div>
      <p className="text-[11px] text-ink-faint font-mono truncate max-w-xs">{url}</p>
    </div>
  )

  /* ── Form ── */
  return (
    <div className="p-6 flex flex-col gap-4">
      <InfoBanner msg="Enter any public URL — we'll extract the text content and make it queryable in your notebook." />

      <div>
        <label className="text-xs font-medium text-ink-muted mb-1.5 block">Website URL</label>
        <div className="flex items-center gap-2 border border-border rounded-lg bg-white px-3 focus-within:border-[#2D5A8C] transition-colors">
          <Globe size={14} className="text-ink-faint shrink-0" />
          <input
            type="url"
            value={url}
            onChange={e => { setUrl(e.target.value); setError(null) }}
            onKeyDown={e => e.key === 'Enter' && handleScrape()}
            placeholder="https://example.com/page"
            className="flex-1 bg-transparent py-2.5 text-sm outline-none"
            autoFocus
          />
        </div>
        <p className="text-[10px] text-ink-faint mt-1">Must be a publicly accessible page (no login required).</p>
      </div>

      {error && <ErrorBanner msg={error} />}

      <button
        onClick={handleScrape}
        disabled={!url.trim()}
        className="btn-md btn-primary w-full"
      >
        <Globe size={14} />
        Scrape & index
      </button>
    </div>
  )
}


/* ── Shared helpers ─────────────────────────────────────────────── */
const Field: React.FC<{ label: string; value: string; onChange: (v: string) => void; placeholder?: string; type?: string }> = ({ label, value, onChange, placeholder, type = 'text' }) => (
  <div>
    <label className="text-xs font-medium text-ink-muted mb-1.5 block">{label}</label>
    <input type={type} value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} className="input text-sm" />
  </div>
)

const ConnectBtn: React.FC<{ loading: boolean; onClick: () => void; label: string; disabled?: boolean }> = ({ loading, onClick, label, disabled }) => (
  <button onClick={onClick} disabled={loading || disabled} className="btn-md btn-primary w-full">
    {loading ? <><Loader2 size={14} className="animate-spin" /> Connecting…</> : label}
  </button>
)

const InfoBanner: React.FC<{ msg: string }> = ({ msg }) => (
  <div className="flex items-start gap-2 p-3 bg-[#2D5A8C]-muted rounded-lg border border-[#2D5A8C]/20">
    <span className="text-xs text-[#2D5A8C] leading-snug">{msg}</span>
  </div>
)

const ErrorBanner: React.FC<{ msg: string }> = ({ msg }) => (
  <div className="flex items-start gap-2 p-3 bg-tertiary-muted rounded-lg border border-tertiary/20">
    <AlertCircle size={14} className="text-tertiary shrink-0 mt-0.5" />
    <p className="text-xs text-tertiary leading-snug">{msg}</p>
  </div>
)

/* ══════════════════════════════════════════════════════════════════
   SHAREPOINT FLOWS
══════════════════════════════════════════════════════════════════ */

type SpStep = 'form' | 'files' | 'done'

interface SpFile {
  name: string; extension: string; item_id: string; drive_id: string
  path: string; size: number; pipeline: string
}

/* Shared logo header shown in both browse forms */
const SharePointHeader: React.FC = () => (
  <div className="flex items-center gap-3 p-3 bg-[#2D5A8C]-muted rounded-xl border border-[#2D5A8C]/20">
    <img src="/sharepoint.svg" alt="SharePoint" className="w-8 h-8 object-contain shrink-0" />
    <div>
      <p className="text-xs font-semibold text-ink">Microsoft SharePoint</p>
      <p className="text-[10px] text-ink-faint">Credentials are managed by your administrator</p>
    </div>
  </div>
)

const SharePointDbFlow: React.FC<{ onDone: () => void }> = ({ onDone }) => {
  const { refreshSources, notebook } = useNotebook()
  const [step, setStep]         = useState<SpStep>('form')
  const [siteUrl, setSiteUrl]   = useState('')
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState<string | null>(null)
  const [siteId, setSiteId]     = useState('')
  const [token, setToken]       = useState('')
  const [allFiles, setAllFiles] = useState<SpFile[]>([])
  const [selected, setSelected] = useState<SpFile[]>([])

  const handleBrowse = async () => {
    if (!siteUrl.trim()) { setError('Please enter a SharePoint site URL'); return }
    setLoading(true); setError(null)
    try {
      const r = await axios.post(`${API_BASE}/api/datasources/sharepoint-browse`,
        { site_url: siteUrl.trim(), notebook_id: notebook?.id },
        { withCredentials: true })
      const files: SpFile[] = r.data.db_files
      setSiteId(r.data.site_id); setToken(r.data._token)
      setAllFiles(files); setSelected(files)
      setStep('files')
    } catch (e: any) { setError(e.response?.data?.detail || 'Browse failed') }
    finally { setLoading(false) }
  }

  const handleConnect = async () => {
    if (selected.length === 0) return
    setLoading(true); setError(null)
    try {
      await axios.post(`${API_BASE}/api/datasources/sharepoint-connect`,
        { site_url: siteUrl, site_id: siteId, token, files: selected, notebook_id: notebook?.id },
        { withCredentials: true })
      await refreshSources()
      setStep('done')
    } catch (e: any) { setError(e.response?.data?.detail || 'Connect failed') }
    finally { setLoading(false) }
  }

  const toggle = (f: SpFile) =>
    setSelected(s => s.some(x => x.item_id === f.item_id) ? s.filter(x => x.item_id !== f.item_id) : [...s, f])

  if (step === 'done') return (
    <div className="flex flex-col items-center justify-center gap-4 p-10 text-center">
      <div className="w-14 h-14 rounded-full bg-[#2D5A8C]-muted flex items-center justify-center">
        <CheckCircle2 size={32} className="text-[#2D5A8C]" />
      </div>
      <div>
        <p className="text-base font-semibold text-ink">SharePoint data connected!</p>
        <p className="text-sm text-ink-muted mt-1">{selected.length} file{selected.length !== 1 ? 's' : ''} imported and ready to query.</p>
      </div>
      <button onClick={onDone} className="btn-md btn-primary mt-2">Done</button>
    </div>
  )

  if (step === 'files') return (
    <div className="p-6 flex flex-col gap-4">
      <div>
        <p className="text-sm font-semibold text-ink">Select files to import</p>
        <p className="text-xs text-ink-faint mt-0.5">Only CSV and Excel files are shown — each becomes a queryable table.</p>
      </div>
      {allFiles.length === 0
        ? <InfoBanner msg="No CSV or Excel files found in this SharePoint site." />
        : <>
            <div className="flex gap-3 text-xs">
              <button onClick={() => setSelected([...allFiles])} className="text-[#2D5A8C] hover:underline">Select all</button>
              <span className="text-ink-faint">·</span>
              <button onClick={() => setSelected([])} className="text-[#2D5A8C] hover:underline">Deselect all</button>
              <span className="ml-auto text-ink-faint">{selected.length} / {allFiles.length} selected</span>
            </div>
            <div className="flex flex-col gap-1.5 max-h-64 overflow-y-auto pr-1">
              {allFiles.map(f => (
                <label key={f.item_id} className={cn(
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer border transition-colors duration-150',
                  selected.some(x => x.item_id === f.item_id) ? 'border-[#2D5A8C] bg-[#2D5A8C]-muted' : 'border-border hover:border-[#2D5A8C]/40',
                )}>
                  <input type="checkbox" checked={selected.some(x => x.item_id === f.item_id)} onChange={() => toggle(f)} className="accent-secondary w-3.5 h-3.5" />
                  <Table2 size={13} className="text-ink-faint shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-ink truncate">{f.name}</p>
                    <p className="text-[10px] text-ink-faint">{f.path} · {(f.size / 1024).toFixed(1)} KB</p>
                  </div>
                </label>
              ))}
            </div>
          </>
      }
      {error && <ErrorBanner msg={error} />}
      <div className="flex gap-2 pt-1">
        <button onClick={() => setStep('form')} className="btn-md btn-outline flex-1">Back</button>
        <button onClick={handleConnect} disabled={loading || selected.length === 0} className="btn-md btn-primary flex-1">
          {loading ? <Loader2 size={14} className="animate-spin" /> : `Import ${selected.length} file${selected.length !== 1 ? 's' : ''}`}
        </button>
      </div>
    </div>
  )

  return (
    <div className="p-6 flex flex-col gap-4">
      <SharePointHeader />
      <Field label="SharePoint site URL" value={siteUrl} onChange={setSiteUrl}
        placeholder="https://yourorg.sharepoint.com/sites/YourSite" />
      {error && <ErrorBanner msg={error} />}
      <ConnectBtn loading={loading} disabled={!siteUrl.trim()} onClick={handleBrowse} label="Browse files" />
    </div>
  )
}


const SharePointDocFlow: React.FC<{ onDone: () => void }> = ({ onDone }) => {
  const { refreshSources, notebook } = useNotebook()
  const [step, setStep]         = useState<SpStep>('form')
  const [siteUrl, setSiteUrl]   = useState('')
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState<string | null>(null)
  const [siteId, setSiteId]     = useState('')
  const [token, setToken]       = useState('')
  const [allFiles, setAllFiles] = useState<SpFile[]>([])
  const [selected, setSelected] = useState<SpFile[]>([])
  const [category, setCategory] = useState('general')

  const handleBrowse = async () => {
    if (!siteUrl.trim()) { setError('Please enter a SharePoint site URL'); return }
    setLoading(true); setError(null)
    try {
      const r = await axios.post(`${API_BASE}/api/datasources/sharepoint-browse`,
        { site_url: siteUrl.trim(), notebook_id: notebook?.id },
        { withCredentials: true })
      const files: SpFile[] = r.data.doc_files
      setSiteId(r.data.site_id); setToken(r.data._token)
      setAllFiles(files); setSelected(files)
      setStep('files')
    } catch (e: any) { setError(e.response?.data?.detail || 'Browse failed') }
    finally { setLoading(false) }
  }

  const handleImport = async () => {
    if (selected.length === 0) return
    setLoading(true); setError(null)
    try {
      await axios.post(`${API_BASE}/api/datasources/sharepoint-connect`,
        { site_url: siteUrl, site_id: siteId, token, files: selected, notebook_id: notebook?.id, category },
        { withCredentials: true })
      await refreshSources()
      setStep('done')
    } catch (e: any) { setError(e.response?.data?.detail || 'Import failed') }
    finally { setLoading(false) }
  }

  const toggle = (f: SpFile) =>
    setSelected(s => s.some(x => x.item_id === f.item_id) ? s.filter(x => x.item_id !== f.item_id) : [...s, f])

  if (step === 'done') return (
    <div className="flex flex-col items-center justify-center gap-4 p-10 text-center">
      <div className="w-14 h-14 rounded-full bg-[#2D5A8C]-muted flex items-center justify-center">
        <CheckCircle2 size={32} className="text-[#2D5A8C]" />
      </div>
      <div>
        <p className="text-base font-semibold text-ink">Documents imported!</p>
        <p className="text-sm text-ink-muted mt-1">{selected.length} document{selected.length !== 1 ? 's' : ''} indexed and ready to query.</p>
      </div>
      <button onClick={onDone} className="btn-md btn-primary mt-2">Done</button>
    </div>
  )

  if (step === 'files') return (
    <div className="p-6 flex flex-col gap-4">
      <div>
        <p className="text-sm font-semibold text-ink">Select documents to import</p>
        <p className="text-xs text-ink-faint mt-0.5">PDF, Word, PowerPoint and TXT files found in this site.</p>
      </div>
      {allFiles.length === 0
        ? <InfoBanner msg="No documents found in this SharePoint site." />
        : <>
            <div className="flex gap-3 text-xs">
              <button onClick={() => setSelected([...allFiles])} className="text-[#2D5A8C] hover:underline">Select all</button>
              <span className="text-ink-faint">·</span>
              <button onClick={() => setSelected([])} className="text-[#2D5A8C] hover:underline">Deselect all</button>
              <span className="ml-auto text-ink-faint">{selected.length} / {allFiles.length} selected</span>
            </div>
            <div className="flex flex-col gap-1.5 max-h-52 overflow-y-auto pr-1">
              {allFiles.map(f => (
                <label key={f.item_id} className={cn(
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer border transition-colors duration-150',
                  selected.some(x => x.item_id === f.item_id) ? 'border-[#2D5A8C] bg-[#2D5A8C]-muted' : 'border-border hover:border-[#2D5A8C]/40',
                )}>
                  <input type="checkbox" checked={selected.some(x => x.item_id === f.item_id)} onChange={() => toggle(f)} className="accent-secondary w-3.5 h-3.5" />
                  <FileText size={13} className="text-ink-faint shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-ink truncate">{f.name}</p>
                    <p className="text-[10px] text-ink-faint">{f.path} · {(f.size / 1024).toFixed(1)} KB</p>
                  </div>
                </label>
              ))}
            </div>
            <div>
              <label className="text-xs font-medium text-ink-muted mb-1.5 block">Category</label>
              <select value={category} onChange={e => setCategory(e.target.value)} className="input text-sm">
                {['general','business_rules','policy','definition','column_definitions'].map(c => (
                  <option key={c} value={c}>{c.replace(/_/g,' ')}</option>
                ))}
              </select>
            </div>
          </>
      }
      {error && <ErrorBanner msg={error} />}
      <div className="flex gap-2 pt-1">
        <button onClick={() => setStep('form')} className="btn-md btn-outline flex-1">Back</button>
        <button onClick={handleImport} disabled={loading || selected.length === 0} className="btn-md btn-primary flex-1">
          {loading ? <Loader2 size={14} className="animate-spin" /> : `Import ${selected.length} doc${selected.length !== 1 ? 's' : ''}`}
        </button>
      </div>
    </div>
  )

  return (
    <div className="p-6 flex flex-col gap-4">
      <SharePointHeader />
      <Field label="SharePoint site URL" value={siteUrl} onChange={setSiteUrl}
        placeholder="https://yourorg.sharepoint.com/sites/YourSite" />
      {error && <ErrorBanner msg={error} />}
      <ConnectBtn loading={loading} disabled={!siteUrl.trim()} onClick={handleBrowse} label="Browse documents" />
    </div>
  )
}

export default AddSourcesOverlay