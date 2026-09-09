import React, { useState } from 'react'
import { useSessionContext } from '../App'

const PERSONAS = ['executive', 'sales_manager', 'field_rep', 'analyst']
const PERSONA_LABELS: Record<string, string> = {
  executive: 'Executive',
  sales_manager: 'Sales Manager',
  field_rep: 'Field Rep',
  analyst: 'Analyst',
}

const PERSONA_COLORS: Record<string, string> = {
  executive: '#7C5CFF',
  sales_manager: '#3B82F6',
  field_rep: '#10B981',
  analyst: '#F59E0B',
}

const PersonaDropdown: React.FC = () => {
  const { currentUser, updatePersona } = useSessionContext()
  const [isOpen, setIsOpen] = useState(false)
  const [loading, setLoading] = useState(false)

  const currentPersona = currentUser?.persona || 'executive'

  const handleSelectPersona = async (persona: string) => {
    if (persona === currentPersona || loading) return
    setLoading(true)
    try {
      await updatePersona(persona)
      setIsOpen(false)
    } catch (err) {
      console.error('Failed to update persona:', err)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ position: 'relative' }}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          padding: '6px 12px',
          backgroundColor: 'transparent',
          border: `1px solid ${PERSONA_COLORS[currentPersona] || '#D1D5DB'}`,
          borderRadius: '20px',
          cursor: 'pointer',
          fontSize: '12px',
          fontWeight: '500',
          color: PERSONA_COLORS[currentPersona] || '#2C3B4D',
          fontFamily: 'DM Sans, sans-serif',
          transition: 'all 0.2s ease',
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.backgroundColor = `${PERSONA_COLORS[currentPersona] || '#E5E7EB'}20`
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.backgroundColor = 'transparent'
        }}
      >
        <span
          style={{
            width: '8px',
            height: '8px',
            borderRadius: '50%',
            backgroundColor: PERSONA_COLORS[currentPersona] || '#D1D5DB',
          }}
        />
        {PERSONA_LABELS[currentPersona] || currentPersona}
        <span style={{ fontSize: '10px' }}>▾</span>
      </button>

      {isOpen && (
        <div
          style={{
            position: 'absolute',
            top: '100%',
            right: 0,
            marginTop: '8px',
            backgroundColor: 'white',
            borderRadius: '6px',
            boxShadow: '0 10px 25px rgba(0, 0, 0, 0.1)',
            zIndex: 1000,
            minWidth: '160px',
            overflow: 'hidden',
          }}
        >
          {PERSONAS.map(persona => (
            <button
              key={persona}
              onClick={() => handleSelectPersona(persona)}
              style={{
                display: 'block',
                width: '100%',
                padding: '10px 16px',
                border: 'none',
                backgroundColor: currentPersona === persona ? `${PERSONA_COLORS[persona] || '#E5E7EB'}20` : 'white',
                cursor: loading ? 'not-allowed' : 'pointer',
                fontSize: '13px',
                fontWeight: currentPersona === persona ? '600' : '400',
                color: '#2C3B4D',
                textAlign: 'left',
                fontFamily: 'DM Sans, sans-serif',
                borderBottom: persona !== PERSONAS[PERSONAS.length - 1] ? '1px solid #F3F4F6' : 'none',
                transition: 'background-color 0.2s ease',
              }}
              onMouseEnter={(e) => {
                if (currentPersona !== persona && !loading) {
                  e.currentTarget.style.backgroundColor = '#F3F4F6'
                }
              }}
              onMouseLeave={(e) => {
                if (currentPersona !== persona) {
                  e.currentTarget.style.backgroundColor = 'white'
                }
              }}
              disabled={loading}
            >
              <span style={{
                display: 'inline-block',
                width: '8px',
                height: '8px',
                borderRadius: '50%',
                backgroundColor: PERSONA_COLORS[persona] || '#D1D5DB',
                marginRight: '8px',
              }} />
              {PERSONA_LABELS[persona] || persona}
              {currentPersona === persona && ' ✓'}
            </button>
          ))}
        </div>
      )}

      {isOpen && (
        <div
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            zIndex: 999,
          }}
          onClick={() => setIsOpen(false)}
        />
      )}
    </div>
  )
}

export default PersonaDropdown
