import React, { useEffect, useState } from 'react'
import axios from 'axios'
import { X, Link2, Copy, Check, Loader2, Trash2, AlertCircle } from 'lucide-react'
import { cn } from './lib/utils'
import { API_BASE } from './config'

interface Props {
  notebookId: string
  chatId: string
  chatTitle: string
  onClose: () => void
}

/**
 * Share link modal — same idea as Claude's "Share" button:
 *   - Creates a frozen, read-only snapshot of the chat's messages
 *   - Anyone with an authorised SSO login can open the link
 *   - The link only ever unlocks this one chat, nothing else in the notebook
 */
const ShareChatModal: React.FC<Props> = ({ notebookId, chatId, chatTitle, onClose }) => {
  const [token, setToken]     = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)
  const [copied, setCopied]   = useState(false)
  const [revoking, setRevoking] = useState(false)

  const shareUrl = token ? `${window.location.origin}/share/${token}` : ''

  /* Create (or fetch existing) share on open */
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    axios.post(`${API_BASE}/api/notebooks/${notebookId}/chats/${chatId}/share`, {}, { withCredentials: true })
      .then(r => { if (!cancelled) setToken(r.data?.token ?? null) })
      .catch(e => { if (!cancelled) setError(e.response?.data?.detail || 'Failed to create share link') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [notebookId, chatId])

  const handleCopy = async () => {
    if (!shareUrl) return
    try {
      await navigator.clipboard.writeText(shareUrl)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch { /* clipboard denied — user can still select the text manually */ }
  }

  const handleRevoke = async () => {
    setRevoking(true)
    try {
      await axios.delete(`${API_BASE}/api/notebooks/${notebookId}/chats/${chatId}/share`, { withCredentials: true })
      onClose()
    } catch (e: any) {
      setError(e.response?.data?.detail || 'Failed to revoke link')
    } finally {
      setRevoking(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-6"
      style={{ background: 'rgba(15,23,42,0.5)', backdropFilter: 'blur(3px)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-white rounded-2xl shadow-lg overflow-hidden animate-slide-up w-full max-w-md">

        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 bg-[#2D5A8C]">
          <div className="w-8 h-8 rounded-xl bg-white/10 flex items-center justify-center shrink-0">
            <Link2 size={15} className="text-white" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-white">Share chat</p>
            <p className="text-xs text-white/60 truncate">{chatTitle}</p>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg text-white/60 hover:text-white hover:bg-white/10 transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-5 flex flex-col gap-4">
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-6 text-ink-faint">
              <Loader2 size={14} className="animate-spin" />
              <span className="text-xs">Creating link…</span>
            </div>
          ) : error ? (
            <div className="flex items-start gap-2.5 p-3 rounded-xl bg-tertiary-muted border border-tertiary/20">
              <AlertCircle size={14} className="text-tertiary shrink-0 mt-0.5" />
              <p className="text-sm text-tertiary leading-snug">{error}</p>
            </div>
          ) : (
            <>
              <p className="text-xs text-ink-muted leading-relaxed">
                Anyone with an authorised login can open this link 
              </p>

              <div className="flex items-center gap-2">
                <input
                  readOnly
                  value={shareUrl}
                  onFocus={e => e.target.select()}
                  className="flex-1 text-xs text-ink-muted bg-surface border border-border rounded-lg px-3 py-2 outline-none truncate"
                />
                <button
                  onClick={handleCopy}
                  className={cn(
                    'flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium shrink-0 transition-colors',
                    copied
                      ? 'bg-green-50 text-green-700 border border-green-200'
                      : 'bg-[#2D5A8C] text-white hover:bg-[#2D5A8C]-hover',
                  )}
                >
                  {copied ? <Check size={13} /> : <Copy size={13} />}
                  {copied ? 'Copied' : 'Copy'}
                </button>
              </div>

              <button
                onClick={handleRevoke}
                disabled={revoking}
                className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium text-tertiary hover:bg-tertiary-muted transition-colors disabled:opacity-50 self-start"
              >
                {revoking ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                Revoke access
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export default ShareChatModal