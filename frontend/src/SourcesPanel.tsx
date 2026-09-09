import React, { useState, useEffect, useRef } from 'react'
import { Plus, Database, FileText, LayoutDashboard, Trash2, Loader2, RefreshCw, Cloud, Globe, Pencil } from 'lucide-react'
import axios from 'axios'
import { cn } from './lib/utils'
import { API_BASE } from './config'
import { useNotebook } from './NotebookContext'
import type { NotebookSource } from './NotebookContext'
import AddSourcesOverlay from './AddSourcesOverlay'
import SourceDetailDrawer from './SourceDetailDrawer'

/* ── DB type icon map ───────────────────────────────────────────── */
const DB_ICON_MAP: Record<string, string> = {
  sqlite:     '/sqlite.svg',
  postgresql: '/postgre.svg',
  mysql:      '/mysql.png',
  databricks: '/databricks.png',
  snowflake:  '/snowflake.png',
  powerbi:    '/powerbi.png',
  tableau:    '/tableau.png',
  excel:      '/excel.svg',
  sharepoint: '/sharepoint.svg',
}

const DbTypeIcon: React.FC<{ dbType?: string; size?: number }> = ({ dbType, size = 14 }) => {
  const key = dbType?.toLowerCase() ?? ''
  if (key === 'website') return <Globe size={size} className="text-[#2D5A8C] shrink-0" />
  if (key === 'sharepoint') return <Cloud size={size} className="text-[#2D5A8C] shrink-0" />
  const src = DB_ICON_MAP[key]
  if (src) return <img src={src} alt={dbType} style={{ width: size, height: size }} className="object-contain shrink-0" />
  return null
}

/* ── Icon by source type ─────────────────────────────────────────── */
const SourceIcon: React.FC<{ type: NotebookSource['type']; size?: number }> = ({ type, size = 14 }) => {
  if (type === 'database')  return <Database size={size} className="text-[#2D5A8C]" />
  if (type === 'document')  return <FileText size={size} className="text-[#2D5A8C]" />
  if (type === 'dashboard') return <LayoutDashboard size={size} className="text-[#2D5A8C]" />
  return null
}

/* ════════════════════════════════════════════════════════════════ */

