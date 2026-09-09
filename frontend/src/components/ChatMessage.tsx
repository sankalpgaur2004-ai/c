import React, { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ChevronDown, ChevronUp, AlertCircle, Info, FileText } from 'lucide-react'
import DataTable from './DataTable'
import DynamicChart from './DynamicChart'
import { cn } from '../lib/utils'

interface ReferencedDocument {
  doc_id: string
  filename: string
  category?: string
  page?: number
  relevance_score: number
  excerpt: string
}

interface MessageData {
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
  loading?: boolean
  sourceFilter?: 'all' | 'database' | 'documents' | 'dashboard'
  referenced_documents?: ReferencedDocument[]
}

interface Props { message: MessageData; onFollowUp?: (question: string) => void; readOnly?: boolean }

const ChatMessage: React.FC<Props> = ({ message, onFollowUp, readOnly = false }) => {
  const [showSQL, setShowSQL] = useState(false)

  const loadingText = {
    database:  'Generating SQL and analysing data…',
    documents: 'Searching through documents…',
    dashboard: 'Loading dashboard data…',
    all:       'Generating SQL and searching documents…',
  }[message.sourceFilter ?? 'all'] ?? 'Analysing your data…'

  return (
    <div className="mb-6 animate-fade-in">

      {/* ── User question ── */}
      <div className="flex justify-end mb-3">
        <div className={cn(
          'max-w-[70%] px-4 py-2.5 rounded-2xl rounded-tr-sm',
          'bg-[#2D5A8C] text-white text-sm leading-relaxed shadow-sm',
        )}>
          {message.question}
        </div>
      </div>

      {/* ── AI response ── */}
      <div className={cn(
        'rounded-2xl border border-border bg-white px-5 py-4',
        'shadow-card',
      )}>

        {/* Loading */}
        {message.loading && (
          <div className="flex items-center gap-3 text-sm text-ink-muted">
            <div className="w-4 h-4 border-2 border-[#2D5A8C]/30 border-t-secondary rounded-full animate-spin shrink-0" />
            {loadingText}
          </div>
        )}

        {/* Error */}
        {message.error && !message.loading && !message.out_of_scope && (
          <div className={cn(
            'flex items-start gap-3 p-3 rounded-xl',
            'bg-tertiary-muted border border-tertiary/20',
          )}>
            <AlertCircle size={15} className="text-tertiary shrink-0 mt-0.5" />
            <p className="text-sm text-tertiary leading-snug">{message.error}</p>
          </div>
        )}

        {/* Out of scope */}
        {message.out_of_scope && !message.loading && (
          <div className={cn(
            'flex items-start gap-3 p-3 rounded-xl',
            'bg-[#2D5A8C]-muted border border-[#2D5A8C]/20',
          )}>
            <Info size={15} className="text-[#2D5A8C] shrink-0 mt-0.5" />
            <div>
              <p className="text-[10px] font-semibold text-[#2D5A8C] uppercase tracking-wider mb-1">Outside active tables</p>
              <p className="text-sm text-ink leading-relaxed">{message.summary || message.error}</p>
            </div>
          </div>
        )}

        {/* Success */}
        {!message.loading && !message.error && !message.out_of_scope && (
          <div className="flex flex-col gap-4">

            {/* SQL toggle */}
            {message.sql_query && (
              <div>
                <button
                  onClick={() => setShowSQL(s => !s)}
                  className={cn(
                    'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium',
                    'border border-border text-[#2D5A8C]',
                    'hover:bg-[#2D5A8C]-muted hover:border-[#2D5A8C] transition-colors duration-150',
                  )}
                >
                  {showSQL ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                  Generated SQL
                </button>

                {showSQL && (
                  <pre className={cn(
                    'mt-2 p-4 rounded-xl overflow-x-auto text-xs leading-relaxed',
                    'bg-[#2D5A8C] text-green-300 border border-[#2D5A8C]-light',
                  )}>
                    {message.sql_query}
                  </pre>
                )}
              </div>
            )}

            {/* Data table */}
            {message.data && message.columns && (
              <DataTable data={message.data} columns={message.columns} />
            )}

            {/* Chart */}
            {message.chart_data && message.chart_type && message.chart_type !== 'none' && (
              <div className="rounded-xl overflow-hidden border border-border">
                <DynamicChart chartData={message.chart_data} chartType={message.chart_type} />
              </div>
            )}

            {/* Summary */}
            {message.summary && (
              <div className={cn(
                'px-4 py-3 rounded-xl',
                'bg-surface border border-border',
                'text-sm text-ink leading-relaxed',
              )}>
                <style>{`
                  .md-summary p { margin: 6px 0; }
                  .md-summary ul, .md-summary ol { padding-left: 20px; margin: 6px 0; }
                  .md-summary li { margin-bottom: 4px; line-height: 1.6; }
                  .md-summary h2 { font-size: 15px; font-weight: 700; color: #2D5A8C; margin: 14px 0 6px; }
                  .md-summary h3 { font-size: 13px; font-weight: 600; color: #2D5A8C; margin: 10px 0 4px; }
                  .md-summary strong { color: #2D5A8C; font-weight: 600; }
                  .md-summary code { background: rgba(45, 90, 140, 0.1); padding: 1px 5px; border-radius: 4px; font-size: 11px; font-family: monospace; color: #2D5A8C; }
                  .md-summary a { color: #2D5A8C; text-decoration: underline; }
                  .md-summary .md-table-wrap { overflow-x: auto; margin: 10px 0; border: 1px solid #E2E6F0; border-radius: 10px; }
                  .md-summary table { width: 100%; border-collapse: collapse; font-size: 13px; }
                  .md-summary thead { background: rgba(45, 90, 140, 0.07); }
                  .md-summary th { text-align: left; font-weight: 600; color: #2D5A8C; padding: 10px 18px; white-space: nowrap; border-bottom: 1px solid #E2E6F0; }
                  .md-summary td { padding: 10px 18px; border-bottom: 1px solid #E2E6F0; vertical-align: top; line-height: 1.5; }
                  .md-summary tbody tr:last-child td { border-bottom: none; }
                  .md-summary tbody tr:nth-child(even) { background: rgba(45, 90, 140, 0.025); }
                `}</style>
                <div className="md-summary">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      table: ({ ...props }) => (
                        <div className="md-table-wrap">
                          <table {...props} />
                        </div>
                      ),
                    }}
                  >
                    {message.summary.replace(/\\n/g, '\n')}
                  </ReactMarkdown>
                </div>
              </div>
            )}

            {/* Sources — grouped by document, pages combined into one pill per file */}
            {message.referenced_documents && message.referenced_documents.length > 0 && (() => {
              type Group = { filename: string; pages: number[]; bestScore: number; excerpt: string }
              const groups = new Map<string, Group>()

              for (const d of message.referenced_documents) {
                const g = groups.get(d.filename)
                if (g) {
                  if (d.page != null && !g.pages.includes(d.page)) g.pages.push(d.page)
                  if (d.relevance_score > g.bestScore) { g.bestScore = d.relevance_score; g.excerpt = d.excerpt }
                } else {
                  groups.set(d.filename, {
                    filename: d.filename,
                    pages: d.page != null ? [d.page] : [],
                    bestScore: d.relevance_score,
                    excerpt: d.excerpt,
                  })
                }
              }

              const sortedGroups = Array.from(groups.values())
                .map(g => ({ ...g, pages: g.pages.sort((a, b) => a - b) }))
                .sort((a, b) => b.bestScore - a.bestScore)

              return (
                <div>
                  <p className="text-[10px] font-semibold text-ink-faint uppercase tracking-wider mb-2">
                    Sources
                  </p>
                  <div className="flex flex-col gap-1.5">
                    {sortedGroups.map((g, i) => (
                      <div
                        key={`${g.filename}-${i}`}
                        className="flex items-start gap-2 px-3 py-2 rounded-lg border border-border bg-surface text-xs"
                        title={g.excerpt}
                      >
                        <FileText size={13} className="text-[#2D5A8C] shrink-0 mt-0.5" />
                        <div className="min-w-0">
                          <span className="font-medium text-ink">{g.filename}</span>
                          {g.pages.length > 0 && (
                            <span className="text-ink-faint">
                              {' · '}{g.pages.length === 1 ? 'page' : 'pages'} {g.pages.join(', ')}
                            </span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )
            })()}

            {/* Follow-up suggestions — hidden in read-only (shared) view since there's no input to act on them */}
            {!readOnly && message.follow_up_questions && message.follow_up_questions.length > 0 && (
              <div>
                <p className="text-[10px] font-semibold text-ink-faint uppercase tracking-wider mb-2">
                  Follow-up suggestions
                </p>
                <div className="flex flex-wrap gap-2">
                  {message.follow_up_questions.map((q, i) => (
                    <button
                      key={i}
                      onClick={() => onFollowUp?.(q)}
                      className={cn(
                        'px-3 py-1.5 rounded-full text-xs border transition-all duration-150',
                        'border-border text-[#2D5A8C] bg-white',
                        'hover:bg-[#2D5A8C] hover:text-white hover:border-[#2D5A8C]',
                      )}
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default ChatMessage