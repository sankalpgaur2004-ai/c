import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import {
  ArrowLeft, Plus, Trash2, MessageSquare, Loader2,
  LogOut, Pencil, Check, X, Settings, ChevronDown, Share2,
} from 'lucide-react'
import { cn } from './lib/utils'
import { NotebookProvider, useNotebook } from './NotebookContext'
import type { NotebookChat } from './NotebookContext'
import SourcesPanel from './SourcesPanel'
import ChatInput from './components/ChatInput'
import ProjectConfigModal from './ProjectConfigModal'
import type { ProjectConfig } from './ProjectConfigModal'
import CreateChatModal from './CreateChatModal'
import { useSessionContext } from './App'
import ShareChatModal from './ShareChatModal'
import AddSourcesOverlay from './AddSourcesOverlay'

/* ────────────────────────────────────────────────────────────────── */

const PERSONAS = [
  { value: 'executive',     label: 'Executive' },
  { value: 'analyst',       label: 'Analyst' },
  { value: 'sales_manager', label: 'Sales Manager' },
  { value: 'field_rep',     label: 'Field Rep' },
]

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins  = Math.floor(diff / 60000)
  const hours = Math.floor(diff / 3600000)
  const days  = Math.floor(diff / 86400000)
  if (mins < 1)   return 'Just now'
  if (mins < 60)  return `${mins}m ago`
  if (hours < 24) return `${hours}h ago`
  if (days < 30)  return `${days}d ago`
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

/* ── Page wrapper — provides NotebookContext ─────────────────────── */
const ProjectDetailPage: React.FC = () => {
  const { notebookId } = useParams<{ notebookId: string }>()
  if (!notebookId) return null
  return (
    <NotebookProvider notebookId={notebookId}>
      <ProjectDetailShell />
    </NotebookProvider>
  )
}

