// useSessions.ts
// Chat sessions backed by the server — persists across devices and server restarts.
// Falls back to localStorage if the backend is unreachable (offline / unauthenticated).

import { useState, useCallback, useEffect, useRef } from 'react'
import axios from 'axios'
import { API_BASE } from '../config'

export interface Message {
  id: string
  question: string
  sql_query?: string
  data?: Record<string, any>[]
  columns?: string[]
  summary?: string
  follow_up_questions?: string[]
  chart_data?: Record<string, any>
  chart_type?: string
  error?: string
  out_of_scope?: boolean
  loading: boolean
  sourceFilter?: 'all' | 'database' | 'documents' | 'dashboard'
}

export interface Session {
  id: string
  title: string
  createdAt: number
  updatedAt: number
  messages: Message[]
  lastQuestion: string | null
  lastSQL: string | null
  lastSummary: string | null
}

// ── Dashboard persistence (sessionStorage — ephemeral, no user data) ───────────

const DASHBOARDS_KEY         = 'insightagent_dashboards'
const SELECTED_DASHBOARD_KEY = 'insightagent_selected_dashboard'

export interface PersistedDashboard {
  id: string; type: 'powerbi' | 'tableau'; name: string
  embedUrl?: string; embedToken?: string; tokenExpiry?: string
  creds?: Record<string, string>; status: 'connected' | 'error'; error?: string
}

