import React, { useState, useCallback, useRef, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, Database, LayoutDashboard, Loader2, Settings, LogOut } from 'lucide-react'
import ProjectConfigModal from './ProjectConfigModal'
import type { ProjectConfig } from './ProjectConfigModal'
import { cn } from './lib/utils'
import { NotebookProvider, useNotebook } from './NotebookContext'
import ResizablePanel from './ResizablePanel'
import SourcesPanel from './SourcesPanel'
import ChatPanel from './ChatPanel'
import PreviewPanel from './PreviewPanel'
import { useSessionContext } from './App'

/* ── Layout constants ───────────────────────────────────────────── */
const SOURCES_DEFAULT  = 260
const SOURCES_MIN      = 150
const SOURCES_MAX      = 500
const PREVIEW_DEFAULT  = 500
const PREVIEW_MIN      = 300
const PREVIEW_MAX      = 1000

/* ════════════════════════════════════════════════════════════════ */

const NotebookPage: React.FC = () => {
  const { notebookId, chatId } = useParams<{ notebookId: string; chatId?: string }>()
  const { currentUser, logout } = useSessionContext()

  if (!notebookId) return null

  return (
    <NotebookProvider notebookId={notebookId}>
      <NotebookShell
        notebookId={notebookId}
        currentUser={currentUser}
        onLogout={logout}
        chatId={chatId}
      />
    </NotebookProvider>
  )
}

/* ── Inner shell (has access to NotebookContext) ─────────────────── */
interface ShellProps {
  notebookId: string
  currentUser: any
  onLogout: () => void
  chatId?: string
}