/* ── Inner shell — consumes NotebookContext ──────────────────────── */
const ProjectDetailShell: React.FC = () => {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { currentUser, logout } = useSessionContext()
  const {
    notebook, loading, error,
    chats, chatsLoading, loadChats,
    createChat, deleteChat, renameChat,
    updateConfig, sources, sourcesLoading,
    sourceFilter,
  } = useNotebook()

  // ── Force "Add sources" on freshly created projects ──
  // HomePage navigates here with ?new=1 right after creating a notebook.
  // Until the user connects at least one source, we keep the Add Sources
  // overlay open and don't let them close it (no X, no backdrop click).
  const isNewProject = searchParams.get('new') === '1'
  const [forceAddSources, setForceAddSources] = useState(false)

  useEffect(() => {
    if (isNewProject && !loading && !sourcesLoading) {
      setForceAddSources(sources.length === 0)
    }
  }, [isNewProject, loading, sourcesLoading, sources.length])

  // Once a source has been added, drop the mandatory overlay + clean the URL
  useEffect(() => {
    if (isNewProject && sources.length > 0) {
      setForceAddSources(false)
      const next = new URLSearchParams(searchParams)
      next.delete('new')
      setSearchParams(next, { replace: true })
    }
  }, [isNewProject, sources.length, searchParams, setSearchParams])

  // Page-level transition state for smooth navigate-out
  const [exiting, setExiting]       = useState(false)
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const [deletingId, setDeletingId]     = useState<string | null>(null)
  const [renamingId, setRenamingId]     = useState<string | null>(null)
  const [renameVal, setRenameVal]       = useState('')
  const [editingName, setEditingName]   = useState(false)
  const [nameVal, setNameVal]           = useState('')
  const [showConfig, setShowConfig]     = useState(false)
  const [showCreateChatModal, setShowCreateChatModal] = useState(false)
  const [sharingChat, setSharingChat]   = useState<NotebookChat | null>(null)
  const nameInputRef = useRef<HTMLInputElement>(null)
  const userRef = useRef<HTMLDivElement>(null)
  const renameRef = useRef<HTMLInputElement>(null)

  // Instruction + persona panel state
  const [instructions, setInstructions] = useState('')
  const [persona, setPersona] = useState('')
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Sync from notebook once loaded
  useEffect(() => {
    if (notebook) {
      setInstructions(notebook.instructions ?? '')
      setNameVal(notebook.name ?? '')
    }
  }, [notebook?.id])

  useEffect(() => { if (editingName) nameInputRef.current?.focus() }, [editingName])

  // Close user menu on outside click
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (userRef.current && !userRef.current.contains(e.target as Node)) setUserMenuOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])

  // Focus rename input
  useEffect(() => { if (renamingId) renameRef.current?.focus() }, [renamingId])

  /* ── Commit project name edit ── */
  const commitNameEdit = async () => {
    setEditingName(false)
    const trimmed = nameVal.trim()
    if (trimmed && trimmed !== notebook?.name) {
      await updateConfig({ name: trimmed })
    } else {
      setNameVal(notebook?.name ?? '')
    }
  }

  /* ── Create new chat with persona ── */
  const handleCreateChat = async (title: string, persona?: string) => {
    const chat = await createChat(title, persona)
    setShowCreateChatModal(false)
    setExiting(true)
    setTimeout(() => {
      navigate(`/notebook/${notebook!.id}/chat/${chat.id}`)
    }, 180)
  }

  /* ── Debounced instruction save ── */
  const handleInstructionsChange = (val: string) => {
    setInstructions(val)
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      updateConfig({ instructions: val })
    }, 800)
  }

  /* ── Launch a new chat — store query in sessionStorage then navigate ── */
  const handleLaunch = useCallback(async (question: string, filter: string) => {
    const title = question.length > 60 ? question.slice(0, 57) + '…' : question
    const chat = await createChat(title, persona || undefined)
    // Store pending query so ChatPanel picks it up immediately on mount
    sessionStorage.setItem('pendingChatQuery', JSON.stringify({ question, filter }))
    setExiting(true)
    setTimeout(() => {
      navigate(`/notebook/${notebook!.id}/chat/${chat.id}`)
    }, 180)
  }, [createChat, navigate, notebook, persona])

  /* ── Open an existing chat ── */
  const handleOpenChat = (chat: NotebookChat) => {
    setExiting(true)
    setTimeout(() => {
      navigate(`/notebook/${notebook!.id}/chat/${chat.id}`)
    }, 180)
  }

  /* ── Delete chat ── */
  const handleDeleteChat = async (e: React.MouseEvent, chatId: string) => {
    e.stopPropagation()
    setDeletingId(chatId)
    try { await deleteChat(chatId) } finally { setDeletingId(null) }
  }

  /* ── Rename chat ── */
  const startRename = (e: React.MouseEvent, chat: NotebookChat) => {
    e.stopPropagation()
    setRenamingId(chat.id)
    setRenameVal(chat.title)
  }
  const commitRename = async () => {
    if (!renamingId || !renameVal.trim()) { setRenamingId(null); return }
    await renameChat(renamingId, renameVal.trim())
    setRenamingId(null)
  }

  const initials = currentUser
    ? `${currentUser.first_name?.[0] ?? ''}${currentUser.last_name?.[0] ?? ''}`.toUpperCase()
    : '?'

  /* ── Loading ── */
  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-surface">
        <div className="flex flex-col items-center gap-4 animate-fade-in">
          <div className="w-10 h-10 rounded-full border-4 border-[#2D5A8C]/20 border-t-[#2D5A8C] animate-spin" />
          <p className="text-sm text-ink-muted">Loading project…</p>
        </div>
      </div>
    )
  }

  if (error || !notebook) {
    return (
      <div className="flex items-center justify-center h-screen bg-surface">
        <div className="text-center">
          <p className="text-sm font-semibold text-ink">Project not found</p>
          <button onClick={() => navigate('/')} className="btn-md btn-primary mt-4">
            <ArrowLeft size={14} /> Back home
          </button>
        </div>
      </div>
    )
  }

  /* ════════════════════════════════════════════════════════════════ */
  return (
    <div
      className={cn(
        'min-h-screen bg-[#F5F6FA] flex flex-col transition-opacity duration-200',
        exiting ? 'opacity-0' : 'opacity-100 animate-fade-in',
      )}
    >

      {/* ── Header ── */}
      <header className="bg-white border-b border-border px-8 py-4 flex items-center gap-4 shrink-0">

        {/* Back */}
        <button
          onClick={() => navigate('/home')}
          className="p-2 rounded-lg text-ink-faint hover:text-[#2D5A8C] hover:bg-[#2D5A8C]/8 transition-colors"
          title="Back to projects"
        >
          <ArrowLeft size={18} />
        </button>

        {/* Logo */}
        <img
          src="/CIRCULANTSLOGO.png"
          alt="Circulants"
          className="h-12 w-auto object-contain"
          onError={e => { (e.currentTarget as HTMLImageElement).src = '/circulants.png' }}
        />

        {/* Project name — click to edit */}
        <div className="flex-1 flex items-center gap-2 min-w-0">
          {editingName ? (
            <input
              ref={nameInputRef}
              value={nameVal}
              onChange={e => setNameVal(e.target.value)}
              onBlur={commitNameEdit}
              onKeyDown={e => {
                if (e.key === 'Enter') commitNameEdit()
                if (e.key === 'Escape') { setEditingName(false); setNameVal(notebook?.name ?? '') }
              }}
              className="text-xl font-bold text-[#2D5A8C] bg-transparent border-b-2 border-[#2D5A8C] outline-none w-full max-w-sm"
            />
          ) : (
            <button
              onClick={() => { setNameVal(notebook.name); setEditingName(true) }}
              className="group flex items-center gap-2 text-xl font-bold text-[#2D5A8C] truncate hover:opacity-80 transition-opacity"
              title="Click to rename"
            >
              <span className="truncate">{notebook.name}</span>
              <Pencil size={14} className="shrink-0 opacity-0 group-hover:opacity-50 transition-opacity" />
            </button>
          )}
        </div>

        {/* Settings + User avatar */}
        <div className="flex items-center gap-3 shrink-0" ref={userRef}>
          <button
            onClick={() => setShowConfig(true)}
            title="Project settings"
            className="p-2 rounded-lg text-ink-faint hover:text-[#2D5A8C] hover:bg-[#2D5A8C]/8 transition-colors"
          >
            <Settings size={18} />
          </button>

          <button
            onClick={() => setUserMenuOpen(o => !o)}
            className="w-9 h-9 rounded-full bg-[#2D5A8C] flex items-center justify-center text-white text-sm font-bold hover:opacity-90 transition-opacity"
          >
            {initials}
          </button>
          {userMenuOpen && (
            <div className="absolute top-11 right-0 z-50 w-56 bg-white rounded-xl shadow-lg border border-border py-1 animate-fade-in">
              <div className="px-4 py-3 border-b border-border">
                <p className="text-sm font-semibold text-ink">{currentUser?.first_name} {currentUser?.last_name}</p>
                <p className="text-xs text-ink-muted mt-0.5 truncate">{currentUser?.email}</p>
              </div>
              <button onClick={logout}
                className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-tertiary hover:bg-tertiary-muted transition-colors">
                <LogOut size={14} /> Sign out
              </button>
            </div>
          )}
        </div>
      </header>

      {/* ── Mandatory add-sources overlay for freshly created projects ── */}
      {forceAddSources && (
        <AddSourcesOverlay
          mandatory
          onClose={() => setForceAddSources(false)}
        />
      )}

      {/* ── Create chat modal ── */}
      {showCreateChatModal && (
        <CreateChatModal
          onSubmit={handleCreateChat}
          onClose={() => setShowCreateChatModal(false)}
        />
      )}

      {/* ── Share chat modal ── */}
      {sharingChat && (
        <ShareChatModal
          notebookId={notebook.id}
          chatId={sharingChat.id}
          chatTitle={sharingChat.title}
          onClose={() => setSharingChat(null)}
        />
      )}

      {/* ── Project config modal ── */}
      {showConfig && (
        <ProjectConfigModal
          mode="edit"
          initialValues={{
            name:         notebook.name,
            description:  notebook.description ?? '',
            instructions: instructions,
          }}
          onSubmit={async (config: ProjectConfig) => {
            await updateConfig(config)
            setShowConfig(false)
          }}
          onClose={() => setShowConfig(false)}
        />
      )}

      {/* ── Body — two columns ── */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── LEFT — Chats column ── */}
        <div className="flex-1 flex flex-col overflow-hidden px-8 py-6 min-w-0">

          {/* New chat input */}
          <div className="bg-white rounded-2xl shadow-sm border border-border mb-6">
            <div className="px-5 pt-4 pb-1">
              <p className="text-xs font-semibold text-ink-faint uppercase tracking-wider mb-3">
                Start a new chat
              </p>
            </div>
            {/* Same ChatInput as the notebook — source filter is set via checkboxes in Data Sources panel */}
            <ChatInput
              onSubmit={(question, filter) => handleLaunch(question, filter)}
              loading={false}
              sourceFilter={sourceFilter}
              placeholder="What would you like to explore?"
            />
          </div>

          {/* Chat list */}
          <div className="flex items-center justify-between mb-3">
            <p className="text-sm font-semibold text-ink">Your Chats</p>
            <button
              onClick={() => setShowCreateChatModal(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-[#2D5A8C] hover:bg-[#2D5A8C]/8 border border-[#2D5A8C]/20 transition-colors"
            >
              <Plus size={13} /> New chat
            </button>
          </div>

          <div className="flex-1 overflow-y-auto scrollbar-none">
            {chatsLoading ? (
              <div className="flex items-center justify-center py-16 gap-2 text-ink-faint">
                <Loader2 size={14} className="animate-spin" />
                <span className="text-xs">Loading chats…</span>
              </div>
            ) : chats.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 gap-3 text-center">
                <div className="w-12 h-12 rounded-2xl bg-[#2D5A8C]/8 flex items-center justify-center">
                  <MessageSquare size={22} className="text-[#2D5A8C]" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-ink">No chats yet</p>
                  <p className="text-xs text-ink-muted mt-1">Type a message above to start your first chat</p>
                </div>
              </div>
            ) : (
              <div className="flex flex-col gap-1">
                {chats.map(chat => (
                  <ChatRow
                    key={chat.id}
                    chat={chat}
                    isRenaming={renamingId === chat.id}
                    renameVal={renameVal}
                    renameRef={renamingId === chat.id ? renameRef : undefined}
                    deleting={deletingId === chat.id}
                    onOpen={() => handleOpenChat(chat)}
                    onDelete={e => handleDeleteChat(e, chat.id)}
                    onShare={e => { e.stopPropagation(); setSharingChat(chat) }}
                    onRenameStart={e => startRename(e, chat)}
                    onRenameChange={setRenameVal}
                    onRenameCommit={commitRename}
                    onRenameCancel={() => setRenamingId(null)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        {/* ── RIGHT — Config sidebar ── */}
        <aside className="w-80 shrink-0 border-l border-border bg-white overflow-y-auto scrollbar-none">

          {/* Persona + Instructions */}
          <div className="px-5 py-5 border-b border-border flex-shrink-0">
            <div className="flex items-center justify-between mb-4">
              <p className="text-sm font-semibold text-ink">Chat Settings</p>
            </div>

            {/* Persona */}
            <div className="mb-4">
              <label className="text-xs font-medium text-ink-faint block mb-1.5">Default Chat Persona</label>
              <p className="text-xs text-ink-faint mb-2">
                Applied to new chats launched from this sidebar
              </p>
              <div className="relative">
                <select
                  value={persona}
                  onChange={e => setPersona(e.target.value)}
                  className="input text-sm appearance-none pr-8"
                >
                  <option value="">No persona</option>
                  {PERSONAS.map(p => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                </select>
                <ChevronDown size={13} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-ink-faint pointer-events-none" />
              </div>
            </div>

            {/* Instructions textarea */}
            <div>
              <label className="text-xs font-medium text-ink-faint block mb-1.5">
                Custom instructions
                <span className="text-ink-faint font-normal ml-1">(saved automatically)</span>
              </label>
              <textarea
                value={instructions}
                onChange={e => handleInstructionsChange(e.target.value)}
                rows={4}
                placeholder="Add instructions to tailor responses for this project…"
                className="input text-sm resize-none"
              />
            </div>
          </div>

          {/* Data Sources — reuse SourcesPanel as-is */}
          <div className="flex-shrink-0">
            <div className="px-5 py-4 border-b border-border">
              <p className="text-sm font-semibold text-ink">Data Sources</p>
              <p className="text-xs text-ink-muted mt-0.5">
                {sources.length} source{sources.length !== 1 ? 's' : ''} connected
              </p>
            </div>
            <div className="px-5 pb-5">
              {/* SourcesPanel reads from NotebookContext — works with zero changes */}
              <SourcesPanel />
            </div>
          </div>

        </aside>
      </div>
    </div>
  )
}

/* ── Chat row ────────────────────────────────────────────────────── */
interface ChatRowProps {
  chat: NotebookChat
  isRenaming: boolean
  renameVal: string
  renameRef?: React.RefObject<HTMLInputElement>
  deleting: boolean
  onOpen: () => void
  onDelete: (e: React.MouseEvent) => void
  onShare: (e: React.MouseEvent) => void
  onRenameStart: (e: React.MouseEvent) => void
  onRenameChange: (v: string) => void
  onRenameCommit: () => void
  onRenameCancel: () => void
}

const ChatRow: React.FC<ChatRowProps> = ({
  chat, isRenaming, renameVal, renameRef, deleting,
  onOpen, onDelete, onShare, onRenameStart, onRenameChange, onRenameCommit, onRenameCancel,
}) => (
  <div
    onClick={!isRenaming ? onOpen : undefined}
    className={cn(
      'group flex items-center gap-3 px-4 py-3.5 rounded-xl cursor-pointer',
      'border border-transparent hover:border-border hover:bg-white',
      'transition-all duration-150',
      isRenaming && 'border-[#2D5A8C]/30 bg-white',
    )}
  >
    {/* Icon */}
    <div className="w-8 h-8 rounded-lg bg-[#2D5A8C]/8 flex items-center justify-center shrink-0">
      <MessageSquare size={14} className="text-[#2D5A8C]" />
    </div>

    {/* Title + meta */}
    <div className="flex-1 min-w-0">
      {isRenaming ? (
        <div className="flex items-center gap-1.5" onClick={e => e.stopPropagation()}>
          <input
            ref={renameRef}
            value={renameVal}
            onChange={e => onRenameChange(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') onRenameCommit()
              if (e.key === 'Escape') onRenameCancel()
            }}
            className="flex-1 text-sm font-medium border border-[#2D5A8C]/30 rounded px-2 py-0.5 outline-none focus:ring-2 focus:ring-[#2D5A8C]/20"
          />
          <button onClick={onRenameCommit} className="p-1 rounded text-[#2D5A8C] hover:bg-[#2D5A8C]/10 transition-colors">
            <Check size={13} />
          </button>
          <button onClick={e => { e.stopPropagation(); onRenameCancel() }} className="p-1 rounded text-ink-faint hover:bg-surface transition-colors">
            <X size={13} />
          </button>
        </div>
      ) : (
        <>
          <p className="text-sm font-medium text-ink truncate">{chat.title}</p>
          <div className="flex items-center gap-2 mt-0.5">
            <p className="text-xs text-ink-faint">
              {chat.message_count > 0
                ? `${chat.message_count} message${chat.message_count !== 1 ? 's' : ''} · `
                : ''}
              {timeAgo(chat.updated_at)}
            </p>
          </div>
        </>
      )}
    </div>

    {/* Actions — visible on hover */}
    {!isRenaming && (
      <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
        <button
          onClick={onShare}
          className="p-1.5 rounded-lg text-ink-faint hover:text-[#2D5A8C] hover:bg-[#2D5A8C]/8 transition-colors"
          title="Share chat"
        >
          <Share2 size={12} />
        </button>
        <button
          onClick={onRenameStart}
          className="p-1.5 rounded-lg text-ink-faint hover:text-[#2D5A8C] hover:bg-[#2D5A8C]/8 transition-colors"
          title="Rename"
        >
          <Pencil size={12} />
        </button>
        <button
          onClick={onDelete}
          disabled={deleting}
          className="p-1.5 rounded-lg text-ink-faint hover:text-tertiary hover:bg-tertiary-muted transition-colors disabled:opacity-40"
          title="Delete chat"
        >
          {deleting ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
        </button>
      </div>
    )}
  </div>
)

export default ProjectDetailPage