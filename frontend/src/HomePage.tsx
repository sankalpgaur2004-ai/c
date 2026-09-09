import React, { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import {
  Plus, MoreVertical, Pencil, Trash2, Search,
  Loader2, LayoutGrid, List, ChevronDown,
  FileText, LayoutDashboard, LogOut, Settings, Globe, Table, Home,
} from 'lucide-react'
import ProjectConfigModal from './ProjectConfigModal'
import type { ProjectConfig } from './ProjectConfigModal'
import { cn } from './lib/utils'
import { API_BASE } from './config'
import type { CurrentUser } from './App'

/* ── Types ──────────────────────────────────────────────────────── */
export interface Notebook {
  id: string
  name: string
  description?: string
  persona?: string
  created_at: string
  updated_at: string
  source_count: number
  db_count: number
  table_count: number
  doc_count: number
  website_count: number
  dashboard_count: number
  is_shared?: number | boolean
  shared_by?: string | null
}

type SortKey = 'recent' | 'oldest' | 'name'
type ViewMode = 'grid' | 'list'

interface HomePageProps {
  currentUser: CurrentUser | null
  onLogout: () => void
}

function formatDate(iso: string) {
  const d = new Date(iso)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

/* ════════════════════════════════════════════════════════════════ */
const HomePage: React.FC<HomePageProps> = ({ currentUser, onLogout }) => {
  const navigate = useNavigate()

  const [notebooks, setNotebooks]     = useState<Notebook[]>([])
  const [loading, setLoading]         = useState(true)
  const [creating, setCreating]       = useState(false)
  const [search, setSearch]           = useState('')
  const [sort, setSort]               = useState<SortKey>('recent')
  const [viewMode, setViewMode]       = useState<ViewMode>('grid')
  const [showCreate, setShowCreate]   = useState(false)
  const [menuOpen, setMenuOpen]       = useState<string | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)
  const [renaming, setRenaming]       = useState<string | null>(null)
  const [renameVal, setRenameVal]     = useState('')
  const [sortOpen, setSortOpen]       = useState(false)
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const menuRef    = useRef<HTMLDivElement>(null)
  const sortRef    = useRef<HTMLDivElement>(null)
  const userRef    = useRef<HTMLDivElement>(null)
  const renameRef  = useRef<HTMLInputElement>(null)

  useEffect(() => { fetchNotebooks() }, [])
  useEffect(() => { if (renaming) renameRef.current?.focus() }, [renaming])

  // Refetch notebooks when page becomes visible (user returns from notebook)
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (!document.hidden) {
        fetchNotebooks()
      }
    }
    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange)
  }, [])

  /* Close dropdowns on outside click */
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(null)
      if (sortRef.current && !sortRef.current.contains(e.target as Node)) setSortOpen(false)
      if (userRef.current && !userRef.current.contains(e.target as Node)) setUserMenuOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const fetchNotebooks = async () => {
    try {
      setLoading(true)
      const r = await axios.get(`${API_BASE}/api/notebooks`, { withCredentials: true })
      setNotebooks(r.data)
    } catch { } finally { setLoading(false) }
  }

  const handleCreateSubmit = async (config: ProjectConfig) => {
    setCreating(true)
    try {
      const r = await axios.post(`${API_BASE}/api/notebooks`, config, { withCredentials: true })
      navigate(`/notebook/${r.data.id}?new=1`)
    } catch (err) { setCreating(false); throw err }
  }

  const handleDelete = async (id: string) => {
    try {
      await axios.delete(`${API_BASE}/api/notebooks/${id}`, { withCredentials: true })
      setNotebooks(prev => prev.filter(n => n.id !== id))
    } catch { } finally { setDeleteConfirm(null) }
  }

  const commitRename = async (id: string) => {
    const trimmed = renameVal.trim()
    if (!trimmed) { setRenaming(null); return }
    try {
      await axios.patch(`${API_BASE}/api/notebooks/${id}`, { name: trimmed }, { withCredentials: true })
      setNotebooks(prev => prev.map(n => n.id === id ? { ...n, name: trimmed } : n))
    } catch { } finally { setRenaming(null) }
  }

  /* Sort + filter */
  const sorted = [...notebooks]
    .filter(n => n.name.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => {
      if (sort === 'name')   return a.name.localeCompare(b.name)
      if (sort === 'oldest') return new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
      return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
    })

  const ownNotebooks    = sorted.filter(n => !n.is_shared)
  const sharedNotebooks = sorted.filter(n => !!n.is_shared)

  const initials = currentUser
    ? `${currentUser.first_name?.[0] ?? ''}${currentUser.last_name?.[0] ?? ''}`.toUpperCase()
    : '?'

  /* ══════════════════════════════════════════════════════════════ */
  return (
    <div className="min-h-screen bg-white flex flex-col">

      {/* ── Top bar ── */}
      <header className="fixed top-0 left-0 right-0 z-40 bg-white flex items-center px-8 py-4">

        {/* Logo — pinned to top-left */}
        <img
          src="/CIRCULANTSLOGO.png"
          alt="Circulants"
          className="h-20 w-auto object-contain"
          onError={e => { (e.currentTarget as HTMLImageElement).src = '/circulants.png' }}
        />

        {/* Center pill */}
        <div className="flex-1 flex justify-center -ml-20">
          <div className="px-8 py-3 rounded-full text-white text-2xl font-bold -mt-4" style={{ backgroundColor: '#2D5A8C' }}>
            ConvergeAI
          </div>
        </div>

        {/* Home — back to a2 */}
        <a
          href="https://a2.circulants.ai"
          className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium text-ink-muted hover:text-[#2D5A8C] hover:bg-[#2D5A8C]-muted transition-colors -mt-6 mr-3"
          title="Back to a2"
        >
          <Home size={16} /> Home
        </a>

        {/* User avatar — pinned to top-right */}
        <div className="relative -mt-6" ref={userRef}>
          <button
            onClick={() => setUserMenuOpen(o => !o)}
            className="w-10 h-10 rounded-full bg-tertiary flex items-center justify-center text-white text-sm font-bold hover:opacity-90 transition-opacity"
          >
            {initials}
          </button>

          {userMenuOpen && (
            <div className="absolute top-12 right-0 z-50 w-56 bg-white rounded-xl shadow-lg border border-border py-1 animate-fade-in">
              <div className="px-4 py-3 border-b border-border">
                <p className="text-sm font-semibold text-ink">{currentUser?.first_name} {currentUser?.last_name}</p>
                <p className="text-xs text-ink-muted mt-0.5 truncate">{currentUser?.email}</p>
              </div>
              <button onClick={onLogout}
                className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-tertiary hover:bg-tertiary-muted transition-colors">
                <LogOut size={14} /> Sign out
              </button>
            </div>
          )}
        </div>
      </header>

      {/* ── Main ── */}
      <main className="flex-1 w-full pt-32 pb-8" style={{ paddingLeft: '10%', paddingRight: '10%' }}>

        {/* ── Toolbar ── */}
        <div className="flex items-center gap-3 mb-8 flex-wrap">

          {/* Search */}
          <div className="relative flex-1 min-w-[200px] max-w-sm">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint pointer-events-none" />
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search projects…"
              className="input pl-9 text-sm"
            />
          </div>

          {/* View toggle */}
          <div className="flex items-center border border-border rounded-lg overflow-hidden">
            <button
              onClick={() => setViewMode('grid')}
              className={cn('p-2 transition-colors', viewMode === 'grid' ? 'bg-[#2D5A8C] text-white' : 'text-ink-faint hover:bg-surface')}
            >
              <LayoutGrid size={15} />
            </button>
            <button
              onClick={() => setViewMode('list')}
              className={cn('p-2 transition-colors', viewMode === 'list' ? 'bg-[#2D5A8C] text-white' : 'text-ink-faint hover:bg-surface')}
            >
              <List size={15} />
            </button>
          </div>

          {/* Sort */}
          <div className="relative" ref={sortRef}>
            <button
              onClick={() => setSortOpen(o => !o)}
              className="flex items-center gap-2 px-3 py-2 rounded-lg border border-border text-sm text-ink hover:bg-surface transition-colors"
            >
              {sort === 'recent' ? 'Most recent' : sort === 'oldest' ? 'Oldest first' : 'A → Z'}
              <ChevronDown size={13} className={cn('transition-transform', sortOpen && 'rotate-180')} />
            </button>
            {sortOpen && (
              <div className="absolute right-0 top-10 z-30 w-40 bg-white rounded-xl border border-border shadow-lg py-1 animate-fade-in">
                {(['recent', 'oldest', 'name'] as SortKey[]).map(s => (
                  <button key={s} onClick={() => { setSort(s); setSortOpen(false) }}
                    className={cn('w-full text-left px-4 py-2 text-sm transition-colors',
                      sort === s ? 'text-[#2D5A8C] font-medium bg-[#2D5A8C]-muted' : 'text-ink hover:bg-surface')}>
                    {s === 'recent' ? 'Most recent' : s === 'oldest' ? 'Oldest first' : 'A → Z'}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Create button */}
          <button
            onClick={() => setShowCreate(true)}
            disabled={creating}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-white text-sm font-medium transition-colors disabled:opacity-60 shrink-0"
            style={{ backgroundColor: '#2D5A8C' }}
            onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = '#3A6BA8')}
            onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = '#2D5A8C')}
          >
            {creating ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}
            Create new project
          </button>
        </div>

        {/* ── Loading ── */}
        {loading && (
          <div className="flex items-center justify-center py-32 gap-3 text-ink-faint">
            <Loader2 size={20} className="animate-spin text-[#2D5A8C]" />
            <span className="text-sm">Loading projects…</span>
          </div>
        )}

        {/* ── Empty state ── */}
        {!loading && notebooks.length === 0 && (
          <div className="flex flex-col items-center justify-center py-32 gap-4 text-center">
            <div className="w-16 h-16 rounded-2xl bg-[#2D5A8C]-muted flex items-center justify-center">
              <Plus size={28} className="text-[#2D5A8C]" />
            </div>
            <div>
              <p className="text-base font-semibold text-ink">No projects yet</p>
              <p className="text-sm text-ink-muted mt-1">Create your first project to get started</p>
            </div>
            <button onClick={() => setShowCreate(true)} className="btn-md btn-primary mt-2">
              <Plus size={15} /> Create project
            </button>
          </div>
        )}

        {/* ── Section: Recent projects ── */}
        {!loading && ownNotebooks.length > 0 && (
          <>
            <p className="text-base font-semibold text-ink mb-4">
              {search ? `Results for "${search}"` : 'My projects'}
            </p>

            {viewMode === 'grid' ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-5 animate-fade-in">
                {ownNotebooks.map((nb) => (
                  <ProjectCard
                    key={nb.id}
                    notebook={nb}
                    isMenuOpen={menuOpen === nb.id}
                    isRenaming={renaming === nb.id}
                    renameVal={renameVal}
                    renameRef={renameRef}
                    isDeleteConfirm={deleteConfirm === nb.id}
                    menuRef={menuOpen === nb.id ? menuRef : undefined}
                    onOpen={() => navigate(`/notebook/${nb.id}`, { state: { notebook: nb } })}
                    onMenuToggle={() => setMenuOpen(menuOpen === nb.id ? null : nb.id)}
                    onRenameStart={() => { setMenuOpen(null); setRenameVal(nb.name); setRenaming(nb.id) }}
                    onRenameChange={setRenameVal}
                    onRenameCommit={() => commitRename(nb.id)}
                    onRenameCancel={() => setRenaming(null)}
                    onDeleteRequest={() => { setMenuOpen(null); setDeleteConfirm(nb.id) }}
                    onDeleteConfirm={() => handleDelete(nb.id)}
                    onDeleteCancel={() => setDeleteConfirm(null)}
                  />
                ))}
              </div>
            ) : (
              <div className="flex flex-col divide-y divide-border border border-border rounded-xl overflow-hidden animate-fade-in">
                {ownNotebooks.map((nb) => (
                  <ProjectRow
                    key={nb.id}
                    notebook={nb}
                    isMenuOpen={menuOpen === nb.id}
                    isRenaming={renaming === nb.id}
                    renameVal={renameVal}
                    renameRef={renameRef}
                    isDeleteConfirm={deleteConfirm === nb.id}
                    menuRef={menuOpen === nb.id ? menuRef : undefined}
                    onOpen={() => navigate(`/notebook/${nb.id}`, { state: { notebook: nb } })}
                    onMenuToggle={() => setMenuOpen(menuOpen === nb.id ? null : nb.id)}
                    onRenameStart={() => { setMenuOpen(null); setRenameVal(nb.name); setRenaming(nb.id) }}
                    onRenameChange={setRenameVal}
                    onRenameCommit={() => commitRename(nb.id)}
                    onRenameCancel={() => setRenaming(null)}
                    onDeleteRequest={() => { setMenuOpen(null); setDeleteConfirm(nb.id) }}
                    onDeleteConfirm={() => handleDelete(nb.id)}
                    onDeleteCancel={() => setDeleteConfirm(null)}
                  />
                ))}
              </div>
            )}
          </>
        )}

        {/* ── Section: Shared chats ── */}
        {!loading && sharedNotebooks.length > 0 && (
          <>
            <p className="text-base font-semibold text-ink mb-4 mt-8 flex items-center gap-2">
              Shared chats
            </p>

            {viewMode === 'grid' ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-5 animate-fade-in">
                {sharedNotebooks.map((nb) => (
                  <ProjectCard
                    key={nb.id}
                    notebook={nb}
                    isMenuOpen={menuOpen === nb.id}
                    isRenaming={renaming === nb.id}
                    renameVal={renameVal}
                    renameRef={renameRef}
                    isDeleteConfirm={deleteConfirm === nb.id}
                    menuRef={menuOpen === nb.id ? menuRef : undefined}
                    onOpen={() => navigate(`/notebook/${nb.id}`, { state: { notebook: nb } })}
                    onMenuToggle={() => setMenuOpen(menuOpen === nb.id ? null : nb.id)}
                    onRenameStart={() => { setMenuOpen(null); setRenameVal(nb.name); setRenaming(nb.id) }}
                    onRenameChange={setRenameVal}
                    onRenameCommit={() => commitRename(nb.id)}
                    onRenameCancel={() => setRenaming(null)}
                    onDeleteRequest={() => { setMenuOpen(null); setDeleteConfirm(nb.id) }}
                    onDeleteConfirm={() => handleDelete(nb.id)}
                    onDeleteCancel={() => setDeleteConfirm(null)}
                  />
                ))}
              </div>
            ) : (
              <div className="flex flex-col divide-y divide-border border border-border rounded-xl overflow-hidden animate-fade-in">
                {sharedNotebooks.map((nb) => (
                  <ProjectRow
                    key={nb.id}
                    notebook={nb}
                    isMenuOpen={menuOpen === nb.id}
                    isRenaming={renaming === nb.id}
                    renameVal={renameVal}
                    renameRef={renameRef}
                    isDeleteConfirm={deleteConfirm === nb.id}
                    menuRef={menuOpen === nb.id ? menuRef : undefined}
                    onOpen={() => navigate(`/notebook/${nb.id}`, { state: { notebook: nb } })}
                    onMenuToggle={() => setMenuOpen(menuOpen === nb.id ? null : nb.id)}
                    onRenameStart={() => { setMenuOpen(null); setRenameVal(nb.name); setRenaming(nb.id) }}
                    onRenameChange={setRenameVal}
                    onRenameCommit={() => commitRename(nb.id)}
                    onRenameCancel={() => setRenaming(null)}
                    onDeleteRequest={() => { setMenuOpen(null); setDeleteConfirm(nb.id) }}
                    onDeleteConfirm={() => handleDelete(nb.id)}
                    onDeleteCancel={() => setDeleteConfirm(null)}
                  />
                ))}
              </div>
            )}
          </>
        )}

        {!loading && search && sorted.length === 0 && (
          <p className="text-center text-sm text-ink-faint py-16">No projects match "{search}"</p>
        )}
      </main>

      {/* ── Create modal ── */}
      {showCreate && (
        <ProjectConfigModal
          mode="create"
          onSubmit={handleCreateSubmit}
          onClose={() => setShowCreate(false)}
        />
      )}
    </div>
  )
}

