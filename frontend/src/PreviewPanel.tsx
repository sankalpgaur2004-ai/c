import React, { useState, useEffect, useCallback, useRef } from 'react'
import {
  Table2, LayoutDashboard, FileText,
  Download, RefreshCw, Loader2, ChevronDown,
  Database, AlertCircle,
} from 'lucide-react'
import axios from 'axios'
import * as pbi from 'powerbi-client'
import { cn } from './lib/utils'
import { API_BASE } from './config'
import { useNotebook } from './NotebookContext'
import { getEmbedToken } from './powerbiApi'
import type { ActiveFilter, VisualData, VisualInfo } from './powerbiApi'
import DocumentViewer from './DocumentViewer'

/* ── Tab config ─────────────────────────────────────────────────── */
const TABS = [
  { key: 'table'     as const, label: 'Data',       icon: <Table2 size={13} /> },
  { key: 'document'  as const, label: 'Documents',  icon: <FileText size={13} /> },
  { key: 'dashboard' as const, label: 'Dashboard',  icon: <LayoutDashboard size={13} /> },
]

/* ════════════════════════════════════════════════════════════════ */

const PreviewPanel: React.FC = () => {
  const { previewTab, setPreviewTab, sources } = useNotebook()

  const dbSources        = sources.filter(s => s.type === 'database')
  const documentSources  = sources.filter(s => s.type === 'document')
  const dashboardSources = sources.filter(s => s.type === 'dashboard')

  return (
    <div className="flex flex-col h-full overflow-hidden bg-white">
      <div className="flex items-center border-b border-border shrink-0 px-2 pt-1">
        {TABS.map(tab => (
          <button
            key={tab.key}
            onClick={() => setPreviewTab(tab.key)}
            className={cn(
              'flex items-center gap-1.5 px-3 py-2 text-xs font-medium',
              'border-b-2 transition-all duration-150 -mb-px',
              previewTab === tab.key
                ? 'border-[#2D5A8C] text-[#2D5A8C]'
                : 'border-transparent text-ink-faint hover:text-ink hover:border-border',
            )}
          >
            {tab.icon}
            {tab.label}
            {tab.key === 'table'     && dbSources.length > 0       && <CountBadge n={dbSources.length} />}
            {tab.key === 'document'  && documentSources.length > 0  && <CountBadge n={documentSources.length} />}
            {tab.key === 'dashboard' && dashboardSources.length > 0 && <CountBadge n={dashboardSources.length} />}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-hidden">
        {previewTab === 'table'     && <DataSourcePreview sources={dbSources} />}
        {previewTab === 'document'  && <DocumentPreview sources={documentSources} />}
        {previewTab === 'dashboard' && <DashboardPreview sources={dashboardSources} />}
      </div>
    </div>
  )
}

/* ══════════════════════════════════════════════════════════════════
   DATA SOURCE PREVIEW
══════════════════════════════════════════════════════════════════ */

const DataSourcePreview: React.FC<{ sources: any[] }> = ({ sources }) => {
  const { getDisplayAlias } = useNotebook()
  const [selectedSource, setSelectedSource] = useState(0)
  const [selectedTable, setSelectedTable]   = useState<string | null>(null)
  const [tables, setTables]                 = useState<string[]>([])
  const [tableData, setTableData]           = useState<{ columns: string[]; rows: any[] } | null>(null)
  const [loading, setLoading]               = useState(false)
  const [error, setError]                   = useState<string | null>(null)

  const currentSource = sources[selectedSource]

  useEffect(() => {
    if (!currentSource) return
    setSelectedTable(null); setTableData(null); setError(null); setLoading(true)
    const p = new URLSearchParams()
    if (currentSource?.notebook_id) p.set('notebook_id', currentSource.notebook_id)
    axios.get(`${API_BASE}/api/datasources/schema-preview?${p}`, { withCredentials: true })
      .then(r => {
        const allTables: Record<string, any> = r.data.tables ?? {}
        const aliasTables = Object.keys(allTables)
          .filter(t => t.startsWith(`${currentSource.alias}.`))
          .map(t => t.replace(`${currentSource.alias}.`, ''))
        setTables(aliasTables)
        if (aliasTables.length > 0) setSelectedTable(aliasTables[0])
      })
      .catch(() => setError('Failed to load schema'))
      .finally(() => setLoading(false))
  }, [selectedSource, currentSource?.alias])

  useEffect(() => {
    if (!selectedTable || !currentSource) return
    setTableData(null); setLoading(true); setError(null)
    const params = new URLSearchParams({ alias: currentSource.alias, table: selectedTable, limit: '50' })
    if (currentSource.notebook_id) params.set('notebook_id', currentSource.notebook_id)
    axios.get(`${API_BASE}/api/datasources/table-preview?${params}`, { withCredentials: true })
      .then(r => {
        if (r.data.success && r.data.rows?.length) {
          setTableData({ columns: r.data.columns ?? [], rows: r.data.rows })
        } else {
          setError('No data in this table')
        }
      })
      .catch(e => setError(e.response?.data?.detail || 'Failed to load preview'))
      .finally(() => setLoading(false))
  }, [selectedTable, currentSource?.alias])

  if (sources.length === 0) return (
    <EmptyPreview
      icon={<Database size={28} className="text-ink-faint" />}
      title="No databases connected"
      description="Connect a database from the Sources panel to preview its tables"
    />
  )

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-2 border-b border-border shrink-0 flex flex-col gap-1.5 bg-surface">
        {sources.length > 1 && (
          <div className="flex gap-1 overflow-x-auto scrollbar-none">
            {sources.map((s, i) => (
              <button key={s.alias} onClick={() => setSelectedSource(i)}
                className={cn(
                  'px-2.5 py-1 rounded-full text-xs font-medium whitespace-nowrap transition-colors shrink-0',
                  i === selectedSource ? 'bg-[#2D5A8C] text-white' : 'bg-white text-ink-muted border border-border hover:border-[#2D5A8C]',
                )}>
                <Database size={10} className="inline mr-1" />{getDisplayAlias(s)}
              </button>
            ))}
          </div>
        )}
        {tables.length > 0 && (
          <div className="relative">
            <select value={selectedTable ?? ''} onChange={e => setSelectedTable(e.target.value)}
              className="input text-xs py-1.5 pr-7 appearance-none">
              {tables.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
            <ChevronDown size={12} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-ink-faint pointer-events-none" />
          </div>
        )}
      </div>
      <div className="flex-1 overflow-hidden relative">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-white/80 z-10">
            <div className="flex flex-col items-center gap-2 text-ink-faint">
              <Loader2 size={20} className="animate-spin text-[#2D5A8C]" />
              <span className="text-xs">Loading preview…</span>
            </div>
          </div>
        )}
        {error && !loading && (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-6">
            <AlertCircle size={24} className="text-tertiary" />
            <p className="text-xs text-ink-muted">{error}</p>
            <button onClick={() => setSelectedTable(t => t)} className="btn-sm btn-outline">
              <RefreshCw size={12} /> Retry
            </button>
          </div>
        )}
        {tableData && !loading && !error && (
          <div className="flex flex-col h-full">
            <div className="flex items-center justify-between px-3 py-1.5 border-b border-border bg-surface shrink-0">
              <span className="text-[11px] text-ink-faint">
                Showing {Math.min(tableData.rows.length, 50)} rows · {tableData.columns.length} columns
              </span>
              <button
                onClick={() => {
                  const header = tableData.columns.join(',')
                  const rows = tableData.rows.map(r => tableData.columns.map(c => r[c] ?? '').join(','))
                  const blob = new Blob([[header, ...rows].join('\n')], { type: 'text/csv' })
                  const a = document.createElement('a'); a.href = URL.createObjectURL(blob)
                  a.download = `${selectedTable}.csv`; a.click()
                }}
                className="text-[11px] text-[#2D5A8C] hover:text-[#2D5A8C]-hover flex items-center gap-1 transition-colors"
              >
                <Download size={11} /> CSV
              </button>
            </div>
            <div className="flex-1 overflow-auto scrollbar-none">
              <table className="w-full text-xs border-collapse">
                <thead className="sticky top-0 z-10">
                  <tr>
                    {tableData.columns.map(col => (
                      <th key={col} className="px-3 py-2 text-left font-semibold whitespace-nowrap bg-[#2D5A8C] text-white border-r border-[#2D5A8C]-light last:border-r-0">
                        {col}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {tableData.rows.slice(0, 50).map((row, i) => (
                    <tr key={i} className={cn(
                      'border-b border-border transition-colors duration-100',
                      i % 2 === 0 ? 'bg-white' : 'bg-surface', 'hover:bg-[#2D5A8C]-muted',
                    )}>
                      {tableData.columns.map(col => (
                        <td key={col}
                          className="px-3 py-1.5 text-ink whitespace-nowrap max-w-[160px] truncate border-r border-border last:border-r-0"
                          title={String(row[col] ?? '')}>
                          {row[col] == null
                            ? <span className="text-ink-faint italic">null</span>
                            : String(row[col])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        {!loading && !error && !tableData && tables.length === 0 && (
          <EmptyPreview
            icon={<Table2 size={24} className="text-ink-faint" />}
            title="No tables found"
            description="No tables were found for this connection"
          />
        )}
      </div>
    </div>
  )
}

/* ══════════════════════════════════════════════════════════════════
   DOCUMENT PREVIEW
══════════════════════════════════════════════════════════════════ */

const DocumentPreview: React.FC<{ sources: any[] }> = ({ sources }) => {
  const { refreshSources, getDisplayAlias } = useNotebook()
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState(0)

  if (sources.length === 0) return (
    <EmptyPreview
      icon={<FileText size={28} className="text-ink-faint" />}
      title="No documents"
      description="Upload documents from the Sources panel to view them here"
    />
  )

  // Same pattern as Dashboard/Database previews: clamp against the current
  // list length (a document may have just been deleted) rather than reset
  // to 0, so deleting one document keeps you roughly where you were.
  const safeSelected = Math.min(selected, Math.max(0, sources.length - 1))
  const currentDoc   = sources[safeSelected]

  const handleDelete = async (docId: string) => {
    if (!confirm('Delete this document?')) return
    try {
      await axios.delete(`${API_BASE}/api/documents/${docId}`, { withCredentials: true })
      await refreshSources()
    } catch { setError('Failed to delete document') }
  }

  return (
    <div className="flex flex-col h-full">
      {sources.length > 1 && (
        <div className="flex gap-1 px-3 py-2 border-b border-border overflow-x-auto scrollbar-none shrink-0 bg-surface">
          {sources.map((s, i) => (
            <button key={s.id ?? s.alias} onClick={() => setSelected(i)}
              className={cn(
                'px-3 py-1 rounded-full text-xs font-medium whitespace-nowrap transition-colors',
                i === safeSelected ? 'bg-[#2D5A8C] text-white' : 'bg-white text-ink-muted border border-border hover:border-[#2D5A8C]',
              )}>
              <FileText size={10} className="inline mr-1 -mt-0.5" />{getDisplayAlias(s)}
            </button>
          ))}
        </div>
      )}

      <div className="flex-1 min-h-0 relative">
        {error && (
          <div className="absolute top-2 left-1/2 -translate-x-1/2 z-20 flex items-center gap-2 px-3 py-1.5 bg-tertiary-muted rounded-lg border border-tertiary/20 shadow-sm">
            <AlertCircle size={13} className="text-tertiary shrink-0" />
            <p className="text-xs text-tertiary">{error}</p>
          </div>
        )}
        <DocumentViewer key={currentDoc.id} docId={currentDoc.id} onDelete={() => handleDelete(currentDoc.id)} />
      </div>
    </div>
  )
}

/* ══════════════════════════════════════════════════════════════════
   DASHBOARD PREVIEW — Power BI SDK embed
   Now captures: active page, slicer filters, visual export data
══════════════════════════════════════════════════════════════════ */

const _pbiService = new pbi.service.Service(
  pbi.factories.hpmFactory,
  pbi.factories.wpmpFactory,
  pbi.factories.routerFactory,
)

if (typeof document !== 'undefined') {
  const id = 'pbi-fill-fix'
  if (!document.getElementById(id)) {
    const s = document.createElement('style')
    s.id = id
    s.textContent = `
      [data-pbi-host] iframe {
        position: absolute !important;
        top: 0 !important; left: 0 !important;
        width: 100% !important; height: 100% !important;
        border: none !important;
      }
    `
    document.head.appendChild(s)
  }
}

// ── Helper: extract active filters from page ──────────────────────────────────
async function _capturePageFilters(page: pbi.Page): Promise<ActiveFilter[]> {
  try {
    const filters = await page.getFilters()
    const result: ActiveFilter[] = []
    for (const f of filters) {
      if (f.filterType === pbi.models.FilterType.Basic) {
        const bf     = f as pbi.models.IBasicFilter
        // Cast target to any — IFilterGeneralTarget union doesn't expose
        // .table / .column directly in all SDK versions
        const target = bf.target as any
        if (bf.values && bf.values.length > 0 && target?.table) {
          result.push({
            table:  target.table  ?? '',
            column: target.column ?? '',
            values: bf.values,
          })
        }
      }
    }
    return result
  } catch {
    return []
  }
}

// ── Helper: export data from all visuals ──────────────────────────────────────
async function _captureVisualData(page: pbi.Page): Promise<VisualData[]> {
  try {
    const visuals = await page.getVisuals()
    const results: VisualData[] = []

    await Promise.allSettled(
      visuals.map(async (visual) => {
        try {
          // Skip non-data visuals
          if (['image', 'textbox', 'shape', 'basicShape'].includes(visual.type)) return

          const exported = await visual.exportData(pbi.models.ExportDataType.Summarized)
          if (!exported?.data) return

          // Truncate to 25 rows
          const lines = exported.data.trim().split('\n')
          const truncated = lines.slice(0, 26)
          if (lines.length > 26) truncated.push('...(truncated)')

          results.push({
            title: visual.title || undefined,
            type:  visual.type,
            data:  truncated.join('\n'),
          })
        } catch {
          // exportData not supported for this visual — skip silently
        }
      })
    )
    return results
  } catch {
    return []
  }
}

// ── Helper: full, unfiltered visual inventory (title + type, no exportData) ──
// _captureVisualData above deliberately skips non-data visuals and anything
// exportData() fails on, so it under-reports the true visual count. This
// gives an accurate answer to meta questions like "how many visuals are on
// this page" or "what are the titles of the visuals" — it's just a metadata
// read, so it's fast and never throws away a visual.
async function _captureVisualInventory(page: pbi.Page): Promise<VisualInfo[]> {
  try {
    const visuals = await page.getVisuals()
    return visuals.map(v => ({
      title: v.title || '(untitled)',
      type:  v.type,
    }))
  } catch {
    return []
  }
}

// ── Helper: capture slicer states ────────────────────────────────────────────
async function _captureSlicerFilters(page: pbi.Page): Promise<ActiveFilter[]> {
  try {
    const visuals = await page.getVisuals()
    const slicers = visuals.filter(v => v.type === 'slicer')
    const filters: ActiveFilter[] = []

    await Promise.allSettled(
      slicers.map(async (slicer) => {
        try {
          const state = await slicer.getSlicerState()
          const targets = state.targets || []
          for (const target of targets) {
            const f = state.filters?.[0]
            if (!f) continue
            const bf = f as pbi.models.IBasicFilter
            if (bf.values && bf.values.length > 0) {
              filters.push({
                table:  (target as any).table ?? '',
                column: (target as any).column ?? '',
                values: bf.values,
              })
            }
          }
        } catch { /* slicer state not available */ }
      })
    )
    return filters
  } catch {
    return []
  }
}

const DashboardPreview: React.FC<{ sources: any[] }> = ({ sources }) => {
  const [selected, setSelected] = useState(0)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState<string | null>(null)
  const [capturingContext, setCapturingContext] = useState(false)

  // ── Schema build state ──
  const [schemaBuilding, setSchemaBuilding]   = useState(false)
  const [schemaStep, setSchemaStep]           = useState<string>('Building AI schema…')
  const [schemaPercent, setSchemaPercent]     = useState(0)
  const schemaPollerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const embedRef   = useRef<HTMLDivElement>(null)
  const reportRef  = useRef<pbi.Report | null>(null)

  const {
    setActiveDashboardPage,
    setDashboardActiveFilters,
    setDashboardPageVisualData,
    setDashboardVisualInventory,
    setDashboardContextReady,
    getDisplayAlias,
  } = useNotebook()

  const safeSelected  = Math.min(selected, Math.max(0, sources.length - 1))
  const currentSource = sources[safeSelected]

  // ── Capture full page context (page + filters + visual data) ──
  const capturePageContext = useCallback(async (report: pbi.Report) => {
    try {
      setCapturingContext(true)
      setDashboardContextReady(false)
      const pages      = await report.getPages()
      const activePage = pages.find(p => p.isActive)
      if (!activePage) return

      // Set active page in context
      setActiveDashboardPage({
        name:        activePage.name,
        displayName: activePage.displayName,
      })

      // Capture filters + slicer states in parallel
      const [pageFilters, slicerFilters, visualData, visualInventory] = await Promise.all([
        _capturePageFilters(activePage),
        _captureSlicerFilters(activePage),
        _captureVisualData(activePage),
        _captureVisualInventory(activePage),
      ])

      // Merge filters (deduplicate by table+column)
      const allFilters = [...pageFilters, ...slicerFilters]
      const seen = new Set<string>()
      const dedupedFilters = allFilters.filter(f => {
        const key = `${f.table}.${f.column}`
        if (seen.has(key)) return false
        seen.add(key)
        return true
      })

      setDashboardActiveFilters(dedupedFilters)
      setDashboardPageVisualData(visualData)
      setDashboardVisualInventory(visualInventory)
      setDashboardContextReady(true)

      console.log(`[Dashboard] Page: "${activePage.displayName}", Filters: ${dedupedFilters.length}, Visuals: ${visualData.length} (inventory: ${visualInventory.length})`)
    } catch (e) {
      console.warn('[Dashboard] capturePageContext failed:', e)
    } finally {
      setCapturingContext(false)
    }
  }, [setActiveDashboardPage, setDashboardActiveFilters, setDashboardPageVisualData, setDashboardVisualInventory, setDashboardContextReady])

  const destroyEmbed = useCallback(() => {
    try {
      if (embedRef.current) _pbiService.reset(embedRef.current)
      reportRef.current = null
    } catch { }
    // Clear context on destroy
    setActiveDashboardPage(null)
    setDashboardActiveFilters([])
    setDashboardPageVisualData([])
    setDashboardVisualInventory([])
    setDashboardContextReady(false)
    // Stop any schema poller
    if (schemaPollerRef.current) { clearInterval(schemaPollerRef.current); schemaPollerRef.current = null }
    setSchemaBuilding(false)
  }, [setActiveDashboardPage, setDashboardActiveFilters, setDashboardPageVisualData, setDashboardVisualInventory, setDashboardContextReady])

  // ── Poll schema progress until done ──────────────────────────────────
  const startSchemaPoller = useCallback((sourceId: number) => {
    if (schemaPollerRef.current) clearInterval(schemaPollerRef.current)
    setSchemaBuilding(true)
    setSchemaStep('Building AI schema…')
    setSchemaPercent(0)

    schemaPollerRef.current = setInterval(async () => {
      try {
        // First try the cheap exists check
        const existsRes = await axios.get(`${API_BASE}/api/powerbi/${sourceId}/schema/exists`, { withCredentials: true })
        if (existsRes.data.exists) {
          setSchemaBuilding(false)
          setSchemaStep('Schema ready')
          setSchemaPercent(100)
          if (schemaPollerRef.current) { clearInterval(schemaPollerRef.current); schemaPollerRef.current = null }
          return
        }
        // Otherwise fetch progress
        const progRes = await axios.get(`${API_BASE}/api/powerbi/${sourceId}/schema/progress`, { withCredentials: true })
        const { status, step, percent } = progRes.data
        setSchemaStep(step || 'Building AI schema…')
        setSchemaPercent(percent || 0)
        if (status === 'done') {
          setSchemaBuilding(false)
          if (schemaPollerRef.current) { clearInterval(schemaPollerRef.current); schemaPollerRef.current = null }
        }
      } catch { /* ignore poll errors */ }
    }, 2500)
  }, [])

  const loadEmbed = useCallback(async (source: any) => {
    if (!source?.id) return
    destroyEmbed()
    setLoading(true)
    setError(null)

    // ── Check if schema exists; if not, start poller ──
    try {
      const existsRes = await axios.get(`${API_BASE}/api/powerbi/${source.id}/schema/exists`, { withCredentials: true })
      if (!existsRes.data.exists) {
        startSchemaPoller(source.id)
      }
    } catch { /* if check fails, don't block the embed */ }

    try {
      const { token, embedUrl } = await getEmbedToken(Number(source.id))
      if (!token || !embedUrl) throw new Error('No embed token received')

      await new Promise<void>(res => requestAnimationFrame(() => requestAnimationFrame(() => res())))

      const embed = embedRef.current
      if (!embed) throw new Error('Embed container not ready')

      const report = _pbiService.embed(embed, {
        type:        'report',
        id:          source.config?.report_id,
        embedUrl,
        accessToken: token,
        tokenType:   pbi.models.TokenType.Embed,
        settings: {
          panes: {
            filters:        { visible: false },
            pageNavigation: { visible: true },
          },
          background: pbi.models.BackgroundType.Transparent,
          layoutType: pbi.models.LayoutType.Master,
        },
      }) as pbi.Report

      reportRef.current = report

      // Wait for report to fully load, then capture context
      report.on('loaded', () => {
        setTimeout(() => capturePageContext(report), 500)
      })

      // Re-capture when user changes page
      report.on('pageChanged', (event: any) => {
        const newPage = event?.detail?.newPage
        if (newPage) {
          setActiveDashboardPage({
            name:        newPage.name,
            displayName: newPage.displayName,
          })
          // Re-export visual data for new page after short delay
          setTimeout(() => capturePageContext(report), 800)
        }
      })

      // Re-capture when slicers change
      report.on('dataSelected', () => {
        setTimeout(() => capturePageContext(report), 600)
      })

      setLoading(false)
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || 'Failed to load dashboard')
      setLoading(false)
    }
  }, [destroyEmbed, capturePageContext, setActiveDashboardPage, startSchemaPoller])

  useEffect(() => {
    if (selected >= sources.length && sources.length > 0) setSelected(0)
  }, [sources.length])

  useEffect(() => {
    if (currentSource) loadEmbed(currentSource)
    else destroyEmbed()
    return destroyEmbed
  }, [currentSource?.id, loadEmbed, destroyEmbed])

  if (sources.length === 0) return (
    <EmptyPreview
      icon={<LayoutDashboard size={28} className="text-ink-faint" />}
      title="No dashboards connected"
      description="Connect a Power BI report from the Sources panel"
    />
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, overflow: 'hidden' }}>

      {sources.length > 1 && (
        <div className="flex gap-1 px-3 py-2 border-b border-border overflow-x-auto scrollbar-none shrink-0 bg-surface">
          {sources.map((s, i) => (
            <button key={s.id ?? s.alias} onClick={() => setSelected(i)}
              className={cn(
                'px-3 py-1 rounded-full text-xs font-medium whitespace-nowrap transition-colors',
                i === safeSelected ? 'bg-[#2D5A8C] text-white' : 'bg-white text-ink-muted border border-border hover:border-[#2D5A8C]',
              )}>
              {getDisplayAlias(s)}
            </button>
          ))}
        </div>
      )}

      <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>

        {(loading || schemaBuilding) && (
          <div style={{ position: 'absolute', inset: 0, zIndex: 20, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10, background: 'white' }}>
            <Loader2 size={26} className="animate-spin text-[#2D5A8C]" />
            {schemaBuilding && !loading ? (
              <>
                <span className="text-xs font-medium text-ink">Building AI schema…</span>
                <span className="text-[11px] text-ink-faint max-w-[220px] text-center">{schemaStep}</span>
                <div className="w-40 bg-border rounded-full h-1.5 overflow-hidden">
                  <div
                    className="bg-[#2D5A8C] h-1.5 rounded-full transition-all duration-500"
                    style={{ width: `${schemaPercent}%` }}
                  />
                </div>
                <span className="text-[10px] text-ink-faint">{schemaPercent}%</span>
              </>
            ) : (
              <span className="text-xs text-ink-faint">Loading dashboard…</span>
            )}
          </div>
        )}

        {/* Subtle indicator when re-capturing context after page/slicer change */}
        {capturingContext && !loading && (
          <div style={{ position: 'absolute', top: 8, right: 8, zIndex: 10 }}>
            <div className="flex items-center gap-1.5 bg-white/90 border border-border rounded-full px-2 py-1 shadow-sm">
              <Loader2 size={10} className="animate-spin text-[#2D5A8C]" />
              <span className="text-[10px] text-ink-faint">Syncing…</span>
            </div>
          </div>
        )}

        {error && !loading && (
          <div style={{ position: 'absolute', inset: 0, zIndex: 20, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, textAlign: 'center', padding: '0 24px' }}>
            <AlertCircle size={28} className="text-tertiary" />
            <p className="text-sm font-semibold text-ink">Failed to load dashboard</p>
            <p className="text-xs text-ink-muted max-w-xs">{error}</p>
            <button onClick={() => loadEmbed(currentSource)} className="btn-sm btn-outline mt-2">
              <RefreshCw size={12} /> Retry
            </button>
          </div>
        )}

        <div
          ref={embedRef}
          data-pbi-host=""
          style={{
            position:        'absolute',
            inset:           0,
            backgroundColor: '#ffffff',
            visibility:      error ? 'hidden' : 'visible',
          }}
        />
      </div>
    </div>
  )
}

/* ── Shared helpers ─────────────────────────────────────────────── */
const CountBadge: React.FC<{ n: number }> = ({ n }) => (
  <span className="ml-1 px-1.5 py-0.5 rounded-full text-[10px] font-semibold bg-[#2D5A8C]-muted text-[#2D5A8C]">{n}</span>
)

const EmptyPreview: React.FC<{ icon: React.ReactNode; title: string; description: string }> = ({ icon, title, description }) => (
  <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-6 animate-fade-in">
    <div className="w-12 h-12 rounded-full bg-surface-raised flex items-center justify-center">{icon}</div>
    <div>
      <p className="text-sm font-semibold text-ink">{title}</p>
      <p className="text-xs text-ink-muted mt-1 leading-snug max-w-[200px]">{description}</p>
    </div>
  </div>
)

export default PreviewPanel