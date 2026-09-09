import React, { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import axios from 'axios'
import { Link2, Loader2, AlertCircle, ArrowLeft, User } from 'lucide-react'
import { API_BASE } from './config'
import ChatMessage from './components/ChatMessage'

interface SharedChat {
  token: string
  chat_id: string
  notebook_id: string
  chat_title: string | null
  notebook_name: string | null
  persona: string | null
  owner_name: string | null
  created_at: string
  messages: any[]
}

/**
 * Public-within-the-app viewer for a shared chat link.
 *
 * Still requires an authenticated SSO session (same as everywhere else in
 * the app), but deliberately doesn't reuse the notebook layout — no source
 * panel, no chat list, no input box, no way to navigate into the sharer's
 * other chats or notebooks. This page only ever knows about the one frozen
 * snapshot behind the token in the URL.
 */
const SharedChatPage: React.FC = () => {
  const { token } = useParams<{ token: string }>()
  const navigate = useNavigate()
  const [chat, setChat]       = useState<SharedChat | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)

  useEffect(() => {
    if (!token) return
    setLoading(true)
    setError(null)
    axios.get(`${API_BASE}/api/share/${token}`, { withCredentials: true })
      .then(r => setChat(r.data))
      .catch(e => setError(e.response?.data?.detail || 'This share link is invalid or has been revoked'))
      .finally(() => setLoading(false))
  }, [token])

  const messages = (chat?.messages ?? [])
    .filter((m: any) => m.role === 'assistant')
    .map((m: any) => ({
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
      sourceFilter:        m.source_filter,
    }))

  return (
    <div className="flex flex-col h-screen bg-surface">

      {/* Header */}
      <div className="flex items-center gap-3 px-6 py-4 bg-[#2D5A8C] shrink-0">
        <button
          onClick={() => navigate('/home')}
          className="p-1.5 rounded-lg text-white/70 hover:text-white hover:bg-white/10 transition-colors"
          title="Back to your notebooks"
        >
          <ArrowLeft size={16} />
        </button>
        <div className="w-8 h-8 rounded-xl bg-white/10 flex items-center justify-center shrink-0">
          <Link2 size={15} className="text-white" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-white truncate">
            {chat?.chat_title || 'Shared chat'}
          </p>
          {chat?.owner_name && (
            <p className="text-xs text-white/60 flex items-center gap-1">
              <User size={10} /> Shared by {chat.owner_name}
              {chat.notebook_name ? ` · ${chat.notebook_name}` : ''}
            </p>
          )}
        </div>
        <span className="text-[10px] font-medium text-white/70 bg-white/10 px-2 py-1 rounded-full shrink-0">
          Read-only
        </span>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="max-w-3xl mx-auto">
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-16 text-ink-faint">
              <Loader2 size={16} className="animate-spin" />
              <span className="text-sm">Loading shared chat…</span>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center gap-3 py-16 text-center">
              <div className="w-12 h-12 rounded-full bg-tertiary-muted flex items-center justify-center">
                <AlertCircle size={20} className="text-tertiary" />
              </div>
              <div>
                <p className="text-sm font-semibold text-ink">Can't open this link</p>
                <p className="text-xs text-ink-muted mt-1 max-w-sm">{error}</p>
              </div>
              <button
                onClick={() => navigate('/home')}
                className="mt-2 text-xs font-medium text-[#2D5A8C] hover:underline"
              >
                Go to your notebooks
              </button>
            </div>
          ) : messages.length === 0 ? (
            <p className="text-sm text-ink-faint text-center py-16">This chat has no messages yet.</p>
          ) : (
            <div className="flex flex-col gap-2">
              {messages.map(m => (
                <ChatMessage key={m.id} message={m} readOnly />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default SharedChatPage