const SourcesPanel: React.FC = () => {
  const {
    sources, sourcesLoading, refreshSources, notebook,
    isSourceEnabled, toggleSourceEnabled, getDisplayAlias, renameSource,
  } = useNotebook()
  const [modalOpen, setModalOpen]   = useState(false)
  const [deleting, setDeleting]     = useState<string | null>(null)
  const [drawerSource, setDrawerSource] = useState<NotebookSource | null>(null)

  const handleDelete = async (source: NotebookSource) => {
    setDeleting(source.id)
    try {
      if (source.type === 'document') {
        // Removes from document store AND ChromaDB vector index
        await axios.delete(`${API_BASE}/api/documents/${source.id}`, { withCredentials: true })
      } else if (source.type === 'dashboard') {
        // Delete the dashboard row entirely (cascades notebook_dashboards via FK)
        await axios.delete(`${API_BASE}/api/dashboards/${source.id}`, { withCredentials: true })
      } else {
        // Disconnect database source from the in-memory engine + user_data_sources table
        const params = notebook?.id ? `?notebook_id=${notebook.id}` : ''
        await axios.post(
          `${API_BASE}/api/datasources/disconnect-source${params}`,
          { alias: source.alias },
          { withCredentials: true },
        )
      }
      await refreshSources()
    } catch (e) {
      console.error('Failed to remove source', e)
    } finally {
      setDeleting(null)
    }
  }

  const databases  = sources.filter(s => s.type === 'database')
  const websites   = sources.filter(s => s.type === 'document' && s.db_type === 'website')
  const documents  = sources.filter(s => s.type === 'document' && s.db_type !== 'website')
  const dashboards = sources.filter(s => s.type === 'dashboard')

  return (
    <>
      <div className="flex flex-col h-full overflow-hidden">

        {/* ── Add sources button ── */}
        <div className="px-3 py-3 shrink-0 border-b border-border">
          <button
            onClick={() => setModalOpen(true)}
            className="w-full flex items-center justify-center gap-2 btn-md btn-primary"
          >
            <Plus size={15} />
            Add sources
          </button>
        </div>

        {/* ── Sources list ── */}
        <div className="flex-1 overflow-y-auto px-3 py-3 scrollbar-none">

          {/* Refresh button */}
          <div className="flex items-center justify-between mb-3 px-1">
            <span className="text-[11px] font-semibold text-ink-faint uppercase tracking-wider">
              {sources.length} Connected
            </span>
            <button
              onClick={refreshSources}
              className="p-1 rounded text-ink-faint hover:text-[#2D5A8C] hover:bg-[#2D5A8C]-muted transition-colors"
              title="Refresh sources"
            >
              <RefreshCw size={11} className={sourcesLoading ? 'animate-spin' : ''} />
            </button>
          </div>

          {sourcesLoading && sources.length === 0 ? (
            <div className="flex items-center justify-center py-10 gap-2 text-ink-faint">
              <Loader2 size={14} className="animate-spin" />
              <span className="text-xs">Loading…</span>
            </div>
          ) : sources.length === 0 ? (
            <EmptyState onAdd={() => setModalOpen(true)} />
          ) : (
            <div className="flex flex-col gap-4">
              {/* ── Chat source filter ── */}
              {/* "All sources" is now a bulk convenience action over the
                  per-INDIVIDUAL-source checkboxes below — there is no
                  separate category-level toggle anymore. Checked when
                  every connected source is currently enabled; clicking it
                  either enables everything or disables everything. */}
              <div className="rounded-lg border border-border bg-surface px-2.5 py-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={sources.length > 0 && sources.every(s => isSourceEnabled(s.id))}
                    onChange={() => {
                      const allEnabled = sources.every(s => isSourceEnabled(s.id))
                      sources.forEach(s => {
                        if (isSourceEnabled(s.id) === allEnabled) toggleSourceEnabled(s.id)
                      })
                    }}
                    className="accent-secondary w-3.5 h-3.5 rounded"
                  />
                  <span className="text-xs font-semibold text-ink">All sources</span>
                  <span className="text-[10px] text-ink-faint ml-auto">used in chat</span>
                </label>
              </div>

              <SourceGroup label="Databases"  icon={<Database size={12} />}       items={databases}  deleting={deleting} onDelete={handleDelete} onOpen={setDrawerSource}
                isSourceEnabled={isSourceEnabled} onToggleSourceEnabled={toggleSourceEnabled} getDisplayAlias={getDisplayAlias} onRename={renameSource} />
              <SourceGroup label="Documents"  icon={<FileText size={12} />}        items={documents}  deleting={deleting} onDelete={handleDelete} onOpen={setDrawerSource}
                isSourceEnabled={isSourceEnabled} onToggleSourceEnabled={toggleSourceEnabled} getDisplayAlias={getDisplayAlias} onRename={renameSource} />
              <SourceGroup label="Websites"   icon={<Globe size={12} />}           items={websites}   deleting={deleting} onDelete={handleDelete} onOpen={setDrawerSource}
                linkedNote="Same as Documents"
                isSourceEnabled={isSourceEnabled} onToggleSourceEnabled={toggleSourceEnabled} getDisplayAlias={getDisplayAlias} onRename={renameSource} />
              <SourceGroup label="Dashboards" icon={<LayoutDashboard size={12} />} items={dashboards} deleting={deleting} onDelete={handleDelete} onOpen={setDrawerSource}
                isSourceEnabled={isSourceEnabled} onToggleSourceEnabled={toggleSourceEnabled} getDisplayAlias={getDisplayAlias} onRename={renameSource} />
            </div>
          )}
        </div>
      </div>

      {/* ── Modal — rendered outside panel so it overlays full screen ── */}
      {modalOpen && (
        <AddSourcesOverlay onClose={() => { setModalOpen(false); refreshSources() }} />
      )}

      {/* ── Source detail drawer ── */}
      {drawerSource && (
        <SourceDetailDrawer source={drawerSource} onClose={() => setDrawerSource(null)} />
      )}
    </>
  )
}

/* ── Source group ────────────────────────────────────────────────── */
const SourceGroup: React.FC<{
  label: string; icon: React.ReactNode; items: NotebookSource[]
  deleting: string | null; onDelete: (s: NotebookSource) => void
  onOpen: (s: NotebookSource) => void
  linkedNote?: string
  isSourceEnabled: (id: string) => boolean
  onToggleSourceEnabled: (id: string) => void
  getDisplayAlias: (s: NotebookSource) => string
  onRename: (id: string, newAlias: string) => void
}> = ({
  label, icon, items, deleting, onDelete, onOpen, linkedNote,
  isSourceEnabled, onToggleSourceEnabled, getDisplayAlias, onRename,
}) => {
  if (items.length === 0) return null
  return (
    <div>
      <div className="flex items-center gap-1.5 mb-1.5 px-1">
        <span className="text-ink-faint">{icon}</span>
        <span className="text-[10px] font-semibold text-ink-faint uppercase tracking-wider truncate">{label}</span>
        {linkedNote && (
          <span className="text-[9px] text-ink-faint italic truncate">({linkedNote})</span>
        )}
      </div>
      <div className="flex flex-col gap-1">
        {items.map(source => (
          <SourcePill
            key={source.id} source={source} deleting={deleting === source.id}
            onDelete={() => onDelete(source)} onOpen={() => onOpen(source)}
            enabled={isSourceEnabled(source.id)} onToggleEnabled={() => onToggleSourceEnabled(source.id)}
            displayAlias={getDisplayAlias(source)} onRename={(newAlias) => onRename(source.id, newAlias)}
          />
        ))}
      </div>
    </div>
  )
}

