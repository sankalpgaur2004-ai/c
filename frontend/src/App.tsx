import React, { useState, createContext, useContext, useEffect, useRef } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useParams, useNavigate } from 'react-router-dom'
import axios from 'axios'
import { API_BASE } from './config'
import HomePage from './HomePage'
import NotebookPage from './NotebookPage'
import ProjectDetailPage from './ProjectDetailPage'
import LandingPage from './LandingPage'


/* ── Types ─────────────────────────────────────────────────────── */
export interface CurrentUser {
  user_id: string
  email: string
  first_name: string
  last_name: string
  persona: string | null
}

interface SessionCtx {
  currentUser: CurrentUser | null
  // Kept as a no-op for PersonaDropdown's typecheck — persona is handled
  // per-notebook now, this legacy hook no longer exists.
  updatePersona: (persona: string) => Promise<void>
  logout: () => void
}

export const SessionContext = createContext<SessionCtx | null>(null)
export const useSessionContext = () => {
  const ctx = useContext(SessionContext)
  if (!ctx) throw new Error('useSessionContext must be used within SessionContext.Provider')
  return ctx
}

type AuthState = 'checking' | 'unauthenticated' | 'authenticated'

/* ── Share opener ──────────────────────────────────────────────────
 * Not a parallel "shared chat" UI — this forks the share into a real
 * notebook+chat for the current viewer, then hands off straight to the
 * exact same NotebookPage everyone else uses. Same link, same frontend,
 * just a different chat_id/notebook_id under the viewer's own account. */
const ShareOpener: React.FC = () => {
  const { token } = useParams<{ token: string }>()
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)
  const openedRef = useRef(false)   // guards against StrictMode's double-invoke firing this twice

  useEffect(() => {
    if (!token || openedRef.current) return
    openedRef.current = true
    axios.post(`${API_BASE}/api/share/${token}/open`, {}, { withCredentials: true })
      .then(r => {
        const { notebook_id, chat_id } = r.data
        navigate(`/notebook/${notebook_id}/chat/${chat_id}`, { replace: true })
      })
      .catch(e => {
        openedRef.current = false   // allow a retry (e.g. after a transient network error)
        setError(e.response?.data?.detail || 'This share link is invalid or has been revoked')
      })
  }, [token, navigate])

  return (
    <div className="flex items-center justify-center h-screen bg-surface">
      <div className="flex flex-col items-center gap-4 text-center px-6">
        {error ? (
          <>
            <p className="text-sm font-semibold text-ink">Can't open this link</p>
            <p className="text-xs text-ink-muted max-w-xs">{error}</p>
            <button onClick={() => navigate('/home')} className="text-xs font-medium text-[#2D5A8C] hover:underline">
              Go to your notebooks
            </button>
          </>
        ) : (
          <>
            <div className="w-10 h-10 rounded-full border-4 border-[#2D5A8C]-muted border-t-secondary animate-spin" />
            <p className="text-sm text-ink-muted font-medium">Setting up the shared chat…</p>
          </>
        )}
      </div>
    </div>
  )
}

/* ── App ────────────────────────────────────────────────────────── */
const App: React.FC = () => {
  const [authState, setAuthState]     = useState<AuthState>('checking')
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null)

  /* ── Auth check on mount ── */
  useEffect(() => {
    axios.get(`${API_BASE}/api/auth/me`, { withCredentials: true })
      .then(r => {
        setCurrentUser(r.data)
        setAuthState('authenticated')
      })
      .catch(() => {
        setAuthState('unauthenticated')
        // Preserve where the user was actually trying to go (e.g. a
        // /share/{token} link) so the SSO round-trip can send them back
        // there instead of always landing on the site root.
        const dest = window.location.pathname + window.location.search
        window.location.href = `${API_BASE}/auth/login?redirect=${encodeURIComponent(dest)}`
      })
  }, [])

  /* ── Logout ── */
  const handleLogout = async () => {
    try { await axios.post(`${API_BASE}/auth/logout`, {}, { withCredentials: true }) } catch { }
    window.location.href = 'https://a2.circulants.ai/'
  }

  /* ── Loading screen ── */
  if (authState === 'checking') {
    return (
      <div className="flex items-center justify-center h-screen bg-surface">
        <div className="flex flex-col items-center gap-4 animate-fade-in">
          {/* Spinner */}
          <div className="w-10 h-10 rounded-full border-4 border-[#2D5A8C]-muted border-t-secondary animate-spin" />
          <p className="text-sm text-ink-muted font-medium">Loading…</p>
        </div>
      </div>
    )
  }

  if (authState === 'unauthenticated') return null

  /* ── Authenticated ── */
  return (
    <SessionContext.Provider value={{
      currentUser,
      updatePersona: async () => {},   // persona handled per-notebook now
      logout: handleLogout,
    }}>
      <BrowserRouter>
        <Routes>
          {/* Landing page — shown first before home */}
          <Route path="/" element={<LandingPage />} />

          {/* Home — notebook grid */}
          <Route
            path="/home"
            element={
              <HomePage
                currentUser={currentUser}
                onLogout={handleLogout}
              />
            }
          />

          {/* Project detail — chat list + config sidebar */}
          <Route
            path="/notebook/:notebookId"
            element={<ProjectDetailPage />}
          />

          {/* Notebook workspace — 3-panel chat UI */}
          <Route
            path="/notebook/:notebookId/chat/:chatId"
            element={<NotebookPage />}
          />

          {/* Shared chat link — forks a live copy for the viewer, then hands
              off to the same notebook chat UI as everywhere else. */}
          <Route path="/share/:token" element={<ShareOpener />} />

          {/* Fallback */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </SessionContext.Provider>
  )
}

export default App