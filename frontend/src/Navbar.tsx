import React, { useState, useRef, useEffect } from 'react'
import { LogOut, Settings, ChevronDown, User, SlidersHorizontal } from 'lucide-react'
import { cn } from './lib/utils'

interface NavbarProps {
  user: {
    first_name: string
    last_name: string
    email: string
  } | null
  onLogout: () => void
  /** Optional: notebook name shown in center when inside a notebook */
  notebookName?: string
  /** Optional: callback to rename the notebook */
  onRenameNotebook?: (name: string) => void
  /** Optional: open project settings */
  onSettings?: () => void
}

const CirculantsLogo: React.FC<{ className?: string }> = ({ className }) => (
  <div className={cn('flex items-center mt-4 -ml-2', className)}>
    <img
      src="/CIRCULANTSLOGO.png"
      alt="Circulants"
      className="h-14 w-auto object-contain"
      style={{ filter: 'brightness(0) invert(1)' }}
      onError={e => {
        const t = e.currentTarget
        if (!t.src.includes('circulants.png')) {
          t.src = '/circulants.png'
        }
      }}
    />
  </div>
)

const Navbar: React.FC<NavbarProps> = ({
  user,
  onLogout,
  notebookName,
  onRenameNotebook,
  onSettings,
}) => {
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [editing, setEditing]           = useState(false)
  const [nameValue, setNameValue]       = useState(notebookName ?? '')
  const dropdownRef = useRef<HTMLDivElement>(null)
  const inputRef    = useRef<HTMLInputElement>(null)

  // Sync external name changes
  useEffect(() => { setNameValue(notebookName ?? '') }, [notebookName])

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // Focus input when editing starts
  useEffect(() => {
    if (editing) inputRef.current?.focus()
  }, [editing])

  const initials = user
    ? `${user.first_name?.[0] ?? ''}${user.last_name?.[0] ?? ''}`.toUpperCase()
    : '?'

  const fullName = user ? `${user.first_name} ${user.last_name}` : ''

  const commitRename = () => {
    setEditing(false)
    const trimmed = nameValue.trim()
    if (trimmed && trimmed !== notebookName) {
      onRenameNotebook?.(trimmed)
    } else {
      setNameValue(notebookName ?? '')
    }
  }

  return (
    <nav className={cn(
      'fixed top-0 left-0 right-0 z-50',
      'h-14 px-5',
      'bg-[#2D5A8C] border-b border-[#2D5A8C]-light',
      'flex items-center justify-between',
      'shadow-navy',
    )}>

      {/* ── Left: Logo ─────────────────────────────── */}
      <a
        href="/"
        className="flex items-center gap-2 shrink-0 group"
        aria-label="Go to home"
      >
        <CirculantsLogo className="transition-opacity group-hover:opacity-80" />
      </a>

      {/* ── Center: Notebook name (optional) ────────── */}
      {notebookName !== undefined && (
        <div className="absolute left-1/2 -translate-x-1/2 flex items-center gap-2">
          {editing ? (
            <input
              ref={inputRef}
              value={nameValue}
              onChange={e => setNameValue(e.target.value)}
              onBlur={commitRename}
              onKeyDown={e => {
                if (e.key === 'Enter') commitRename()
                if (e.key === 'Escape') {
                  setEditing(false)
                  setNameValue(notebookName)
                }
              }}
              className={cn(
                'bg-[#2D5A8C]-light text-white text-sm font-medium',
                'px-3 py-1 rounded-md border border-[#2D5A8C]-hover',
                'focus:outline-none focus:ring-2 focus:ring-white/30',
                'min-w-[180px] text-center',
              )}
            />
          ) : (
            <button
              onClick={() => onRenameNotebook && setEditing(true)}
              className={cn(
                'text-white text-sm font-medium px-3 py-1 rounded-md',
                'transition-colors duration-150',
                onRenameNotebook
                  ? 'hover:bg-[#2D5A8C]-light cursor-text'
                  : 'cursor-default',
              )}
              title={onRenameNotebook ? 'Click to rename' : undefined}
            >
              {notebookName || 'Untitled notebook'}
            </button>
          )}
        </div>
      )}

      {/* ── Right: Settings + User avatar ─────────────── */}
      <div className="flex items-center gap-3 shrink-0" ref={dropdownRef}>
        {onSettings && (
          <button
            onClick={onSettings}
            title="Project settings"
            className={cn(
              'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium',
              'text-white/80 hover:text-white hover:bg-[#2D5A8C]-light',
              'transition-colors duration-150',
            )}
          >
            <SlidersHorizontal size={14} />
            <span className="hidden sm:block">Settings</span>
          </button>
        )}

        {/* Avatar button */}
        <button
          onClick={() => setDropdownOpen(o => !o)}
          className={cn(
            'flex items-center gap-2 pl-2 pr-3 py-1.5 rounded-lg',
            'text-white/90 hover:text-white hover:bg-[#2D5A8C]-light',
            'transition-colors duration-150',
          )}
          aria-expanded={dropdownOpen}
          aria-haspopup="true"
        >
          {/* Initials circle */}
          <span className={cn(
            'w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0',
            'bg-tertiary text-white',
          )}>
            {initials}
          </span>
          <span className="text-sm font-medium hidden sm:block max-w-[120px] truncate">
            {user?.first_name ?? 'User'}
          </span>
          <ChevronDown
            size={14}
            className={cn(
              'text-white/60 transition-transform duration-200',
              dropdownOpen && 'rotate-180',
            )}
          />
        </button>

        {/* Dropdown menu */}
        {dropdownOpen && (
          <div className={cn(
            'absolute top-12 right-4 z-50 w-56',
            'bg-white rounded-lg shadow-lg border border-border',
            'py-1 animate-fade-in',
          )}>
            {/* User info header */}
            <div className="px-4 py-3 border-b border-border">
              <p className="text-sm font-semibold text-ink truncate">{fullName}</p>
              <p className="text-xs text-ink-muted truncate mt-0.5">{user?.email}</p>
            </div>

            {/* Menu items */}
            <div className="py-1">
              <DropdownItem icon={<User size={14} />} label="Profile" onClick={() => setDropdownOpen(false)} />
              <DropdownItem icon={<Settings size={14} />} label="Settings" onClick={() => setDropdownOpen(false)} />
            </div>

            <div className="border-t border-border py-1">
              <DropdownItem
                icon={<LogOut size={14} />}
                label="Sign out"
                onClick={() => { setDropdownOpen(false); onLogout() }}
                danger
              />
            </div>
          </div>
        )}
      </div>
    </nav>
  )
}

/* ── Small helper ─────────────────────────────────────────────── */
interface DropdownItemProps {
  icon: React.ReactNode
  label: string
  onClick: () => void
  danger?: boolean
}

const DropdownItem: React.FC<DropdownItemProps> = ({ icon, label, onClick, danger }) => (
  <button
    onClick={onClick}
    className={cn(
      'w-full flex items-center gap-3 px-4 py-2 text-sm',
      'transition-colors duration-150',
      danger
        ? 'text-tertiary hover:bg-tertiary-muted'
        : 'text-ink hover:bg-[#2D5A8C]-muted hover:text-[#2D5A8C]',
    )}
  >
    <span className={danger ? 'text-tertiary' : 'text-ink-muted'}>{icon}</span>
    {label}
  </button>
)

export default Navbar