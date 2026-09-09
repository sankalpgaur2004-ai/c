import React, { useState, useRef, useEffect, KeyboardEvent } from 'react'
import { Send } from 'lucide-react'
import { cn } from '../lib/utils'

export type SourceFilter = 'all' | 'database' | 'documents' | 'dashboard' | string

interface Props {
  onSubmit: (question: string, sourceFilter: SourceFilter) => void
  loading: boolean
  placeholder?: string
  sourceFilter: SourceFilter
}

const ChatInput: React.FC<Props> = ({
  onSubmit, loading, placeholder, sourceFilter,
}) => {
  const [value, setValue]       = useState('')
  const textareaRef             = useRef<HTMLTextAreaElement>(null)

  /* Auto-grow textarea */
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 160) + 'px'
  }, [value])

  const handleSubmit = () => {
    const trimmed = value.trim()
    if (!trimmed || loading) return
    onSubmit(trimmed, sourceFilter)
    setValue('')
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSubmit() }
  }

  const canSubmit = !loading && value.trim().length > 0

  return (
    <div className="px-4 py-3 bg-white border-t border-border">

      {/* Textarea + send */}
      <div className={cn(
        'flex items-end gap-2 rounded-xl border bg-surface px-3 py-2.5',
        'border-border focus-within:border-[#2D5A8C] focus-within:ring-2 focus-within:ring-secondary/10',
        'transition-all duration-150',
      )}>

        {/* Textarea */}
        <textarea
          ref={textareaRef}
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder || 'Ask a question about your data…'}
          disabled={loading}
          rows={1}
          className={cn(
            'flex-1 resize-none bg-transparent border-none outline-none',
            'text-sm text-ink placeholder-ink-faint leading-relaxed',
            'min-h-[24px] max-h-[160px] py-0.5',
            'disabled:opacity-50',
          )}
        />

        {/* Send button */}
        <button
          onClick={handleSubmit}
          disabled={!canSubmit}
          className={cn(
            'shrink-0 self-end w-8 h-8 rounded-lg flex items-center justify-center',
            'transition-all duration-150',
            canSubmit
              ? 'bg-[#2D5A8C] text-white hover:bg-[#2D5A8C]-hover shadow-sm'
              : 'bg-border text-ink-faint cursor-not-allowed',
          )}
          title="Send (Enter)"
        >
          {loading ? (
            <div className="w-3.5 h-3.5 border-2 border-white/40 border-t-white rounded-full animate-spin" />
          ) : (
            <Send size={14} />
          )}
        </button>
      </div>

      <p className="text-center text-[10px] text-ink-faint mt-1.5">
        Enter to send · Shift+Enter for new line
      </p>
    </div>
  )
}

export default ChatInput