const NotebookShell: React.FC<ShellProps> = ({ notebookId, currentUser, onLogout, chatId }) => {
  const navigate   = useNavigate()
  const { notebook, loading, error, renameNotebook, updateConfig } = useNotebook()
  const [showConfig, setShowConfig] = useState(false)
  const [editing, setEditing] = useState(false)
  const [nameValue, setNameValue] = useState('')
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const userRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { setNameValue(notebook?.name ?? '') }, [notebook?.name])
  useEffect(() => { if (editing) inputRef.current?.focus() }, [editing])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (userRef.current && !userRef.current.contains(e.target as Node)) setUserMenuOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const commitRename = async () => {
    setEditing(false)
    const trimmed = nameValue.trim()
    if (trimmed && trimmed !== notebook?.name) {
      await renameNotebook(trimmed)
    } else {
      setNameValue(notebook?.name ?? '')
    }
  }

  const initials = currentUser
    ? `${currentUser.first_name?.[0] ?? ''}${currentUser.last_name?.[0] ?? ''}`.toUpperCase()
    : '?'

  /* ── Loading state ── */
  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-surface">
        <div className="flex flex-col items-center gap-4 animate-fade-in">
          <div className="w-10 h-10 rounded-full border-4 border-[#2D5A8C]-muted border-t-secondary animate-spin" />
          <p className="text-sm text-ink-muted">Loading notebook…</p>
        </div>
      </div>
    )
  }

  /* ── Error state ── */
  if (error || !notebook) {
    return (
      <div className="flex items-center justify-center h-screen bg-surface">
        <div className="flex flex-col items-center gap-4 text-center animate-fade-in">
          <p className="text-sm font-semibold text-ink">Notebook not found</p>
          <p className="text-xs text-ink-muted">This notebook may have been deleted or you don't have access.</p>
          <button onClick={() => navigate('/')} className="btn-md btn-primary">
            <ArrowLeft size={14} /> Back to notebooks
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-surface">

      {/* ── Header (integrated into page) ── */}
      <div className="flex items-center px-8 py-4 gap-4">
        {/* Back button */}
        <button
          onClick={() => navigate(`/notebook/${notebookId}`)}
          className="p-2 rounded-lg text-ink-faint hover:text-[#2D5A8C] hover:bg-[#2D5A8C]/8 transition-colors shrink-0"
          title="Back to project"
        >
          <ArrowLeft size={18} />
        </button>

        {/* Logo */}
        <a href="/" className="shrink-0">
          <img
            src="/CIRCULANTSLOGO.png"
            alt="Circulants"
            className="h-16 w-auto object-contain"
            onError={e => { (e.currentTarget as HTMLImageElement).src = '/circulants.png' }}
          />
        </a>

        {/* Center — Notebook name */}
        <div className="flex-1 flex justify-center -mt-3">
          {editing ? (
            <input
              ref={inputRef}
              value={nameValue}
              onChange={e => setNameValue(e.target.value)}
              onBlur={commitRename}
              onKeyDown={e => {
                if (e.key === 'Enter') commitRename()
                if (e.key === 'Escape') { setEditing(false); setNameValue(notebook?.name ?? '') }
              }}
              className="text-center text-lg font-semibold px-4 py-2 rounded-lg border border-border focus:outline-none focus:ring-2 focus:ring-secondary"
              onClick={e => e.stopPropagation()}
            />
          ) : (
            <button
              onClick={() => setEditing(true)}
              className="text-center text-lg font-semibold px-4 py-2 rounded-lg hover:bg-[#2D5A8C]-muted transition-colors"
            >
              {notebook?.name || 'Untitled'}
            </button>
          )}
        </div>

        {/* Right — Settings + User avatar */}
        <div className="flex items-center gap-3 shrink-0 -mt-3" ref={userRef}>
          <button
            onClick={() => setShowConfig(true)}
            title="Project settings"
            className="p-2 rounded-lg text-ink-faint hover:text-[#2D5A8C] hover:bg-[#2D5A8C]-muted transition-colors"
          >
            <Settings size={18} />
          </button>

          <button
            onClick={() => setUserMenuOpen(o => !o)}
            className="w-10 h-10 rounded-full bg-tertiary flex items-center justify-center text-white text-sm font-bold hover:opacity-90 transition-opacity"
          >
            {initials}
          </button>

          {userMenuOpen && (
            <div className="absolute top-20 right-8 z-50 w-56 bg-white rounded-xl shadow-lg border border-border py-1">
              <div className="px-4 py-3 border-b border-border">
                <p className="text-sm font-semibold text-ink">{currentUser?.first_name} {currentUser?.last_name}</p>
                <p className="text-xs text-ink-muted mt-0.5 truncate">{currentUser?.email}</p>
              </div>
              <button
                onClick={onLogout}
                className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-tertiary hover:bg-tertiary-muted transition-colors"
              >
                <LogOut size={14} /> Sign out
              </button>
            </div>
          )}
        </div>
      </div>

      {/* ── Project config modal ── */}
      {showConfig && (
        <ProjectConfigModal
          mode="edit"
          initialValues={{
            name:         notebook.name,
            description:  notebook.description ?? '',
            instructions: notebook.instructions ?? '',
          }}
          onSubmit={async (config: ProjectConfig) => {
            await updateConfig(config)
            setShowConfig(false)
          }}
          onClose={() => setShowConfig(false)}
        />
      )}

      {/* ── 3-panel workspace with card styling ── */}
      <div className="flex flex-1 overflow-hidden px-4 gap-4 -mt-4 pb-4">

        {/* LEFT — Sources Card */}
        <div className="flex-shrink-0 rounded-lg shadow-lg overflow-hidden bg-white border border-border">
          <ResizablePanel
            defaultWidth={SOURCES_DEFAULT}
            minWidth={SOURCES_MIN}
            maxWidth={SOURCES_MAX}
            resizeEdge="right"
            collapsible
            collapsedIcon={<Database size={18} />}
            collapsedLabel="Sources"
            header={
              <span className="panel-title">Sources</span>
            }
            className="border-none"
          >
            <SourcesPanel />
          </ResizablePanel>
        </div>

        {/* CENTER — Chat Card */}
        <div className="flex-1 min-w-0 rounded-lg shadow-lg overflow-hidden bg-white border border-border">
          <div className="flex flex-col overflow-hidden h-full">
            <ChatPanel chatId={chatId} />
          </div>
        </div>

        {/* RIGHT — Preview Card */}
        <div className="flex-shrink-0 rounded-lg shadow-lg overflow-hidden bg-white border border-border">
          <ResizablePanel
            defaultWidth={PREVIEW_DEFAULT}
            minWidth={PREVIEW_MIN}
            maxWidth={PREVIEW_MAX}
            resizeEdge="left"
            collapsible
            collapsedIcon={<LayoutDashboard size={18} />}
            collapsedLabel="Preview"
            header={
              <span className="panel-title">Preview</span>
            }
            className="border-none"
          >
            <PreviewPanel />
          </ResizablePanel>
        </div>

      </div>
    </div>
  )
}

export default NotebookPage