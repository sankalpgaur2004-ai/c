import React, { useState, useRef, useEffect, useCallback } from 'react'
import axios from 'axios'
import { Sparkles, RotateCcw, Loader2, Share2 } from 'lucide-react'
import { cn } from './lib/utils'
import { API_BASE } from './config'
import { useNotebook } from './NotebookContext'
import ChatMessage from './components/ChatMessage'
import ChatInput from './components/ChatInput'
import type { SourceFilter } from './components/ChatInput'
import { sendChat, analyzePageVisuals } from './powerbiApi'
import ShareChatModal from './ShareChatModal'

const STARTER_PROMPTS = [
  'Summarize this page',
  'Show me the top 10 records by volume',
  'What are the most recent entries?',
  'Give me a count breakdown by category',
]

interface Props {
  chatId?: string
}

const PERSONAS = [
  { value: 'executive',     label: 'Executive' },
  { value: 'analyst',       label: 'Analyst' },
  { value: 'sales_manager', label: 'Sales Manager' },
  { value: 'field_rep',     label: 'Field Rep' },
]

// ── Detect descriptive / page-level questions ─────────────────────────────────
const DESCRIPTIVE_PATTERNS = [
  /\bsummar(ize|y|ise)\b/i,
  /\boverview\b/i,
  /\bexplain\b/i,
  /\bdescribe\b/i,
  /\bwhat (does|is on|is shown|do you see)\b/i,
  /\bhow (is|are) (the|this|our)\b/i,
  /\btell me about (this|the) (page|dashboard|report)\b/i,
  /\bwhat.{0,20}(page|dashboard|report) show\b/i,
  /\bwhat.{0,20}(kpi|metric|visual)/i,
  /\btrend(s)?\b/i,
  /\bbreakdown\b/i,
  /\bhow (are|is) (we|things)\b/i,
  /\bperforming\b/i,
  /\bwhat('s| is) happening\b/i,
  /\bgive me a (summary|overview|snapshot|brief)\b/i,
  /\bwhat about (this|the)\b/i,
  /\babout this (page|dashboard|report)\b/i,
  /\bthis page\b/i,
  /\bwhat can you (tell|say)\b/i,
  /\bwhat.{0,10}going on\b/i,
  /\bsnap(shot)?\b/i,
  /\bwhat.{0,15}happening\b/i,
]

function isDescriptiveQuestion(question: string): boolean {
  return DESCRIPTIVE_PATTERNS.some(p => p.test(question))
}

const ChatPanel: React.FC<Props> = ({ chatId }) => {
  const {
    notebook, sources, sourcesLoading,
    messages, addMessage, updateMessage, clearMessages, loadMessages, persistMessage,
    setActiveChatId,
    previewTab, setPreviewData, setPreviewTab,
    chats, renameChat,
    activeDashboardPage,
    getDashboardContext,
    waitForDashboardReady,
    sourceFilter, setSourceFilter, isSourceFilterActive,
    isSourceEnabled,
  } = useNotebook()

  const currentChat = chatId ? chats.find(c => c.id === chatId) : null

  const [loading, setLoading]           = useState(false)
  const [shareOpen, setShareOpen]       = useState(false)
  const bottomRef    = useRef<HTMLDivElement>(null)
  const hasTitledRef = useRef(false)

  // Bug 10 fix: keep a ref to messages so getConversationHistory never has a stale closure
  const messagesRef = useRef(messages)
  useEffect(() => { messagesRef.current = messages }, [messages])

  // Build conversation history always from the latest messages via ref
  const getConversationHistory = useCallback(() => {
    return messagesRef.current
      .filter((m: any) => !m.loading && (m.summary || m.sql_query))
      .slice(-10)
      .map((m: any) => ({
        question:     m.question,
        sql_query:    m.sql_query ?? null,
        summary:      m.summary ?? null,
        insight:      m.summary ?? null,
        sourceFilter: m.sourceFilter ?? 'all',
      }))
  }, []) // no messages dep needed — reads from ref

  /* ── Auto scroll ── */
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  /* ── Auto-title ── */
  const maybeAutoTitle = useCallback((question: string) => {
    if (!chatId || hasTitledRef.current) return
    const chat = chats.find(c => c.id === chatId)
    if (!chat || chat.title !== 'New Chat') return
    hasTitledRef.current = true
    const title = question.length > 60 ? question.slice(0, 57) + '…' : question
    renameChat(chatId, title).catch(() => {})
  }, [chatId, chats, renameChat])

  /* ── Core send ── */
  const sendQuestion = useCallback(async (question: string, filter?: SourceFilter) => {
    if (loading) return
    const activeFilter = filter ?? sourceFilter
    const msgId = `msg-${Date.now()}-${Math.random().toString(36).slice(2)}`

    maybeAutoTitle(question)
    addMessage({ id: msgId, question, loading: true, sourceFilter: activeFilter } as any)
    setLoading(true)

    // If this question actually needs the embedded report's own JS SDK
    // (exportData, etc. — the descriptive "summarize this page" path), make
    // sure the dashboard is open and captured BEFORE sending anything, since
    // that data only exists while the report is mounted. A specific/lookup
    // question ("who is X", "how many Y") never touches that path — it's
    // answered server-side via the Power BI REST API and DAX, with no need
    // for the report to be visually open at all — so it must NOT force a
    // tab switch; whatever preview the user has open should just stay put.
    const dashboardConnected = sources.some(s => s.type === 'dashboard' && isSourceEnabled(s.id))
    const questionNeedsLiveDashboard = dashboardConnected && isDescriptiveQuestion(question)
    if (questionNeedsLiveDashboard) {
      if (previewTab !== 'dashboard') setPreviewTab('dashboard')
      await waitForDashboardReady()
    }

    /* ── Dashboard path ── */
    if (activeFilter === 'dashboard') {
      // Use the dashboard the user actually has checked in Sources, not just
      // the first dashboard in the list — otherwise this silently queries
      // the wrong dashboard whenever more than one is connected.
      const dashSrc = sources.find(s => s.type === 'dashboard' && isSourceEnabled(s.id))
        ?? sources.find(s => s.type === 'dashboard')
      if (!dashSrc?.id) {
        updateMessage(msgId, { loading: false, error: 'No dashboard connected.' } as any)
        setLoading(false)
        return
      }

      const dashId   = Number(dashSrc.id)
      const history  = getConversationHistory()
      const { page: dashPage, filters: filters, visualData: visData, visualInventory } = getDashboardContext()
      const pageName = dashPage?.displayName ?? undefined

      try {
        // Descriptive question with visual data → analyze-page endpoint
        if (isDescriptiveQuestion(question) && visData.length > 0) {
          const result = await analyzePageVisuals(dashId, question, pageName ?? null, visData, filters, history, visualInventory)

          // Bug 3 fix: always include sourceFilter in patch so it's stored on the message
          const patch = result.status === 'error'
            ? { loading: false, error: result.message || 'Could not summarize page.', sourceFilter: 'dashboard' as const }
            : { loading: false, summary: result.insight ?? 'Done.', sourceFilter: 'dashboard' as const }

          updateMessage(msgId, patch as any)

          // Bug 1 fix (previous session): persist dashboard messages to server
          if (chatId && result.status !== 'error') {
            persistMessage(chatId, { id: msgId, question, ...patch } as any)
          }

        } else {
          // Specific metric, meta question, or descriptive without visual data → chat endpoint
          const result = await sendChat(dashId, question, history, pageName, filters, visData, visualInventory)
          const isErr  = result.status === 'error' || result.query_type === 'unsupported'

          // Bug 3 fix: always include sourceFilter in patch
          const patch = isErr
            ? { loading: false, error: result.message || 'Dashboard could not answer this.', sourceFilter: 'dashboard' as const }
            : { loading: false, summary: result.insight ?? 'Done.', sql_query: result.dax_query ?? undefined, sourceFilter: 'dashboard' as const }

          updateMessage(msgId, patch as any)

          // Bug 1 fix: persist successful dashboard messages
          if (chatId && !isErr) {
            persistMessage(chatId, { id: msgId, question, ...patch } as any)
          }
        }
      } catch (err: any) {
        updateMessage(msgId, {
          loading: false,
          error:   err.response?.data?.detail || err.message || 'Dashboard query failed.',
          sourceFilter: 'dashboard',
        } as any)
      } finally {
        setLoading(false)
      }
      return
    }

    /* ── DB / Docs / All path ── */
    try {
      const { page: dashPage, filters: dashFilters, visualData: dashVisualData } = getDashboardContext()

      // Recompute exclusions fresh from the live `sources` list + per-source
      // `isSourceEnabled` check, right here at send time, instead of trusting
      // `disabledSourceIds` — that value can lag behind the checkbox state
      // (e.g. sources fetched/added after initial mount, or a source whose
      // enabled state changed but didn't make it into that derived value in
      // time), which silently sends an incomplete exclusion list and lets
      // dashboards/sources the user unchecked still get queried. Every id is
      // normalized to a string so it always matches the backend's
      // `str(d.get('id')) not in excluded_ids` check — a number/string id
      // mismatch would otherwise cause a source to silently fail to exclude.
      const excludedSourceIds = sources
        .filter(s => !isSourceEnabled(s.id))
        .map(s => String(s.id))

      const r = await axios.post(
        `${API_BASE}/api/process-question`,
        {
          question,
          source_filter:        activeFilter,
          excluded_source_ids:  excludedSourceIds,
          notebook_id:          notebook?.id,
          chat_id:              chatId ?? null,
          conversation_history: getConversationHistory(),
          // Dashboard page context (for when dashboard is included in source_filter)
          page_name:            dashPage?.displayName ?? null,
          active_filters:       dashFilters ?? [],
          page_visual_data:     dashVisualData ?? [],
        },
        { withCredentials: true },
      )

      const res = r.data
      const isOutOfScope = !res.success && !res.error && res.summary

      updateMessage(msgId, {
        loading:             false,
        sql_query:           res.sql_query,
        data:                res.data,
        columns:             res.columns,
        summary:             res.summary,
        follow_up_questions: res.follow_up_questions,
        chart_data:          res.chart_data,
        chart_type:          res.chart_type,
        out_of_scope:        isOutOfScope || undefined,
        error:               res.success || isOutOfScope ? undefined : (res.error || 'Unknown error'),
        referenced_documents: res.referenced_documents,
      } as any)

      if (res.success) {
        const answeredBy: string = res.answered_by || ''
        // Preview tab is intentionally left untouched here — whatever tab
        // the user has open stays open no matter which source answered the
        // question. We still refresh the underlying table data so it's
        // up to date whenever the user switches to that tab themselves.
        if (answeredBy.includes('database') && res.data?.length) {
          setPreviewData(res.data, res.columns ?? [])
        }
      }
    } catch (err: any) {
      updateMessage(msgId, {
        loading: false,
        error:   err.response?.data?.detail || err.message || 'Request failed',
      } as any)
    } finally {
      setLoading(false)
    }
  }, [
    loading, sourceFilter, sources, notebook, chatId, isSourceEnabled,
    addMessage, updateMessage, setPreviewData, setPreviewTab, previewTab,
    maybeAutoTitle, getConversationHistory, persistMessage,
    getDashboardContext, waitForDashboardReady, isSourceFilterActive,
  ])
  // Note: `messages` intentionally removed from deps — we use messagesRef (Bug 10 fix)

  /* ── On mount / chatId change ──
     Bug 5 fix: removed initializedRef guard — it was never reset on chat switch,
     so switching chats never triggered loadMessages after the first load.

     pendingChatQuery fix: sourcesLoading is added as a dep so the effect re-runs
     once sources finish loading and can consume the queued question from the
     project detail page. A ref (pendingFiredRef) prevents the query firing twice
     or loadMessages running again on a chat that was already loaded normally.
  ── */
  const pendingFiredRef = useRef(false)

  useEffect(() => {
    if (!chatId || !notebook) return

    // Tell context which chat is now active (Bug 6 fix: guards stale async writes)
    setActiveChatId(chatId)

    const raw = sessionStorage.getItem('pendingChatQuery')

    // If this is a fresh chatId mount (not a sourcesLoading re-run), reset state
    // We detect this by checking pendingFiredRef — if we haven't fired yet for this
    // chatId, treat it as a fresh mount.
    if (!pendingFiredRef.current) {
      clearMessages()
      hasTitledRef.current = false
    }

    if (raw) {
      // Wait for sources to finish loading before firing the question
      if (sourcesLoading) return

      // Sources are ready — consume and fire the query exactly once
      if (!pendingFiredRef.current) {
        pendingFiredRef.current = true
        sessionStorage.removeItem('pendingChatQuery')
        try {
          const { question, filter } = JSON.parse(raw)
          setSourceFilter(filter ?? 'all')
          setTimeout(() => sendQuestion(question, filter ?? 'all'), 0)
        } catch {
          loadMessages(chatId)
        }
      }
    } else {
      // No pending query — just load existing messages (only on fresh mount)
      if (!pendingFiredRef.current) {
        loadMessages(chatId)
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatId, notebook?.id, sourcesLoading])

  // Reset pendingFiredRef whenever chatId changes so the next chat starts fresh
  useEffect(() => {
    pendingFiredRef.current = false
  }, [chatId])

  const clearChat = () => {
    clearMessages()
    hasTitledRef.current = false
  }

  const hasSources = sources.length > 0

  return (
    <div className="flex flex-col h-full overflow-hidden bg-white">

      {/* Header */}
      <div className={cn('flex items-center justify-between px-5 py-3 shrink-0 border-b border-border bg-white')}>
        <div className="flex items-center gap-2">
          <Sparkles size={15} className="text-[#2D5A8C]" />
          <span className="text-sm font-semibold text-[#2D5A8C]">Chat</span>
          {currentChat?.persona && (
            <span className="badge badge-sm badge-navy">
              {PERSONAS.find(p => p.value === currentChat.persona)?.label ?? currentChat.persona}
            </span>
          )}
          {sourceFilter === 'dashboard' && activeDashboardPage && (
            <span className="badge badge-sm badge-navy opacity-70">
              {activeDashboardPage.displayName}
            </span>
          )}
          {messages.length > 0 && (
            <span className="badge badge-navy ml-1">
              {messages.length} message{messages.length !== 1 ? 's' : ''}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          {messages.length > 0 && chatId && (
            <button onClick={() => setShareOpen(true)}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs text-ink-faint hover:text-[#2D5A8C] hover:bg-[#2D5A8C]-muted transition-colors">
              <Share2 size={12} /> Share
            </button>
          )}
          {messages.length > 0 && (
            <button onClick={clearChat}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs text-ink-faint hover:text-tertiary hover:bg-tertiary-muted transition-colors">
              <RotateCcw size={12} /> Clear
            </button>
          )}
        </div>
      </div>

      {shareOpen && chatId && notebook && (
        <ShareChatModal
          notebookId={notebook.id}
          chatId={chatId}
          chatTitle={currentChat?.title ?? 'Chat'}
          onClose={() => setShareOpen(false)}
        />
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-2 scrollbar-none bg-white">
        {messages.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center h-full gap-6 animate-fade-in">
            <div className="text-center">
              <div className="w-12 h-12 rounded-2xl bg-[#2D5A8C]-muted flex items-center justify-center mx-auto mb-3">
                <Sparkles size={22} className="text-[#2D5A8C]" />
              </div>
              <h2 className="text-base font-semibold text-ink">
                {hasSources ? 'Ask anything about your data' : 'No sources connected'}
              </h2>
              <p className="text-sm text-ink-muted mt-1 max-w-sm text-balance">
                {hasSources
                  ? 'Ask specific questions or summarize the current dashboard page.'
                  : 'Add a database, document, or dashboard from the Sources panel to get started.'}
              </p>
            </div>
            {hasSources && (
              <div className="grid grid-cols-2 gap-2 w-full max-w-lg">
                {STARTER_PROMPTS.map(prompt => (
                  <button key={prompt} onClick={() => sendQuestion(prompt)} disabled={loading}
                    className={cn(
                      'text-left px-4 py-3 rounded-xl text-sm text-ink border border-border bg-white',
                      'hover:border-[#2D5A8C] hover:bg-[#2D5A8C]-muted hover:text-[#2D5A8C]',
                      'transition-all duration-150 disabled:opacity-50 disabled:cursor-not-allowed',
                    )}>
                    {prompt}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          messages.map(msg => (
            <ChatMessage key={msg.id} message={msg as any} onFollowUp={(q: string) => sendQuestion(q)} />
          ))
        )}

        {loading && (
          <div className="flex items-center gap-3 py-3 animate-fade-in">
            <div className="w-7 h-7 rounded-full bg-[#2D5A8C] flex items-center justify-center shrink-0">
              <Loader2 size={14} className="text-white animate-spin" />
            </div>
            <div className="flex gap-1 items-center">
              {[0, 150, 300].map(d => (
                <span key={d} className="w-2 h-2 rounded-full bg-[#2D5A8C]/40 animate-pulse"
                  style={{ animationDelay: `${d}ms` }} />
              ))}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="shrink-0 border-t border-border">
        <ChatInput
          onSubmit={sendQuestion}
          loading={loading}
          sourceFilter={sourceFilter}
          placeholder={hasSources ? 'Ask a question about your data…' : 'Add a source first to start chatting…'}
        />
      </div>
    </div>
  )
}

export default ChatPanel