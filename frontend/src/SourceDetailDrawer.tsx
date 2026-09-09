import React, { useState, useEffect, useCallback, useRef } from 'react'
import {
  X, Table2, ChevronDown, ChevronRight,
  Database, Loader2, RefreshCw, Key,
  Hash, Type, Calendar, ToggleLeft,
  AlertCircle, Save, Pencil, Check, LayoutDashboard,
  FileText, ClipboardPaste, Share2, Plus, ArrowRight,
} from 'lucide-react'
import * as pbi from 'powerbi-client'
import axios from 'axios'
import { cn } from './lib/utils'
import { API_BASE } from './config'
import { useNotebook } from './NotebookContext'
import { getEmbedToken } from './powerbiApi'
import type { NotebookSource } from './NotebookContext'
import DocumentViewer from './DocumentViewer'

/* ── Power BI service initialization ── */
const _pbiService = new pbi.service.Service(
  pbi.factories.hpmFactory,
  pbi.factories.wpmpFactory,
  pbi.factories.routerFactory,
)

if (typeof document !== 'undefined') {
  const id = 'pbi-drawer-fix'
  if (!document.getElementById(id)) {
    const s = document.createElement('style')
    s.id = id
    s.textContent = `
      [data-pbi-host] {
        width: 100%;
        height: 100%;
        position: relative;
      }
      [data-pbi-host] iframe {
        position: absolute !important;
        top: 0 !important;
        left: 0 !important;
        width: 100% !important;
        height: 100% !important;
        border: none !important;
        display: block !important;
      }
    `
    document.head.appendChild(s)
  }
}

/* ── DB icon map ────────────────────────────────────────────────── */
const DB_ICON_MAP: Record<string, string> = {
  sqlite:     '/sqlite.svg',
  postgresql: '/postgre.svg',
  mysql:      '/mysql.png',
  databricks: '/databricks.png',
  snowflake:  '/snowflake.png',
  powerbi:    '/powerbi.png',
  tableau:    '/tableau.png',
  excel:      '/excel.svg',
}

/* ── Type icon ──────────────────────────────────────────────────── */
const TypeIcon: React.FC<{ dtype: string }> = ({ dtype }) => {
  const d = (dtype || '').toLowerCase()
  if (d.includes('int') || d.includes('float') || d.includes('num') || d.includes('decimal'))
    return <Hash size={11} className="text-blue-400 shrink-0" />
  if (d.includes('date') || d.includes('time'))
    return <Calendar size={11} className="text-purple-400 shrink-0" />
  if (d.includes('bool'))
    return <ToggleLeft size={11} className="text-green-400 shrink-0" />
  return <Type size={11} className="text-ink-faint shrink-0" />
}

/* ── Types ──────────────────────────────────────────────────────── */
interface ColSchema   { type: string; description?: string; is_primary_key?: boolean }
interface TableSchema { description?: string; columns: Record<string, ColSchema> }
interface SchemaData  {
  tables: Record<string, TableSchema>
  selected_tables: Record<string, string[]>
}
// local edits: { "alias.table": { description?, columns: { col: { description? } } } }
type Edits = Record<string, { description?: string; columns: Record<string, { description?: string }> }>

/* ════════════════════════════════════════════════════════════════ */

interface Props { source: NotebookSource; onClose: () => void }

