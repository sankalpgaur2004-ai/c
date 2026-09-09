import React, { useState } from 'react'
import { X, ChevronDown } from 'lucide-react'

const PERSONAS = [
  { value: '', label: 'No persona' },
  { value: 'executive', label: 'Executive' },
  { value: 'analyst', label: 'Analyst' },
  { value: 'sales_manager', label: 'Sales Manager' },
  { value: 'field_rep', label: 'Field Rep' },
]

interface CreateChatModalProps {
  onSubmit: (title: string, persona?: string) => Promise<void>
  onClose: () => void
}

const CreateChatModal: React.FC<CreateChatModalProps> = ({ onSubmit, onClose }) => {
  const [title, setTitle] = useState('')
  const [persona, setPersona] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async () => {
    const trimmedTitle = title.trim() || 'New Chat'
    setLoading(true)
    try {
      await onSubmit(trimmedTitle, persona || undefined)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 animate-fade-in">
      <div className="bg-white rounded-2xl shadow-2xl w-96 max-w-[90%] animate-scale-in">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <h2 className="text-lg font-semibold text-ink">New Chat</h2>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-ink-faint hover:bg-surface transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5 flex flex-col gap-4">

          {/* Title input */}
          <div>
            <label className="block text-xs font-medium text-ink-faint mb-2">
              Chat title (optional)
            </label>
            <input
              type="text"
              value={title}
              onChange={e => setTitle(e.target.value)}
              placeholder="e.g., Q3 Sales Review"
              className="input text-sm w-full"
              onKeyDown={e => {
                if (e.key === 'Enter' && !loading) handleSubmit()
              }}
            />
          </div>

          {/* Persona selector */}
          <div>
            <label className="block text-xs font-medium text-ink-faint mb-2">
              Chat Persona 
            </label>
            <p className="text-xs text-ink-faint mb-2">
              Once set, this persona cannot be changed. Create a new chat to use a different persona.
            </p>
            <div className="relative">
              <select
                value={persona}
                onChange={e => setPersona(e.target.value)}
                className="input text-sm appearance-none pr-8 w-full"
              >
                {PERSONAS.map(p => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
              <ChevronDown size={13} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-ink-faint pointer-events-none" />
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-border flex items-center gap-2 justify-end">
          <button
            onClick={onClose}
            disabled={loading}
            className="btn-md btn-outline"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading}
            className="btn-md btn-primary"
          >
            {loading ? 'Creating…' : 'Create Chat'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default CreateChatModal