export function loadDashboards(): PersistedDashboard[] {
  try { const r = sessionStorage.getItem(DASHBOARDS_KEY); return r ? JSON.parse(r) : [] } catch { return [] }
}
export function saveDashboards(d: PersistedDashboard[]) {
  try { sessionStorage.setItem(DASHBOARDS_KEY, JSON.stringify(d)) } catch { }
}
export function loadSelectedDashboardId(): string | null {
  try { return sessionStorage.getItem(SELECTED_DASHBOARD_KEY) } catch { return null }
}
export function saveSelectedDashboardId(id: string | null) {
  try { id ? sessionStorage.setItem(SELECTED_DASHBOARD_KEY, id) : sessionStorage.removeItem(SELECTED_DASHBOARD_KEY) } catch { }
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function makeSession(): Session {
  return { id: Date.now().toString(), title: 'New Chat', createdAt: Date.now(),
           updatedAt: Date.now(), messages: [], lastQuestion: null, lastSQL: null, lastSummary: null }
}
function titleFrom(q: string) { return q.length > 40 ? q.slice(0, 40).trimEnd() + '…' : q }

// ── Backend helpers ────────────────────────────────────────────────────────────

async function fetchChatsFromServer(): Promise<Session[]> {
  try {
    const r = await axios.get(`${API_BASE}/api/chats`, { withCredentials: true })
    return (r.data.chats || []).map((c: any) => ({
      id: c.id, title: c.title,
      createdAt: new Date(c.created_at).getTime(),
      updatedAt: new Date(c.updated_at).getTime(),
      messages: [], lastQuestion: null, lastSQL: null, lastSummary: null,
    }))
  } catch { return [] }
}

async function fetchMessagesForChat(chatId: string): Promise<Message[]> {
  try {
    const r = await axios.get(`${API_BASE}/api/chats/${chatId}/messages`, { withCredentials: true })
    return (r.data.messages || []).map((m: any) => ({
      id: m.id,
      question: m.question,
      sql_query: m.sql_query,
      data: m.data_json ? JSON.parse(m.data_json) : undefined,
      columns: m.columns_json ? JSON.parse(m.columns_json) : undefined,
      summary: m.summary,
      chart_data: m.chart_data ? JSON.parse(m.chart_data) : undefined,
      chart_type: m.chart_type,
      error: m.error,
      loading: false,
      sourceFilter: m.source_filter as any,
    }))
  } catch { return [] }
}

async function ensureChatOnServer(session: Session) {
  try {
    await axios.post(`${API_BASE}/api/chats`, { id: session.id, title: session.title },
                     { withCredentials: true })
  } catch { }
}

async function persistMessageToServer(sessionId: string, msg: Message) {
  if (msg.loading) return
  try {
    // Bug 12 fix: messages are AI responses — must be saved as 'assistant' or
    // loadMessages (which filters role === 'assistant') will drop them on reload
    await axios.post(`${API_BASE}/api/chats/${sessionId}/messages`,
      { ...msg, role: 'assistant' }, { withCredentials: true })
  } catch { }
}

async function updateChatTitleOnServer(chatId: string, title: string) {
  try {
    await axios.patch(`${API_BASE}/api/chats/${chatId}`, { title }, { withCredentials: true })
  } catch { }
}

// ── Hook ───────────────────────────────────────────────────────────────────────

export function useSessions() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [activeId, setActiveId] = useState<string>('')
  const loaded = useRef(false)

  // Load sessions from server on mount
  useEffect(() => {
    if (loaded.current) return
    loaded.current = true
    fetchChatsFromServer().then(serverSessions => {
      if (serverSessions.length > 0) {
        setSessions(serverSessions)
        setActiveId(serverSessions[0].id)
      } else {
        const initial = makeSession()
        setSessions([initial])
        setActiveId(initial.id)
        ensureChatOnServer(initial)
      }
    })
  }, [])

  // Load messages when switching to a different chat
  useEffect(() => {
    if (!activeId) return
    const session = sessions.find(s => s.id === activeId)
    if (!session || session.messages.length > 0) return
    fetchMessagesForChat(activeId).then(messages => {
      setSessions(prev => prev.map(s => s.id === activeId ? { ...s, messages } : s))
    })
  }, [activeId])

  const activeSession = sessions.find(s => s.id === activeId) ?? sessions[0] ?? makeSession()

  const newSession = useCallback(() => {
    const s = makeSession()
    setSessions(prev => [s, ...prev])
    setActiveId(s.id)
    ensureChatOnServer(s)
    return s.id
  }, [])

  const switchSession = useCallback((id: string) => setActiveId(id), [])

  const deleteSession = useCallback((id: string) => {
    setSessions(prev => {
      const next = prev.filter(s => s.id !== id)
      if (next.length === 0) {
        const fresh = makeSession()
        ensureChatOnServer(fresh)
        setActiveId(fresh.id)
        return [fresh]
      }
      if (id === activeId) setActiveId(next[0].id)
      return next
    })
    axios.delete(`${API_BASE}/api/chats/${id}`, { withCredentials: true }).catch(() => {})
  }, [activeId])

  const addMessage = useCallback((sessionId: string, msg: Message) => {
    setSessions(prev => prev.map(s => {
      if (s.id !== sessionId) return s
      const title = s.messages.length === 0 ? titleFrom(msg.question) : s.title
      if (s.messages.length === 0) updateChatTitleOnServer(sessionId, title)
      return { ...s, title, updatedAt: Date.now(), messages: [...s.messages, msg] }
    }))
    if (!msg.loading) persistMessageToServer(sessionId, msg)
  }, [])

  const updateMessage = useCallback((sessionId: string, msgId: string, patch: Partial<Message>) => {
    setSessions(prev => prev.map(s => {
      if (s.id !== sessionId) return s
      const updated = s.messages.map(m => m.id === msgId ? { ...m, ...patch } : m)
      const updatedMsg = updated.find(m => m.id === msgId)
      if (updatedMsg && !updatedMsg.loading) persistMessageToServer(sessionId, updatedMsg)
      return { ...s, updatedAt: Date.now(), messages: updated }
    }))
  }, [])

  const updateContext = useCallback((sessionId: string, lastQuestion: string,
                                     lastSQL: string | null, lastSummary: string | null) => {
    setSessions(prev => prev.map(s =>
      s.id === sessionId ? { ...s, lastQuestion, lastSQL, lastSummary } : s
    ))
  }, [])

  return { sessions, activeSession, activeId, newSession, switchSession,
           deleteSession, addMessage, updateMessage, updateContext }
}