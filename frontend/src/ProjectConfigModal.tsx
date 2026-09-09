import React, { useState, useEffect } from 'react'
import {
  X, Sparkles, FileText, FolderOpen,
  ChevronRight, Loader2,
} from 'lucide-react'
import { cn } from './lib/utils'

/* ── Types ──────────────────────────────────────────────────────── */
export interface ProjectConfig {
  name: string
  description: string
  instructions: string
}

interface ProjectConfigModalProps {
  /** 'create' shows "Create project" CTA, 'edit' shows "Save changes" */
  mode: 'create' | 'edit'
  /** Pre-fill values when editing */
  initialValues?: Partial<ProjectConfig>
  onSubmit: (config: ProjectConfig) => Promise<void>
  onClose: () => void
}

/* ════════════════════════════════════════════════════════════════ */

const ProjectConfigModal: React.FC<ProjectConfigModalProps> = ({
  mode,
  initialValues,
  onSubmit,
  onClose,
}) => {
  const [name, setName]               = useState(initialValues?.name ?? '')
  const [description, setDescription] = useState(initialValues?.description ?? '')
  const [instructions, setInstructions] = useState(initialValues?.instructions ?? '')
  const [loading, setLoading]         = useState(false)
  const [error, setError]             = useState<string | null>(null)

  /* Sync when initialValues change (e.g. editing opens modal) */
  useEffect(() => {
    if (initialValues) {
      setName(initialValues.name ?? '')
      setDescription(initialValues.description ?? '')
      setInstructions(initialValues.instructions ?? '')
    }
  }, [initialValues?.name])

  const handleSubmit = async () => {
    if (!name.trim()) { setError('Project name is required'); return }
    setLoading(true); setError(null)
    try {
      await onSubmit({ name: name.trim(), description, instructions })
    } catch (e: any) {
      setError(e?.message || 'Something went wrong')
      setLoading(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(15,23,42,0.5)', backdropFilter: 'blur(3px)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className={cn(
        'relative w-full max-w-xl bg-white rounded-2xl shadow-lg',
        'flex flex-col overflow-hidden animate-slide-up',
        'max-h-[90vh]',
      )}>

        {/* ── Header ── */}
        <div className="flex items-start justify-between px-6 pt-6 pb-4 shrink-0">
          <div>
            <h2 className="text-lg font-semibold text-[#2D5A8C]">
              {mode === 'create' ? 'Create new project' : 'Project settings'}
            </h2>
            <p className="text-xs text-ink-faint mt-0.5">
              {mode === 'create'
                ? 'Configure your project before getting started'
                : 'Update your project name and instructions'}
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-ink-faint hover:bg-[#2D5A8C]-muted hover:text-[#2D5A8C] transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {/* ── Body ── */}
        <div className="flex-1 overflow-y-auto px-6 pb-6 flex flex-col gap-5">

          {/* Project name */}
          <Section icon={<FolderOpen size={15} />} label="Project name" required>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder='e.g. "Q2 Sales Analysis"'
              className="input text-sm"
              autoFocus
            />
          </Section>

          {/* Description */}
          <Section icon={<FileText size={15} />} label="Description">
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="What is this project about? (optional)"
              rows={2}
              className="input text-sm resize-none"
            />
          </Section>

          {/* Instructions */}
          <Section icon={<Sparkles size={15} />} label="Project instructions">
            <p className="text-xs text-ink-faint mb-2">
              Tell the AI how to behave in this project — tone, focus areas, what to avoid, preferred formats, etc.
            </p>
            <textarea
              value={instructions}
              onChange={e => setInstructions(e.target.value)}
              placeholder={`e.g. "Always compare results to the previous quarter. Focus on EMEA region. Keep answers concise and use bullet points."`}
              rows={4}
              className="input text-sm resize-none"
            />
            <p className="text-[10px] text-ink-faint mt-1">
              {instructions.length} / 1000 characters
            </p>
          </Section>

          {/* Error */}
          {error && (
            <div className="flex items-center gap-2 p-3 bg-tertiary-muted rounded-lg border border-tertiary/20">
              <X size={13} className="text-tertiary shrink-0" />
              <p className="text-xs text-tertiary">{error}</p>
            </div>
          )}
        </div>

        {/* ── Footer ── */}
        <div className={cn(
          'flex items-center justify-between gap-3 px-6 py-4 shrink-0',
          'border-t border-border bg-surface',
        )}>
          <button onClick={onClose} className="btn-md btn-outline">
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading || !name.trim()}
            className="btn-md btn-primary gap-2"
          >
            {loading ? (
              <><Loader2 size={14} className="animate-spin" /> {mode === 'create' ? 'Creating…' : 'Saving…'}</>
            ) : (
              <>{mode === 'create' ? 'Create project' : 'Save changes'} <ChevronRight size={14} /></>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}

/* ── Section wrapper ─────────────────────────────────────────────── */
const Section: React.FC<{
  icon: React.ReactNode
  label: string
  required?: boolean
  children: React.ReactNode
}> = ({ icon, label, required, children }) => (
  <div>
    <div className="flex items-center gap-1.5 mb-2">
      <span className="text-[#2D5A8C]">{icon}</span>
      <label className="text-sm font-medium text-ink">
        {label}
        {required && <span className="text-tertiary ml-0.5">*</span>}
      </label>
    </div>
    {children}
  </div>
)

export default ProjectConfigModal