/* ── Shared card props ───────────────────────────────────────────── */
interface CardProps {
  notebook: Notebook
  accent?: string
  isMenuOpen: boolean
  isRenaming: boolean
  renameVal: string
  renameRef: React.RefObject<HTMLInputElement>
  isDeleteConfirm: boolean
  menuRef?: React.RefObject<HTMLDivElement>
  onOpen: () => void
  onMenuToggle: () => void
  onRenameStart: () => void
  onRenameChange: (v: string) => void
  onRenameCommit: () => void
  onRenameCancel: () => void
  onDeleteRequest: () => void
  onDeleteConfirm: () => void
  onDeleteCancel: () => void
}

/* ── Grid card ───────────────────────────────────────────────────── */
const ProjectCard: React.FC<CardProps> = ({
  notebook, accent,
  isMenuOpen, isRenaming, renameVal, renameRef, isDeleteConfirm,
  menuRef, onOpen, onMenuToggle,
  onRenameStart, onRenameChange, onRenameCommit, onRenameCancel,
  onDeleteRequest, onDeleteConfirm, onDeleteCancel,
}) => (
  <div className={cn(
    'group relative flex flex-col gap-4 p-4 rounded-lg cursor-pointer',
    'border border-border bg-white',
    'hover:shadow-md hover:border-[#2D5A8C]/30 transition-all duration-200',
  )}>

    {/* Top section: Icon + Name + Menu */}
    <div className="flex items-start gap-3" onClick={onOpen}>
      <div className="shrink-0 w-11 h-11 rounded-lg bg-[#2D5A8C]-muted flex items-center justify-center">
        <FileText size={18} className="text-[#2D5A8C]" />
      </div>

      <div className="flex-1 min-w-0 flex-col">
        {isRenaming ? (
          <input ref={renameRef} value={renameVal}
            onChange={e => onRenameChange(e.target.value)}
            onBlur={onRenameCommit}
            onKeyDown={e => { if (e.key === 'Enter') onRenameCommit(); if (e.key === 'Escape') onRenameCancel() }}
            className="input text-sm font-semibold py-0.5 px-2 w-full"
            onClick={e => { e.stopPropagation(); e.preventDefault() }} />
        ) : (
          <button
            className="text-sm font-semibold text-ink hover:text-[#2D5A8C] transition-colors truncate text-left">
            {notebook.name}
          </button>
        )}
        <p className="text-xs text-ink-faint mt-1">
          {formatDate(notebook.updated_at || notebook.created_at)}
          {notebook.shared_by && <span className="text-[#2D5A8C]"> · Shared by {notebook.shared_by}</span>}
        </p>
      </div>

      {/* Menu button */}
      <div className="relative shrink-0" ref={menuRef}>
        <button onClick={e => { e.stopPropagation(); onMenuToggle() }}
          className={cn('p-1 rounded text-ink-faint hover:bg-surface hover:text-[#2D5A8C] transition-colors',
            'opacity-0 group-hover:opacity-100', isMenuOpen && 'opacity-100')}>
          <MoreVertical size={16} />
        </button>
        {isMenuOpen && (
          <div className="absolute right-0 top-7 z-30 w-40 bg-white rounded-xl shadow-lg border border-border py-1 animate-fade-in">
            <CardMenuAction icon={<Pencil size={12} />} label="Rename" onClick={onRenameStart} />
            <CardMenuAction icon={<Trash2 size={12} />} label="Delete" onClick={onDeleteRequest} danger />
          </div>
        )}
      </div>
    </div>

    {/* Source counts — only shown when > 0 */}
    <div className="flex flex-wrap items-center gap-2 text-xs">
      {notebook.table_count > 0 && (
        <span className="flex items-center gap-1 px-2 py-1 rounded bg-surface text-ink-faint">
          <Table size={12} /> {notebook.table_count} {notebook.table_count === 1 ? 'Table' : 'Tables'}
        </span>
      )}
      {notebook.doc_count > 0 && (
        <span className="flex items-center gap-1 px-2 py-1 rounded bg-surface text-ink-faint">
          <FileText size={12} /> {notebook.doc_count} Doc
        </span>
      )}
      {notebook.website_count > 0 && (
        <span className="flex items-center gap-1 px-2 py-1 rounded bg-surface text-ink-faint">
          <Globe size={12} /> {notebook.website_count} Web
        </span>
      )}
      {notebook.dashboard_count > 0 && (
        <span className="flex items-center gap-1 px-2 py-1 rounded bg-surface text-ink-faint">
          <LayoutDashboard size={12} /> {notebook.dashboard_count} Dash
        </span>
      )}
      {notebook.source_count === 0 && (
        <span className="text-[10px] text-ink-faint italic">No sources yet</span>
      )}
    </div>

    {/* Delete confirm overlay */}
    {isDeleteConfirm && (
      <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-white/95 backdrop-blur-sm rounded-lg px-4">
        <p className="text-sm font-medium text-ink text-center">Delete "{notebook.name}"?</p>
        <p className="text-xs text-ink-muted">This cannot be undone.</p>
        <div className="flex gap-2">
          <button onClick={onDeleteCancel} className="btn-sm btn-outline">Cancel</button>
          <button onClick={onDeleteConfirm} className="btn-sm btn-tertiary">Delete</button>
        </div>
      </div>
    )}
  </div>
)

