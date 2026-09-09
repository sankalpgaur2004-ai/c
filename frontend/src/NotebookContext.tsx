import React, { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react'
import axios from 'axios'
import { API_BASE } from './config'
import type { ActiveFilter, VisualData, VisualInfo } from './powerbiApi'

/* ── Types ──────────────────────────────────────────────────────── */

export type SourceType = 'database' | 'document' | 'dashboard'

export interface NotebookSource {
  id: string
  alias: string
  type: SourceType
  db_type?: string
  label?: string
  connected_at: string
  notebook_id: string
  config?: Record<string, string>
}

export interface NotebookMeta {
  id: string
  name: string
  description?: string
  instructions?: string
  created_at: string
  updated_at: string
  source_count: number
  db_count: number
  doc_count: number
  dashboard_count: number
}

export interface NotebookChat {
  id: string
  title: string
  notebook_id: string
  persona?: string
  created_at: string
  updated_at: string
  message_count: number
}

export interface ReferencedDocument {
  doc_id: string
  filename: string
  category?: string
  page?: number
  relevance_score: number
  excerpt: string
}

export interface ChatMessage {
  id: string
  question?: string
  sql_query?: string
  data?: Record<string, any>[]
  columns?: string[]
  summary?: string
  follow_up_questions?: string[]
  chart_data?: Record<string, any>
  chart_type?: string
  error?: string
  out_of_scope?: boolean
  loading?: boolean
  sourceFilter?: string
  referenced_documents?: ReferencedDocument[]
}

export interface DashboardPage {
  name:        string
  displayName: string
}

/* ── Source filter (shared between ChatInput and SourcesPanel) ───── */
export const SOURCE_FILTER_KEYS = ['database', 'documents', 'dashboard'] as const
export type SourceFilterKey = typeof SOURCE_FILTER_KEYS[number]

interface NotebookCtx {
  notebook: NotebookMeta | null
  loading: boolean
  error: string | null

  sources: NotebookSource[]
  sourcesLoading: boolean
  refreshSources: () => Promise<void>

  chats: NotebookChat[]
  chatsLoading: boolean
  loadChats: () => Promise<void>
  createChat: (title?: string, persona?: string) => Promise<NotebookChat>
  deleteChat: (chatId: string, isActive?: boolean) => Promise<void>
  renameChat: (chatId: string, title: string) => Promise<void>

  // Bug 6 fix: activeChatId tracked here so loadMessages can guard stale writes
  activeChatId: string | null
  setActiveChatId: (id: string | null) => void

  messages: ChatMessage[]
  addMessage: (msg: ChatMessage) => void
  updateMessage: (id: string, patch: Partial<ChatMessage>) => void
  clearMessages: () => void
  loadMessages: (chatId: string) => Promise<void>
  // Bug 1 + Bug 9: persist completed messages server-side without double-serialisation
  persistMessage: (chatId: string, msg: ChatMessage) => Promise<void>

  previewTab: 'table' | 'dashboard' | 'document'
  setPreviewTab: (tab: 'table' | 'dashboard' | 'document') => void
  previewData: any[] | null
  previewColumns: string[] | null
  setPreviewData: (data: any[] | null, columns: string[] | null) => void

  renameNotebook: (name: string) => Promise<void>
  updateConfig: (config: { name?: string; instructions?: string; description?: string }) => Promise<void>

  activeDashboardPage:    DashboardPage | null
  setActiveDashboardPage: (page: DashboardPage | null) => void
  dashboardActiveFilters: ActiveFilter[]
  setDashboardActiveFilters: (filters: ActiveFilter[]) => void
  dashboardPageVisualData: VisualData[]
  setDashboardPageVisualData: (data: VisualData[]) => void
  // Full, unfiltered {title, type} list for every visual on the active page
  // (unlike dashboardPageVisualData, this isn't limited to exportable visuals).
  // Powers meta questions like "how many visuals are on this page".
  dashboardVisualInventory: VisualInfo[]
  setDashboardVisualInventory: (data: VisualInfo[]) => void
  // Reads the CURRENT dashboard context via refs rather than closed-over
  // state — callers that `await waitForDashboardReady()` before reading this
  // would otherwise get the stale values captured when their function/
  // closure was first created, not whatever was just freshly captured.
  getDashboardContext: () => { page: DashboardPage | null; filters: ActiveFilter[]; visualData: VisualData[]; visualInventory: VisualInfo[] }
  // True once the embedded report has finished a live capture of its current
  // page/filters/visuals. Some dashboard queries (Power BI JS SDK exportData,
  // etc.) only answer correctly using data captured while the report is
  // actually mounted — this lets a sender wait for a fresh capture instead
  // of firing off a question against stale/empty context.
  setDashboardContextReady: (ready: boolean) => void
  waitForDashboardReady: (timeoutMs?: number) => Promise<void>

  // Shared chat source filter — set via checkboxes in SourcesPanel, consumed by ChatInput/ChatPanel
  sourceFilter: string
  setSourceFilter: (filter: string) => void
  isSourceFilterActive: (key: SourceFilterKey) => boolean
  toggleSourceFilterKey: (key: SourceFilterKey) => void

  // Per-INDIVIDUAL-source inclusion — finer-grained than sourceFilter above
  // (which only toggles whole categories). A source is actually used in
  // chat only if BOTH its category is enabled AND its own id isn't
  // disabled here. Persisted to localStorage per notebook.
  isSourceEnabled: (sourceId: string) => boolean
  toggleSourceEnabled: (sourceId: string) => void
  disabledSourceIds: string[]

  // Frontend-only display rename — doesn't touch the underlying connection/
  // file/alias on the backend, purely a per-notebook display override so
  // e.g. a "sqlite" source can be shown as "patient_info" for clarity.
  // Persisted to localStorage per notebook.
  getDisplayAlias: (source: NotebookSource) => string
  renameSource: (sourceId: string, newAlias: string) => void
}

/* ── Context ────────────────────────────────────────────────────── */

const NotebookContext = createContext<NotebookCtx | null>(null)

export const useNotebook = () => {
  const ctx = useContext(NotebookContext)
  if (!ctx) throw new Error('useNotebook must be used within NotebookProvider')
  return ctx
}

/* ── Provider ───────────────────────────────────────────────────── */

interface NotebookProviderProps {
  notebookId: string
  children: React.ReactNode
}

export const NotebookProvider: React.FC<NotebookProviderProps> = ({ notebookId, children }) => {
  const [notebook, setNotebook]             = useState<NotebookMeta | null>(null)
  const [loading, setLoading]               = useState(true)
  const [error, setError]                   = useState<string | null>(null)
  const [sources, setSources]               = useState<NotebookSource[]>([])
  const [sourcesLoading, setSourcesLoading] = useState(false)
  const defaultTabAppliedRef = useRef<string | null>(null)   // tracks which notebookId we've already defaulted the tab for
  const [messages, setMessages]             = useState<ChatMessage[]>([])
  const [previewTab, setPreviewTab]         = useState<'table' | 'dashboard' | 'document'>('table')
  const [previewData, setPreviewDataState]  = useState<any[] | null>(null)
  const [previewColumns, setPreviewColumns] = useState<string[] | null>(null)
  const [chats, setChats]                   = useState<NotebookChat[]>([])
  const [chatsLoading, setChatsLoading]     = useState(false)

  // Bug 6: track active chatId to guard against stale async setMessages calls
  const [activeChatId, setActiveChatId]     = useState<string | null>(null)
  const activeChatIdRef                     = useRef<string | null>(null)
  useEffect(() => { activeChatIdRef.current = activeChatId }, [activeChatId])

  const [activeDashboardPage,    _setActiveDashboardPage]    = useState<DashboardPage | null>(null)
  const [dashboardActiveFilters, _setDashboardActiveFilters] = useState<ActiveFilter[]>([])
  const [dashboardPageVisualData, _setDashboardPageVisualData] = useState<VisualData[]>([])
  const [dashboardVisualInventory, _setDashboardVisualInventory] = useState<VisualInfo[]>([])

  // Refs mirror the state above so async callers (e.g. sendQuestion, after
  // awaiting waitForDashboardReady()) can read the CURRENT value instead of
  // whatever was captured in their closure before the await.
  const activeDashboardPageRef    = useRef<DashboardPage | null>(null)
  const dashboardActiveFiltersRef = useRef<ActiveFilter[]>([])
  const dashboardPageVisualDataRef = useRef<VisualData[]>([])
  const dashboardVisualInventoryRef = useRef<VisualInfo[]>([])

  const setActiveDashboardPage = useCallback((page: DashboardPage | null) => {
    activeDashboardPageRef.current = page
    _setActiveDashboardPage(page)
  }, [])
  const setDashboardActiveFilters = useCallback((filters: ActiveFilter[]) => {
    dashboardActiveFiltersRef.current = filters
    _setDashboardActiveFilters(filters)
  }, [])
  const setDashboardPageVisualData = useCallback((data: VisualData[]) => {
    dashboardPageVisualDataRef.current = data
    _setDashboardPageVisualData(data)
  }, [])
  const setDashboardVisualInventory = useCallback((data: VisualInfo[]) => {
    dashboardVisualInventoryRef.current = data
    _setDashboardVisualInventory(data)
  }, [])
  const getDashboardContext = useCallback(() => ({
    page:            activeDashboardPageRef.current,
    filters:         dashboardActiveFiltersRef.current,
    visualData:      dashboardPageVisualDataRef.current,
    visualInventory: dashboardVisualInventoryRef.current,
  }), [])

  // Ref (not state) — this only needs to be read inside an async wait loop,
  // never rendered, so a ref avoids stale-closure issues in sendQuestion's
  // async handler without forcing extra re-renders on every capture.
  const dashboardContextReadyRef = useRef(false)
  const setDashboardContextReady = useCallback((ready: boolean) => {
    dashboardContextReadyRef.current = ready
  }, [])
  const waitForDashboardReady = useCallback((timeoutMs: number = 6000): Promise<void> => {
    return new Promise(resolve => {
      if (dashboardContextReadyRef.current) { resolve(); return }
      const start = Date.now()
      const interval = setInterval(() => {
        if (dashboardContextReadyRef.current || Date.now() - start > timeoutMs) {
          clearInterval(interval)
          resolve()
        }
      }, 150)
    })
  }, [])

  /* ── Shared source filter (checkboxes in SourcesPanel drive this) ── */
  const [sourceFilter, setSourceFilter] = useState<string>('all')

  const isSourceFilterActive = useCallback((key: SourceFilterKey) => {
    return sourceFilter === 'all' || sourceFilter.split(',').filter(Boolean).includes(key)
  }, [sourceFilter])

  const toggleSourceFilterKey = useCallback((key: SourceFilterKey) => {
    setSourceFilter(prev => {
      const current = prev === 'all' ? [...SOURCE_FILTER_KEYS] : prev.split(',').filter(Boolean)
      const idx = current.indexOf(key)
      if (idx > -1) current.splice(idx, 1)
      else current.push(key)
      // Fall back to 'all' when nothing selected, or auto-promote when all three are ticked
      if (current.length === 0 || current.length === SOURCE_FILTER_KEYS.length) return 'all'
      return current.join(',')
    })
  }, [])

  /* ── Per-source enable/disable + display rename ────────────────────
     Both persisted to localStorage, keyed per notebookId, so they survive
     reloads but don't leak across different notebooks. Loaded/reset
     whenever notebookId changes. */
  const [disabledSourceIds, setDisabledSourceIds] = useState<string[]>([])
  const [aliasOverrides, setAliasOverrides]        = useState<Record<string, string>>({})

  const disabledKey  = useCallback((id: string) => `nb-disabled-sources:${id}`, [])
  const overridesKey = useCallback((id: string) => `nb-source-aliases:${id}`, [])

  useEffect(() => {
    if (!notebookId) return
    try {
      const rawDisabled = localStorage.getItem(disabledKey(notebookId))
      setDisabledSourceIds(rawDisabled ? JSON.parse(rawDisabled) : [])
    } catch { setDisabledSourceIds([]) }
    try {
      const rawOverrides = localStorage.getItem(overridesKey(notebookId))
      setAliasOverrides(rawOverrides ? JSON.parse(rawOverrides) : {})
    } catch { setAliasOverrides({}) }
  }, [notebookId, disabledKey, overridesKey])

  const isSourceEnabled = useCallback((sourceId: string) => !disabledSourceIds.includes(sourceId), [disabledSourceIds])

  const toggleSourceEnabled = useCallback((sourceId: string) => {
    setDisabledSourceIds(prev => {
      const next = prev.includes(sourceId) ? prev.filter(id => id !== sourceId) : [...prev, sourceId]
      try { localStorage.setItem(disabledKey(notebookId), JSON.stringify(next)) } catch {}
      return next
    })
  }, [notebookId, disabledKey])

  const getDisplayAlias = useCallback((source: NotebookSource) => aliasOverrides[source.id] ?? source.alias, [aliasOverrides])

  const renameSource = useCallback((sourceId: string, newAlias: string) => {
    const trimmed = newAlias.trim()
    setAliasOverrides(prev => {
      const next = { ...prev }
      if (trimmed) next[sourceId] = trimmed
      else delete next[sourceId]   // renaming back to empty clears the override
      try { localStorage.setItem(overridesKey(notebookId), JSON.stringify(next)) } catch {}
      return next
    })
  }, [notebookId, overridesKey])

  /* ── Notebook meta ── */
  useEffect(() => {
    if (!notebookId) return
    setLoading(true); setError(null)
    axios.get(`${API_BASE}/api/notebooks/${notebookId}`, { withCredentials: true })
      .then(r => setNotebook(r.data))
      .catch(() => setError('Failed to load notebook'))
      .finally(() => setLoading(false))
  }, [notebookId])

  /* ── Sources ── */
  const refreshSources = useCallback(async () => {
    if (!notebookId) return
    setSourcesLoading(true)
    try {
      const [dbRes, dashRes, docsRes] = await Promise.allSettled([
        axios.get(`${API_BASE}/api/datasources/sources?notebook_id=${notebookId}`, { withCredentials: true }),
        axios.get(`${API_BASE}/api/notebooks/${notebookId}/dashboards`, { withCredentials: true }),
        axios.get(`${API_BASE}/api/documents?notebook_id=${notebookId}`, { withCredentials: true }),
      ])

      const liveSources: Record<string, string> =
        dbRes.status === 'fulfilled' ? (dbRes.value.data?.sources ?? {}) : {}
      const dbSources: NotebookSource[] = Object.entries(liveSources).map(([alias, db_type]) => ({
        id: alias, alias, type: 'database' as SourceType, db_type: db_type as string,
        label: alias, connected_at: new Date().toISOString(), notebook_id: notebookId,
      }))

      const dashboards: any[] = dashRes.status === 'fulfilled' ? (dashRes.value.data ?? []) : []
      const dashSources: NotebookSource[] = dashboards.map((d: any) => ({
        id: String(d.id), alias: d.name, type: 'dashboard' as SourceType,
        db_type: d.type, label: d.name,
        connected_at: d.created_at ?? new Date().toISOString(),
        notebook_id: notebookId, config: d.config,
      }))

      const documents: any[] = docsRes.status === 'fulfilled' ? (docsRes.value.data?.documents ?? []) : []
      const docSources: NotebookSource[] = documents.map((doc: any) => {
        const isWebsite    = doc.tags?.source_type === 'website' || doc.filename?.endsWith('.website.txt')
        const isSharePoint = doc.tags?.source_type === 'sharepoint'
        const rawAlias     = doc.filename as string
        const cleanAlias   = isWebsite
          ? rawAlias.replace(/\.website\.txt$/, '').replace(/_/g, ' ')
          : rawAlias
        return {
          id: doc.doc_id, alias: cleanAlias, type: 'document' as SourceType,
          db_type: isSharePoint ? 'sharepoint' : isWebsite ? 'website' : doc.file_extension,
          label: cleanAlias,
          connected_at: doc.upload_timestamp ?? new Date().toISOString(),
          notebook_id: notebookId, config: { description: doc.description, category: doc.category },
        }
      })

      setSources([...dbSources, ...dashSources, ...docSources])

      // Default the preview panel to the dashboard on first load of this
      // notebook (only once per notebook — later refreshes, e.g. after
      // connecting a new source, shouldn't yank the user off a tab they've
      // already picked). Dashboard-related chat queries often rely on the
      // embedded report's own JS SDK (exportData, etc.), which only works
      // while the dashboard is actually mounted — so it takes priority over
      // the database table view as the starting tab.
      if (defaultTabAppliedRef.current !== notebookId) {
        defaultTabAppliedRef.current = notebookId
        if (dashSources.length > 0) setPreviewTab('dashboard')
      }
    } catch {
      // silently fail
    } finally {
      setSourcesLoading(false)
    }
  }, [notebookId])

  useEffect(() => { refreshSources() }, [refreshSources])

  /* ── Chat sessions ── */
  const loadChats = useCallback(async () => {
    if (!notebookId) return
    setChatsLoading(true)
    try {
      const r = await axios.get(`${API_BASE}/api/notebooks/${notebookId}/chats`, { withCredentials: true })
      setChats(r.data)
    } catch { setChats([]) }
    finally { setChatsLoading(false) }
  }, [notebookId])

  useEffect(() => { loadChats() }, [loadChats])

  const createChat = useCallback(async (title?: string, persona?: string): Promise<NotebookChat> => {
    const r = await axios.post(
      `${API_BASE}/api/notebooks/${notebookId}/chats`,
      { title: title || 'New Chat', persona },
      { withCredentials: true }
    )
    const newChat: NotebookChat = r.data
    setChats(prev => [newChat, ...prev])
    return newChat
  }, [notebookId])

  // Bug 11: clear messages when deleting the active chat
  const deleteChat = useCallback(async (chatId: string, isActive?: boolean) => {
    await axios.delete(`${API_BASE}/api/notebooks/${notebookId}/chats/${chatId}`, { withCredentials: true })
    setChats(prev => prev.filter(c => c.id !== chatId))
    if (isActive || activeChatIdRef.current === chatId) {
      setMessages([])
      setActiveChatId(null)
    }
  }, [notebookId])

  const renameChat = useCallback(async (chatId: string, title: string) => {
    await axios.patch(`${API_BASE}/api/chats/${chatId}`, { title }, { withCredentials: true })
    setChats(prev => prev.map(c => c.id === chatId ? { ...c, title } : c))
  }, [])

  /* ── Messages ── */

  // Bug 2: include sourceFilter when restoring from server
  // Bug 6: guard setMessages with activeChatIdRef so stale async fetches can't overwrite current chat
  const loadMessages = useCallback(async (chatId: string) => {
    try {
      const r = await axios.get(`${API_BASE}/api/chats/${chatId}/messages`, { withCredentials: true })
      const raw: any[] = r.data.messages ?? []
      const mapped: ChatMessage[] = raw
        .filter(m => m.role === 'assistant')
        .map(m => ({
          id:                  m.id,
          question:            m.question,
          summary:             m.summary,
          sql_query:           m.sql_query,
          chart_data:          m.chart_data,
          chart_type:          m.chart_type,
          columns:             m.columns,
          data:                m.data,
          error:               m.error,
          follow_up_questions: m.follow_up_questions,
          out_of_scope:        m.out_of_scope,
          loading:             false,
          sourceFilter:        m.source_filter,  // Bug 2 fix
          referenced_documents: m.referenced_documents,
        }))
      if (activeChatIdRef.current === chatId) {  // Bug 6 fix
        setMessages(mapped)
      }
    } catch {
      if (activeChatIdRef.current === chatId) {
        setMessages([])
      }
    }
  }, [])

  const addMessage    = useCallback((msg: ChatMessage) => setMessages(prev => [...prev, msg]), [])
  const updateMessage = useCallback((id: string, patch: Partial<ChatMessage>) =>
    setMessages(prev => prev.map(m => m.id === id ? { ...m, ...patch } : m)), [])
  const clearMessages = useCallback(() => setMessages([]), [])

  // Bug 1: persist dashboard messages to server
  // Bug 9: send raw objects — user_store.save_message serializes JSON fields itself
  const persistMessage = useCallback(async (chatId: string, msg: ChatMessage) => {
    if (!chatId || msg.loading) return
    try {
      await axios.post(
        `${API_BASE}/api/chats/${chatId}/messages`,
        {
          id:                  msg.id,
          role:                'assistant',
          question:            msg.question,
          summary:             msg.summary,
          sql_query:           msg.sql_query,
          chart_data:          msg.chart_data,
          chart_type:          msg.chart_type,
          columns:             msg.columns,
          data_json:           msg.data,
          error:               msg.error,
          follow_up_questions: msg.follow_up_questions,
          out_of_scope:        msg.out_of_scope,
          source_filter:       msg.sourceFilter ?? 'dashboard',
          referenced_documents: msg.referenced_documents,
        },
        { withCredentials: true }
      )
    } catch {
      // Non-fatal — message lives in React state for this session
    }
  }, [])

  /* ── Preview ── */
  const setPreviewData = useCallback((data: any[] | null, columns: string[] | null) => {
    setPreviewDataState(data); setPreviewColumns(columns)
  }, [])

  /* ── Notebook actions ── */
  const renameNotebook = useCallback(async (name: string) => {
    try {
      const r = await axios.patch(`${API_BASE}/api/notebooks/${notebookId}`, { name }, { withCredentials: true })
      setNotebook(r.data)
    } catch { console.error('Failed to rename notebook') }
  }, [notebookId])

  const updateConfig = useCallback(async (config: {
    name?: string; persona?: string; instructions?: string; description?: string
  }) => {
    try {
      const r = await axios.patch(`${API_BASE}/api/notebooks/${notebookId}`, config, { withCredentials: true })
      setNotebook(r.data)
    } catch { console.error('Failed to update project config') }
  }, [notebookId])

  const value: NotebookCtx = {
    notebook, loading, error,
    sources, sourcesLoading, refreshSources,
    chats, chatsLoading, loadChats, createChat, deleteChat, renameChat,
    activeChatId, setActiveChatId,
    messages, addMessage, updateMessage, clearMessages, loadMessages, persistMessage,
    previewTab, setPreviewTab,
    previewData, previewColumns, setPreviewData,
    renameNotebook, updateConfig,
    activeDashboardPage,    setActiveDashboardPage,
    dashboardActiveFilters, setDashboardActiveFilters,
    dashboardPageVisualData, setDashboardPageVisualData,
    dashboardVisualInventory, setDashboardVisualInventory,
    getDashboardContext,
    setDashboardContextReady, waitForDashboardReady,
    sourceFilter, setSourceFilter, isSourceFilterActive, toggleSourceFilterKey,
    isSourceEnabled, toggleSourceEnabled, disabledSourceIds,
    getDisplayAlias, renameSource,
  }

  return (
    <NotebookContext.Provider value={value}>
      {children}
    </NotebookContext.Provider>
  )
}

export default NotebookContext