const SourceDetailDrawer: React.FC<Props> = ({ source, onClose }) => {
  const { notebook } = useNotebook()
  const [schema, setSchema]           = useState<SchemaData | null>(null)
  const [loading, setLoading]         = useState(false)
  const [saving, setSaving]           = useState(false)
  const [saved, setSaved]             = useState(false)
  const [error, setError]             = useState<string | null>(null)
  const [expanded, setExpanded]       = useState<Set<string>>(new Set())
  const [edits, setEdits]             = useState<Edits>({})
  const [hasEdits, setHasEdits]       = useState(false)
  const [search, setSearch]           = useState('')

  // Relationships — notebook-wide (can join tables across different
  // connected databases), so this isn't scoped to just this source's alias.
  const [relTables, setRelTables]         = useState<Record<string, string[]>>({})
  const [relationships, setRelationships] = useState<string[]>([])
  const [relLoading, setRelLoading]       = useState(false)
  const [relSaving, setRelSaving]         = useState(false)
  const [relSaved, setRelSaved]           = useState(false)
  const [relError, setRelError]           = useState<string | null>(null)
  const [relDirty, setRelDirty]           = useState(false)
  const [relOpen, setRelOpen]             = useState(false)
  const [leftTable, setLeftTable]         = useState('')
  const [leftCol, setLeftCol]             = useState('')
  const [rightTable, setRightTable]       = useState('')
  const [rightCol, setRightCol]           = useState('')

  // Table filter — which of the already-loaded tables to actually query on.
  // Defaults to all of them (selected_tables as returned by schema-preview);
  // the user can narrow it down to a subset here.
  const [tableFilterSaving, setTableFilterSaving] = useState(false)
  const [tableFilterError, setTableFilterError]   = useState<string | null>(null)

  const isSchemaBased = source.type === 'database'
  const isDashboard = source.type === 'dashboard'
  const isPastedText = source.type === 'document' && (source.alias?.endsWith('.txt') || source.db_type === 'pasted_text')
  const isDocument = source.type === 'document' && !isPastedText

  const fetchSchema = useCallback(async () => {
    setLoading(true); setError(null); setEdits({}); setHasEdits(false)
    try {
      const params = new URLSearchParams({ alias: source.alias })
      if (notebook?.id) params.set('notebook_id', notebook.id)
      const r = await axios.get(`${API_BASE}/api/datasources/schema-preview?${params}`, { withCredentials: true })
      setSchema(r.data)
      // Auto-expand all tables
      setExpanded(new Set(Object.keys(r.data.tables ?? {})))
    } catch (e: any) { setError(e.response?.data?.detail || 'Failed to load schema') }
    finally { setLoading(false) }
  }, [source.alias, notebook?.id])

  const fetchRelationships = useCallback(async () => {
    setRelLoading(true); setRelError(null)
    try {
      const r = await axios.get(`${API_BASE}/api/datasources/relationships`, {
        params: { notebook_id: notebook?.id },
        withCredentials: true,
      })
      setRelTables(r.data?.tables ?? {})
      setRelationships(r.data?.relationships ?? [])
      setRelDirty(false)
    } catch (e: any) { setRelError(e.response?.data?.detail || 'Failed to load relationships') }
    finally { setRelLoading(false) }
  }, [notebook?.id])

  const embedRef = useRef<HTMLDivElement>(null)

  const destroyEmbed = useCallback(() => {
    try {
      if (embedRef.current) _pbiService.reset(embedRef.current)
    } catch { }
  }, [])

  const loadEmbed = useCallback(async () => {
    destroyEmbed()
    setLoading(true)
    setError(null)

    try {
      const { token, embedUrl } = await getEmbedToken(Number(source.id))
      if (!token || !embedUrl) throw new Error('No embed token received')

      // Wait for multiple animation frames to ensure DOM is ready
      await new Promise<void>(res => {
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            requestAnimationFrame(() => res())
          })
        })
      })

      const embed = embedRef.current
      if (!embed) throw new Error('Embed container not ready')

      // Ensure container has proper dimensions
      const rect = embed.getBoundingClientRect()
      if (rect.width === 0 || rect.height === 0) {
        throw new Error('Embed container has no dimensions')
      }

      _pbiService.embed(embed, {
        type: 'report',
        id: source.config?.report_id,
        embedUrl,
        accessToken: token,
        tokenType: pbi.models.TokenType.Embed,
        settings: {
          panes: {
            filters: { visible: false },
            pageNavigation: { visible: true },
          },
          background: pbi.models.BackgroundType.Transparent,
          layoutType: pbi.models.LayoutType.Master,
        },
      })

      setLoading(false)
    } catch (e: any) {
      console.error('Dashboard embed error:', e)
      setError(e?.response?.data?.detail || e?.message || 'Failed to load dashboard')
      setLoading(false)
    }
  }, [source.id, source.config?.report_id, destroyEmbed])

  useEffect(() => {
    if (isSchemaBased) {
      fetchSchema()
      fetchRelationships()
    } else if (isDashboard) {
      loadEmbed()
    }
    // isPastedText: no fetch needed, text is loaded on demand in PastedTextView
    return isDashboard ? destroyEmbed : undefined
  }, [isSchemaBased, isDashboard, fetchSchema, fetchRelationships, loadEmbed, destroyEmbed])

  /* ── Save edits ── */
  const handleSave = async () => {
    if (!hasEdits) return
    setSaving(true)
    try {
      const params: Record<string, string> = {}
      if (notebook?.id) params.notebook_id = notebook.id
      await axios.post(
        `${API_BASE}/api/datasources/update-schema`,
        { tables: edits },
        { params, withCredentials: true }
      )
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
      setHasEdits(false)
      // Re-fetch to reflect saved state
      await fetchSchema()
    } catch (e: any) { setError(e.response?.data?.detail || 'Save failed') }
    finally { setSaving(false) }
  }

  /* ── Edit helpers ── */
  const editTableDesc = (fullKey: string, value: string) => {
    setEdits(prev => ({
      ...prev,
      [fullKey]: { ...prev[fullKey], description: value, columns: prev[fullKey]?.columns ?? {} }
    }))
    setHasEdits(true)
  }

  const editColDesc = (fullKey: string, colName: string, value: string) => {
    setEdits(prev => ({
      ...prev,
      [fullKey]: {
        ...prev[fullKey],
        columns: { ...(prev[fullKey]?.columns ?? {}), [colName]: { description: value } }
      }
    }))
    setHasEdits(true)
  }

  /* ── Relationships ── */
  const parseRel = (raw: string): { left: string; right: string } | null => {
    const [l, r] = raw.split(' = ')
    return l && r ? { left: l.trim(), right: r.trim() } : null
  }

  const handleAddRelationship = () => {
    if (!leftTable || !leftCol || !rightTable || !rightCol) return
    const raw     = `${leftTable}.${leftCol} = ${rightTable}.${rightCol}`
    const reverse = `${rightTable}.${rightCol} = ${leftTable}.${leftCol}`
    if (relationships.includes(raw) || relationships.includes(reverse)) return
    setRelationships(prev => [...prev, raw])
    setRelDirty(true)
    setLeftTable(''); setLeftCol(''); setRightTable(''); setRightCol('')
  }

  const handleRemoveRelationship = (raw: string) => {
    setRelationships(prev => prev.filter(r => r !== raw))
    setRelDirty(true)
  }

  const handleSaveRelationships = async () => {
    setRelSaving(true); setRelError(null)
    try {
      await axios.post(
        `${API_BASE}/api/datasources/relationships`,
        { relationships },
        { params: { notebook_id: notebook?.id }, withCredentials: true },
      )
      setRelSaved(true); setRelDirty(false)
      setTimeout(() => setRelSaved(false), 2000)
    } catch (e: any) {
      setRelError(e.response?.data?.detail || 'Failed to save relationships')
    } finally {
      setRelSaving(false)
    }
  }

  /* ── Table filter (which tables are active) ── */
  const handleToggleTableActive = async (tableName: string, checked: boolean) => {
    const current = new Set(selectedTables)
    if (checked) current.add(tableName)
    else current.delete(tableName)
    if (current.size === 0) {
      setTableFilterError('At least one table must stay selected.')
      return
    }
    setTableFilterSaving(true); setTableFilterError(null)
    try {
      await axios.post(
        `${API_BASE}/api/datasources/update-table-filter`,
        { tables: Array.from(current).map(t => `${source.alias}.${t}`) },
        { params: { notebook_id: notebook?.id }, withCredentials: true },
      )
      await fetchSchema()   // picks up the new selected_tables + any newly generated schema
    } catch (e: any) {
      setTableFilterError(e.response?.data?.detail || 'Failed to update table selection')
    } finally {
      setTableFilterSaving(false)
    }
  }

  const toggleTable = (key: string) => {
    setExpanded(prev => {
      const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n
    })
  }

  /* ── Derived ── */
  const tables = Object.entries(schema?.tables ?? {})
    .filter(([k]) => k.startsWith(`${source.alias}.`))
    .filter(([k]) => !search || k.toLowerCase().includes(search.toLowerCase()))

  const selectedTables = schema?.selected_tables?.[source.alias] ?? []
  const totalCols = Object.values(schema?.tables ?? {}).reduce((a, t) => a + Object.keys(t.columns ?? {}).length, 0)

  const relTableNames = Object.keys(relTables).sort()
  const parsedRels = relationships
    .map(parseRel)
    .filter((r): r is { left: string; right: string } => r !== null)

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-6"
      style={{ background: 'rgba(15,23,42,0.5)', backdropFilter: 'blur(3px)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div style={(isDashboard || isDocument) ? { display: 'flex', flexDirection: 'column', height: '88vh', width: '100%', maxWidth: '1200px' } : undefined}
           className={cn(
        (isDashboard || isDocument) ? '' : 'flex flex-col bg-white rounded-2xl shadow-lg overflow-hidden animate-slide-up w-full max-w-4xl max-h-[88vh]',
        'bg-white rounded-2xl shadow-lg overflow-hidden animate-slide-up',
      )}>

        {/* ── Header ── */}
        <div className="flex items-center gap-4 px-6 py-4 bg-[#2D5A8C] shrink-0">
          <div className="w-9 h-9 rounded-xl bg-white/10 flex items-center justify-center shrink-0 p-1.5">
            {isDashboard ? (
              <LayoutDashboard size={18} className="text-white" />
            ) : isPastedText ? (
              <ClipboardPaste size={18} className="text-white" />
            ) : isDocument ? (
              <FileText size={18} className="text-white" />
            ) : DB_ICON_MAP[source.db_type?.toLowerCase() ?? ''] ? (
              <img src={DB_ICON_MAP[source.db_type!.toLowerCase()]} alt={source.db_type} className="w-full h-full object-contain" />
            ) : (
              <Database size={18} className="text-white" />
            )}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-base font-semibold text-white">{source.alias}</p>
            <p className="text-xs text-white/60 capitalize">
              {isDashboard   ? 'Power BI Dashboard'
               : isPastedText ? 'Pasted Text · editable'
               : isDocument   ? 'Document'
               : `${source.db_type} · ${tables.length} tables · ${totalCols} columns`}
            </p>
          </div>

          {/* Search — only for schema */}
          {isSchemaBased && (
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search tables…"
              className={cn(
                'px-3 py-1.5 rounded-lg text-sm bg-white/10 text-white placeholder-white/40',
                'border border-white/20 focus:outline-none focus:border-white/50',
                'w-48',
              )}
            />
          )}

          {/* Save — only for schema */}
          {isSchemaBased && (
            <button
              onClick={handleSave}
              disabled={!hasEdits || saving}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all',
                hasEdits
                  ? 'bg-white text-[#2D5A8C] hover:bg-white/90'
                  : 'bg-white/10 text-white/40 cursor-not-allowed',
              )}
            >
              {saving ? <Loader2 size={13} className="animate-spin" /> :
               saved  ? <Check size={13} /> : <Save size={13} />}
              {saved ? 'Saved!' : 'Save changes'}
            </button>
          )}

          <button onClick={onClose} className="p-1.5 rounded-lg text-white/60 hover:text-white hover:bg-white/10 transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* ── Stats bar — only for schema ── */}
        {!loading && isSchemaBased && schema && (
          <div className="flex items-center gap-6 px-6 py-2.5 border-b border-border bg-surface shrink-0">
            <StatPill label="Tables"  value={tables.length} />
            <StatPill label="Columns" value={totalCols} />
            <StatPill label="Active"  value={selectedTables.length} color="green" />
            {hasEdits && <span className="text-xs text-tertiary font-medium ml-auto">Unsaved changes</span>}
            <button onClick={fetchSchema} className={cn('p-1.5 rounded-lg text-ink-faint hover:text-[#2D5A8C] hover:bg-[#2D5A8C]-muted transition-colors', hasEdits ? '' : 'ml-auto')}>
              <RefreshCw size={12} />
            </button>
          </div>
        )}

        {/* ── Relationships — collapsible, sits at the top of the schema view ── */}
        {!loading && isSchemaBased && schema && (
          <div className="border-b border-border bg-surface shrink-0">
            <button
              onClick={() => setRelOpen(o => !o)}
              className="w-full flex items-center gap-2 px-6 py-2.5 hover:bg-[#2D5A8C]-muted/40 transition-colors"
            >
              {relOpen ? <ChevronDown size={13} className="text-[#2D5A8C]" /> : <ChevronRight size={13} className="text-ink-faint" />}
              <Share2 size={13} className="text-[#2D5A8C]" />
              <span className="text-xs font-semibold text-ink">Relationships</span>
              <span className="text-xs text-ink-faint">({parsedRels.length})</span>
              {relDirty && <span className="text-[10px] text-tertiary font-medium">Unsaved</span>}
              <span className="text-[10px] text-ink-faint ml-auto">
                Join paths across your connected tables — used by the assistant to link queries
              </span>
            </button>

            {relOpen && (
              <div className="px-6 pb-4 flex flex-col gap-3">
                {relLoading ? (
                  <div className="flex items-center gap-2 text-ink-faint py-2">
                    <Loader2 size={12} className="animate-spin" />
                    <span className="text-xs">Loading relationships…</span>
                  </div>
                ) : (
                  <>
                    {/* Existing relationships */}
                    {parsedRels.length === 0 ? (
                      <p className="text-xs text-ink-faint italic">
                        No relationships defined yet — tables will only join if the assistant can match column names on its own.
                      </p>
                    ) : (
                      <div className="flex flex-col gap-1.5">
                        {parsedRels.map(rel => (
                          <div key={`${rel.left}|${rel.right}`} className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-border bg-white group">
                            <span className="text-xs font-mono text-ink truncate flex-1">{rel.left}</span>
                            <ArrowRight size={11} className="text-ink-faint shrink-0" />
                            <span className="text-xs font-mono text-ink truncate flex-1">{rel.right}</span>
                            <button
                              onClick={() => handleRemoveRelationship(`${rel.left} = ${rel.right}`)}
                              className="p-1 rounded text-ink-faint opacity-0 group-hover:opacity-100 hover:text-tertiary hover:bg-tertiary-muted transition-all shrink-0"
                              title="Remove relationship"
                            >
                              <X size={11} />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Add new relationship */}
                    <div className="flex items-end gap-2 flex-wrap">
                      <div className="flex flex-col gap-1 min-w-[140px]">
                        <select value={leftTable} onChange={e => { setLeftTable(e.target.value); setLeftCol('') }} className="input text-xs py-1.5">
                          <option value="">Table…</option>
                          {relTableNames.map(t => <option key={t} value={t}>{t}</option>)}
                        </select>
                        <select value={leftCol} onChange={e => setLeftCol(e.target.value)} disabled={!leftTable} className="input text-xs py-1.5 disabled:opacity-50">
                          <option value="">Column…</option>
                          {(relTables[leftTable] ?? []).map(c => <option key={c} value={c}>{c}</option>)}
                        </select>
                      </div>
                      <ArrowRight size={13} className="text-ink-faint shrink-0 mb-2.5" />
                      <div className="flex flex-col gap-1 min-w-[140px]">
                        <select value={rightTable} onChange={e => { setRightTable(e.target.value); setRightCol('') }} className="input text-xs py-1.5">
                          <option value="">Table…</option>
                          {relTableNames.map(t => <option key={t} value={t}>{t}</option>)}
                        </select>
                        <select value={rightCol} onChange={e => setRightCol(e.target.value)} disabled={!rightTable} className="input text-xs py-1.5 disabled:opacity-50">
                          <option value="">Column…</option>
                          {(relTables[rightTable] ?? []).map(c => <option key={c} value={c}>{c}</option>)}
                        </select>
                      </div>
                      <button
                        onClick={handleAddRelationship}
                        disabled={!leftTable || !leftCol || !rightTable || !rightCol}
                        className="flex items-center gap-1.5 btn-sm btn-outline disabled:opacity-40"
                      >
                        <Plus size={12} /> Add
                      </button>
                      <button
                        onClick={handleSaveRelationships}
                        disabled={!relDirty || relSaving}
                        className="flex items-center gap-1.5 btn-sm btn-primary ml-auto"
                      >
                        {relSaving ? <Loader2 size={12} className="animate-spin" /> : relSaved ? <Check size={12} /> : <Save size={12} />}
                        {relSaved ? 'Saved!' : 'Save relationships'}
                      </button>
                    </div>

                    {relError && (
                      <div className="flex items-start gap-2 p-2.5 rounded-lg bg-tertiary-muted border border-tertiary/20">
                        <AlertCircle size={12} className="text-tertiary shrink-0 mt-0.5" />
                        <p className="text-xs text-tertiary leading-snug">{relError}</p>
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        )}

        {/* ── Body ── */}
        <div style={(isDashboard || isDocument) ? { flex: 1, minHeight: 0, position: 'relative', overflow: 'hidden' } : undefined}
             className={(isDashboard || isDocument) ? '' : "flex-1 overflow-y-auto"}>

          {loading && (
            <div style={isDashboard ? { position: 'absolute', inset: 0, zIndex: 20, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, background: 'white' } : undefined}
                 className={isDashboard ? '' : "flex flex-col items-center justify-center h-64 gap-3 text-ink-faint"}>
              <Loader2 size={22} className="animate-spin text-[#2D5A8C]" />
              <span className="text-sm text-ink-faint">{isDashboard ? 'Loading dashboard…' : 'Loading schema…'}</span>
            </div>
          )}

          {error && !loading && (
            <div style={isDashboard ? { position: 'absolute', inset: 0, zIndex: 20, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, textAlign: 'center', padding: '0 24px' } : undefined}
                 className={isDashboard ? '' : "flex flex-col items-center justify-center h-64 gap-3 text-center px-6"}>
              <AlertCircle size={28} className="text-tertiary" />
              <p className={isDashboard ? "text-sm font-semibold text-ink" : "text-sm text-ink-muted"}>{isDashboard ? 'Failed to load dashboard' : error}</p>
              {isDashboard && <p className="text-xs text-ink-muted max-w-xs">{error}</p>}
              <button onClick={isDashboard ? loadEmbed : fetchSchema} className="btn-sm btn-outline"><RefreshCw size={12} /> Retry</button>
            </div>
          )}

          {/* ── Dashboard embed ── */}
          {isDashboard && (
            <div
              ref={embedRef}
              data-pbi-host="true"
              style={{
                width: '100%',
                height: '100%',
                backgroundColor: '#ffffff',
                visibility: error ? 'hidden' : 'visible',
              }}
            />
          )}

          {/* ── Pasted text editor ── */}
          {isPastedText && (
            <PastedTextEditor source={source} onClose={() => {}} />
          )}

          {/* ── Document preview ── */}
          {isDocument && (
            <DocumentViewer docId={source.id} embedded />
          )}

          {/* ── Schema tables ── */}
          {!loading && !error && isSchemaBased && tables.length === 0 && (
            <div className="flex flex-col items-center justify-center h-64 gap-2 text-center px-6">
              <Table2 size={26} className="text-ink-faint" />
              <p className="text-sm text-ink-muted">{search ? `No tables match "${search}"` : 'No schema available yet'}</p>
              {!search && <p className="text-xs text-ink-faint">Schema is generated after tables are selected and connected.</p>}
            </div>
          )}

          {tableFilterError && (
            <div className="flex items-start gap-2.5 mx-6 mt-4 p-3 rounded-xl bg-tertiary-muted border border-tertiary/20">
              <AlertCircle size={14} className="text-tertiary shrink-0 mt-0.5" />
              <p className="text-sm text-tertiary leading-snug">{tableFilterError}</p>
            </div>
          )}

          {!loading && !error && isSchemaBased && tables.length > 0 && (
            <div className="divide-y divide-border">
              {tables.map(([fullKey, tableSchema]) => {
                const tableName   = fullKey.replace(`${source.alias}.`, '')
                const isExpanded  = expanded.has(fullKey)
                const isActive    = selectedTables.includes(tableName)
                const columns     = Object.entries(tableSchema.columns ?? {})
                const editedTable = edits[fullKey]
                const tableDesc   = editedTable?.description ?? tableSchema.description ?? ''

                return (
                  <div key={fullKey} className="group/table">

                    {/* ── Table header ── */}
                    <div
                      className={cn(
                        'flex items-center gap-3 px-6 py-3 cursor-pointer select-none',
                        'hover:bg-[#2D5A8C]-muted transition-colors duration-150',
                        isExpanded && 'bg-[#2D5A8C]-muted/60',
                      )}
                      onClick={() => toggleTable(fullKey)}
                    >
                      {/* Query filter checkbox — unticking excludes this loaded
                          table from what the assistant can query, without
                          removing it from the notebook. Defaults to ticked. */}
                      <input
                        type="checkbox"
                        checked={isActive}
                        disabled={tableFilterSaving}
                        onClick={e => e.stopPropagation()}
                        onChange={e => handleToggleTableActive(tableName, e.target.checked)}
                        className="accent-secondary w-3.5 h-3.5 rounded shrink-0 disabled:opacity-50"
                        title={isActive ? 'Untick to exclude from queries' : 'Tick to include in queries'}
                      />

                      {isExpanded
                        ? <ChevronDown size={15} className="text-[#2D5A8C] shrink-0" />
                        : <ChevronRight size={15} className="text-ink-faint shrink-0" />
                      }
                      <Table2 size={15} className="text-[#2D5A8C] shrink-0" />
                      <span className="text-sm font-semibold text-ink font-mono">{tableName}</span>

                      {/* Editable table description */}
                      <div className="flex-1 min-w-0 ml-2" onClick={e => e.stopPropagation()}>
                        <EditableText
                          value={tableDesc}
                          placeholder="Add table description…"
                          onChange={v => editTableDesc(fullKey, v)}
                          className="text-xs text-ink-muted w-full"
                        />
                      </div>

                      <div className="flex items-center gap-3 shrink-0 ml-2">
                        <span className="text-xs text-ink-faint">{columns.length} col{columns.length !== 1 ? 's' : ''}</span>
                        <span className={cn(
                          'px-2 py-0.5 rounded-full text-[10px] font-semibold',
                          isActive ? 'bg-green-100 text-green-700' : 'bg-surface text-ink-faint border border-border',
                        )}>
                          {isActive ? '● active' : '○ inactive'}
                        </span>
                      </div>
                    </div>

                    {/* ── Columns table ── */}
                    {isExpanded && (
                      <div className="border-t border-border bg-white">
                        {columns.length === 0 ? (
                          <p className="px-12 py-4 text-xs text-ink-faint italic">No columns found in schema</p>
                        ) : (
                          <table className="w-full text-xs">
                            <thead>
                              <tr className="bg-surface border-b border-border">
                                <th className="pl-12 pr-3 py-2.5 text-left text-[10px] font-semibold text-ink-faint uppercase tracking-wider w-52">Column</th>
                                <th className="px-3 py-2.5 text-left text-[10px] font-semibold text-ink-faint uppercase tracking-wider w-36">Type</th>
                                <th className="px-3 py-2.5 text-left text-[10px] font-semibold text-ink-faint uppercase tracking-wider">
                                  Description
                                  <span className="ml-1 text-ink-faint font-normal normal-case">(click to edit)</span>
                                </th>
                              </tr>
                            </thead>
                            <tbody>
                              {columns.map(([colName, col], ci) => {
                                const editedDesc = edits[fullKey]?.columns?.[colName]?.description
                                const desc = editedDesc ?? col.description ?? ''
                                return (
                                  <tr key={colName} className={cn(
                                    'border-b border-border/60 hover:bg-[#2D5A8C]-muted/30 transition-colors',
                                    ci % 2 === 0 ? 'bg-white' : 'bg-surface/40',
                                  )}>
                                    {/* Column name */}
                                    <td className="pl-12 pr-3 py-2.5">
                                      <div className="flex items-center gap-1.5">
                                        {col.is_primary_key && (
                                          <span title="Primary key">
                                            <Key size={11} className="text-yellow-500 shrink-0" />
                                          </span>
                                        )}
                                        <span className={cn(
                                          'font-mono font-medium',
                                          col.is_primary_key ? 'text-[#2D5A8C]' : 'text-ink',
                                        )}>
                                          {colName}
                                        </span>
                                      </div>
                                    </td>
                                    {/* Type */}
                                    <td className="px-3 py-2.5">
                                      <div className="flex items-center gap-1.5">
                                        <TypeIcon dtype={col.type} />
                                        <span className="font-mono text-[11px] text-ink-faint">{col.type}</span>
                                      </div>
                                    </td>
                                    {/* Description — editable */}
                                    <td className="px-3 py-2.5 w-full">
                                      <EditableText
                                        value={desc}
                                        placeholder="Add description…"
                                        onChange={v => editColDesc(fullKey, colName, v)}
                                        className="text-xs text-ink-muted w-full"
                                        highlight={!!editedDesc}
                                      />
                                    </td>
                                  </tr>
                                )
                              })}
                            </tbody>
                          </table>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* ── Footer ── */}
        <div className="flex items-center justify-between px-6 py-3 border-t border-border bg-surface shrink-0">
          {isSchemaBased ? (
            <>
              <p className="text-xs text-ink-faint">
                {hasEdits ? 'You have unsaved changes — click Save to apply.' : 'Click any description to edit it inline.'}
              </p>
              <div className="flex gap-2">
                <button onClick={onClose} className="btn-sm btn-outline">Close</button>
                <button
                  onClick={handleSave}
                  disabled={!hasEdits || saving}
                  className="btn-sm btn-primary"
                >
                  {saving ? <Loader2 size={13} className="animate-spin" /> : saved ? <Check size={13} /> : <Save size={13} />}
                  {saved ? 'Saved!' : 'Save changes'}
                </button>
              </div>
            </>
          ) : isPastedText ? (
            <>
              <p className="text-xs text-ink-faint">Edit the text above and click Save to update.</p>
              <button onClick={onClose} className="btn-sm btn-outline">Close</button>
            </>
          ) : isDocument ? (
            <>
              <p className="text-xs text-ink-faint">Document preview</p>
              <button onClick={onClose} className="btn-sm btn-outline">Close</button>
            </>
          ) : (
            <>
              <p className="text-xs text-ink-faint">Power BI Dashboard</p>
              <button onClick={onClose} className="btn-sm btn-outline">Close</button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

/* ── Inline editable text ────────────────────────────────────────── */
interface EditableTextProps {
  value: string
  placeholder: string
  onChange: (v: string) => void
  className?: string
  highlight?: boolean
}

const EditableText: React.FC<EditableTextProps> = ({ value, placeholder, onChange, className, highlight }) => {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft]     = useState(value)
  const inputRef              = useRef<HTMLInputElement>(null)

  useEffect(() => { setDraft(value) }, [value])
  useEffect(() => { if (editing) inputRef.current?.focus() }, [editing])

  const commit = () => {
    setEditing(false)
    if (draft !== value) onChange(draft)
  }

  if (editing) return (
    <input
      ref={inputRef}
      value={draft}
      onChange={e => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { setDraft(value); setEditing(false) } }}
      className={cn('bg-white border border-[#2D5A8C] rounded px-2 py-0.5 focus:outline-none focus:ring-1 focus:ring-secondary/40 text-ink', className)}
      onClick={e => e.stopPropagation()}
    />
  )

  return (
    <span
      onClick={e => { e.stopPropagation(); setEditing(true) }}
      className={cn(
        'group/edit flex items-center gap-1.5 cursor-text rounded px-1.5 py-0.5',
        'hover:bg-[#2D5A8C]-muted transition-colors',
        highlight && 'text-[#2D5A8C] font-medium',
        className,
      )}
      title="Click to edit"
    >
      <span className={cn('flex-1 truncate', !value && 'text-ink-faint italic')}>
        {value || placeholder}
      </span>
      <Pencil size={10} className="text-ink-faint opacity-0 group-hover/edit:opacity-100 shrink-0 transition-opacity" />
    </span>
  )
}

/* ── Stat pill ───────────────────────────────────────────────────── */
const StatPill: React.FC<{ label: string; value: number; color?: string }> = ({ label, value, color }) => (
  <div className="flex items-center gap-1.5">
    <span className={cn('text-sm font-bold', color === 'green' ? 'text-green-600' : 'text-[#2D5A8C]')}>{value}</span>
    <span className="text-xs text-ink-faint">{label}</span>
  </div>
)

/* ── Pasted text editor ──────────────────────────────────────────── */
const PastedTextEditor: React.FC<{ source: NotebookSource; onClose: () => void }> = ({ source }) => {
  const { notebook } = useNotebook()
  const [text, setText]           = useState('')
  const [description, setDesc]    = useState('')
  const [loading, setLoading]     = useState(true)
  const [saving, setSaving]       = useState(false)
  const [saved, setSaved]         = useState(false)
  const [error, setError]         = useState<string | null>(null)

  // Load existing text on mount
  useEffect(() => {
    const fetchText = async () => {
      try {
        const r = await axios.get(`${API_BASE}/api/documents/${source.id}/text`, { withCredentials: true })
        setText(r.data.text || '')
      } catch {
        setError('Could not load text content.')
      } finally {
        setLoading(false)
      }
    }
    fetchText()
  }, [source.id])

  const handleSave = async () => {
    if (!text.trim()) { setError('Text cannot be empty'); return }
    setSaving(true); setError(null)
    try {
      await axios.put(`${API_BASE}/api/documents/${source.id}/update-text`, {
        text,
        description,
        notebook_id: notebook?.id,
      }, { withCredentials: true })
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (e: any) { setError(e.response?.data?.detail || 'Save failed') }
    finally { setSaving(false) }
  }

  if (loading) return (
    <div className="flex items-center justify-center h-48 gap-2 text-ink-faint">
      <Loader2 size={16} className="animate-spin text-[#2D5A8C]" />
      <span className="text-sm">Loading text…</span>
    </div>
  )

  return (
    <div className="p-6 flex flex-col gap-4">
      <div>
        <label className="text-xs font-medium text-ink-muted mb-1.5 block">
          Text content <span className="text-ink-faint font-normal">(edit and save to update the RAG index)</span>
        </label>
        <textarea
          value={text}
          onChange={e => { setText(e.target.value); setSaved(false) }}
          rows={14}
          className="input text-sm resize-none w-full"
          style={{ minHeight: 280 }}
          placeholder="Text content…"
        />
        <p className="text-[10px] text-ink-faint mt-1">{text.length.toLocaleString()} characters</p>
      </div>

      <div>
        <label className="text-xs font-medium text-ink-muted mb-1.5 block">Description</label>
        <input
          type="text"
          value={description}
          onChange={e => setDesc(e.target.value)}
          placeholder="Optional note about this text…"
          className="input text-sm"
        />
      </div>

      {error && (
        <div className="flex items-start gap-2 p-3 bg-tertiary-muted rounded-lg border border-tertiary/20">
          <AlertCircle size={14} className="text-tertiary shrink-0 mt-0.5" />
          <p className="text-xs text-tertiary leading-snug">{error}</p>
        </div>
      )}

      <button onClick={handleSave} disabled={saving || !text.trim()} className="btn-md btn-primary w-full">
        {saving  ? <><Loader2 size={14} className="animate-spin" /> Saving…</>
         : saved ? <><Check size={14} /> Saved!</>
         : <><Save size={14} /> Save changes</>}
      </button>
    </div>
  )
}

export default SourceDetailDrawer