/* ── List row ────────────────────────────────────────────────────── */
const ProjectRow: React.FC<CardProps> = ({
  notebook, accent,
  isMenuOpen, isRenaming, renameVal, renameRef, isDeleteConfirm,
  menuRef, onOpen, onMenuToggle,
  onRenameStart, onRenameChange, onRenameCommit, onRenameCancel,
  onDeleteRequest, onDeleteConfirm, onDeleteCancel,
}) => (
  <div className="group relative flex items-center gap-4 px-5 py-4 bg-white hover:bg-surface transition-colors">
    {/* Color dot */}
    <div className={cn('w-10 h-10 rounded-xl shrink-0 bg-gradient-to-br', accent)} />

    {/* Name */}
    <div className="flex-1 min-w-0" onClick={onOpen}>
      {isRenaming ? (
        <input ref={renameRef} value={renameVal}
          onChange={e => onRenameChange(e.target.value)}
          onBlur={onRenameCommit}
          onKeyDown={e => { if (e.key === 'Enter') onRenameCommit(); if (e.key === 'Escape') onRenameCancel() }}
          className="input text-sm font-medium py-0.5 px-2 w-full max-w-xs"
          onClick={e => e.stopPropagation()} />
      ) : (
        <p className="text-sm font-semibold text-ink cursor-pointer hover:text-[#2D5A8C] transition-colors truncate">{notebook.name}</p>
      )}
      <p className="text-xs text-ink-faint mt-0.5">
        {notebook.source_count} source{notebook.source_count !== 1 ? 's' : ''}
        {notebook.shared_by && <span className="text-[#2D5A8C]"> · Shared by {notebook.shared_by}</span>}
      </p>
    </div>

    {/* Date */}
    <p className="text-xs text-ink-faint shrink-0 hidden sm:block">{formatDate(notebook.updated_at || notebook.created_at)}</p>

    {/* Menu */}
    <div className="relative shrink-0" ref={menuRef}>
      <button onClick={e => { e.stopPropagation(); onMenuToggle() }}
        className={cn('p-1.5 rounded-lg text-ink-faint hover:bg-[#2D5A8C]-muted hover:text-[#2D5A8C] transition-colors',
          'opacity-0 group-hover:opacity-100', isMenuOpen && 'opacity-100')}>
        <MoreVertical size={14} />
      </button>
      {isMenuOpen && (
        <div className="absolute right-0 top-8 z-30 w-40 bg-white rounded-xl shadow-lg border border-border py-1 animate-fade-in">
          <CardMenuAction icon={<Pencil size={12} />} label="Rename" onClick={onRenameStart} />
          <CardMenuAction icon={<Trash2 size={12} />} label="Delete" onClick={onDeleteRequest} danger />
        </div>
      )}
    </div>

    {/* Delete confirm overlay */}
    {isDeleteConfirm && (
      <div className="absolute inset-0 z-20 flex items-center justify-center gap-4 bg-white/95 backdrop-blur-sm px-6">
        <p className="text-sm font-medium text-ink">Delete "{notebook.name}"?</p>
        <button onClick={onDeleteCancel} className="btn-sm btn-outline">Cancel</button>
        <button onClick={onDeleteConfirm} className="btn-sm btn-tertiary">Delete</button>
      </div>
    )}
  </div>
)

/* ── Tiny helpers ────────────────────────────────────────────────── */
const CardMenuAction: React.FC<{ icon: React.ReactNode; label: string; onClick: () => void; danger?: boolean }> = ({ icon, label, onClick, danger }) => (
  <button onClick={e => { e.stopPropagation(); onClick() }}
    className={cn('w-full flex items-center gap-2.5 px-4 py-2 text-xs transition-colors',
      danger ? 'text-tertiary hover:bg-tertiary-muted' : 'text-ink hover:bg-[#2D5A8C]-muted hover:text-[#2D5A8C]')}>
    <span className={danger ? 'text-tertiary' : 'text-ink-faint'}>{icon}</span>
    {label}
  </button>
)

export default HomePage