/* ── Source pill ─────────────────────────────────────────────────── */
const SourcePill: React.FC<{
  source: NotebookSource; deleting: boolean; onDelete: () => void; onOpen: () => void
  enabled: boolean; onToggleEnabled: () => void
  displayAlias: string; onRename: (newAlias: string) => void
}> = ({ source, deleting, onDelete, onOpen, enabled, onToggleEnabled, displayAlias, onRename }) => {
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [editing, setEditing]     = useState(false)
  const [editValue, setEditValue] = useState(displayAlias)
  const inputRef = useRef<HTMLInputElement>(null)

  const startEditing = (e: React.MouseEvent) => {
    e.stopPropagation()
    setEditValue(displayAlias)
    setEditing(true)
  }

  const commitRename = () => {
    setEditing(false)
    if (editValue.trim() && editValue.trim() !== displayAlias) onRename(editValue)
  }

  useEffect(() => {
    if (editing) { inputRef.current?.focus(); inputRef.current?.select() }
  }, [editing])

  if (confirmDelete) return (
    <div className="flex flex-col gap-1.5 p-2.5 rounded-lg border border-tertiary/30 bg-tertiary-muted">
      <p className="text-xs text-ink">Remove <span className="font-semibold">{displayAlias}</span>?</p>
      <div className="flex gap-1.5">
        <button onClick={() => setConfirmDelete(false)} className="btn-sm btn-outline flex-1 text-xs py-1">Cancel</button>
        <button onClick={onDelete} disabled={deleting} className="btn-sm btn-tertiary flex-1 text-xs py-1">
          {deleting ? <Loader2 size={11} className="animate-spin" /> : 'Remove'}
        </button>
      </div>
    </div>
  )

  return (
    <div
      onClick={editing ? undefined : onOpen}
      className={cn(
        'group flex items-center gap-2 px-2.5 py-2 rounded-lg cursor-pointer',
        'border border-border bg-white',
        'hover:border-[#2D5A8C] hover:bg-[#2D5A8C]-muted',
        'transition-all duration-150',
        !enabled && 'opacity-50',
      )}
    >
      {/* Per-source "used in chat" checkbox — the ONLY chat-inclusion
          control now; there is no separate category-level toggle. */}
      <input
        type="checkbox"
        checked={enabled}
        onChange={e => { e.stopPropagation(); onToggleEnabled() }}
        onClick={e => e.stopPropagation()}
        title="Include this source in chat"
        className="accent-secondary w-3 h-3 rounded shrink-0"
      />
      {/* Connected dot */}
      <span className="w-1.5 h-1.5 rounded-full bg-green-500 shrink-0" title="Connected" />
      {source.db_type && (DB_ICON_MAP[source.db_type.toLowerCase()] || source.db_type === 'website' || source.db_type === 'sharepoint')
        ? <DbTypeIcon dbType={source.db_type} size={source.db_type?.toLowerCase() === 'sharepoint' ? 32 : 14} />
        : <SourceIcon type={source.type} size={13} />
      }
      <div className="flex-1 min-w-0">
        {editing ? (
          <input
            ref={inputRef}
            value={editValue}
            onChange={e => setEditValue(e.target.value)}
            onClick={e => e.stopPropagation()}
            onBlur={commitRename}
            onKeyDown={e => {
              if (e.key === 'Enter') commitRename()
              if (e.key === 'Escape') { setEditing(false); setEditValue(displayAlias) }
            }}
            className="text-xs font-medium text-ink w-full bg-white border border-[#2D5A8C] rounded px-1 py-0.5 outline-none"
          />
        ) : (
          <p
            onClick={startEditing}
            title="Click to rename (display only)"
            className="text-xs font-medium text-ink truncate hover:underline flex items-center gap-1"
          >
            <span className="truncate">{displayAlias}</span>
            <Pencil size={10} className="text-ink-faint opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
          </p>
        )}
        <p className="text-[10px] text-ink-faint capitalize leading-none mt-0.5">
          {source.db_type === 'website'
            ? 'Website'
            : source.db_type?.toLowerCase() === 'sharepoint'
            ? 'SharePoint'
            : source.db_type || ''}
        </p>
      </div>
      <button
        onClick={e => { e.stopPropagation(); setConfirmDelete(true) }}
        className="p-1 rounded text-ink-faint opacity-0 group-hover:opacity-100 hover:text-tertiary hover:bg-tertiary-muted transition-all duration-150 shrink-0"
        title="Remove"
      >
        <Trash2 size={11} />
      </button>
    </div>
  )
}

/* ── Empty state ─────────────────────────────────────────────────── */
const EmptyState: React.FC<{ onAdd: () => void }> = ({ onAdd }) => (
  <div className="flex flex-col items-center justify-center gap-3 py-10 text-center px-4">
    <div className="w-10 h-10 rounded-full bg-[#2D5A8C]-muted flex items-center justify-center">
      <Database size={18} className="text-[#2D5A8C]" />
    </div>
    <div>
      <p className="text-xs font-semibold text-ink">No sources yet</p>
      <p className="text-[11px] text-ink-faint mt-1 leading-snug">
        Connect a database, upload documents, or embed a dashboard
      </p>
    </div>
    <button onClick={onAdd} className="btn-sm btn-primary mt-1">
      <Plus size={13} /> Add source
    </button>
  </div>
)

export default SourcesPanel