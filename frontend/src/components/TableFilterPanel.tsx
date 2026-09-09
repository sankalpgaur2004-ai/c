// TableFilterPanel.tsx — redesigned with warm professional palette
import React, { useState, useEffect, useRef } from 'react'
import axios from 'axios'
import { API_BASE } from '../config'

interface TableFilterPanelProps { onFilterChange?: (activeTables: string[]) => void }

interface FilterState {
  connected: boolean; dbType: string; allTables: string[];
  activeTables: string[]; loading: boolean; saving: boolean;
  error: string | null; sources: Record<string, string>; isFederated: boolean
}

const TableFilterPanel: React.FC<TableFilterPanelProps> = ({ onFilterChange }) => {
  const [state, setState] = useState<FilterState>({
    connected: false, dbType: '', allTables: [], activeTables: [],
    loading: true, saving: false, error: null, sources: {}, isFederated: false,
  })
  const [expanded, setExpanded] = useState(true)
  const [sseConnected, setSseConnected] = useState(false)
  const isSavingRef = useRef(false)
  const esRef = useRef<EventSource | null>(null)
  const hasReceivedMessageRef = useRef(false)

  useEffect(() => {
    const connect = () => {
      if (esRef.current) esRef.current.close()
      const es = new EventSource(`${API_BASE}/api/datasources/stream`)
      esRef.current = es
      es.onopen = () => {
        setSseConnected(true)
        if (hasReceivedMessageRef.current) setState(prev => ({ ...prev, loading: false }))
      }
      es.onmessage = (event) => {
        if (isSavingRef.current) return
        try {
          const data = JSON.parse(event.data)
          hasReceivedMessageRef.current = true
          setState(prev => ({
            ...prev,
            connected: data.connected, dbType: data.db_type || '',
            allTables: data.all_tables || [], activeTables: data.active_tables || [],
            loading: false, error: null, sources: data.sources || {}, isFederated: data.is_federated || false,
          }))
        } catch { }
      }
      es.onerror = () => {
        setSseConnected(false)
        if (hasReceivedMessageRef.current) setState(prev => ({ ...prev, loading: false }))
      }
    }
    connect()
    return () => { esRef.current?.close(); esRef.current = null }
  }, [])

  const toggleTable = async (table: string) => {
    if (state.saving) return
    const newActive = state.activeTables.includes(table)
      ? state.activeTables.filter(t => t !== table)
      : [...state.activeTables, table]

    if (newActive.length === 0) {
      setState(prev => ({ ...prev, error: 'At least one table must remain active' }))
      setTimeout(() => setState(prev => ({ ...prev, error: null })), 2500)
      return
    }
    isSavingRef.current = true
    setState(prev => ({ ...prev, activeTables: newActive, saving: true, error: null }))
    try {
      await axios.post(`${API_BASE}/api/datasources/update-table-filter`, { tables: newActive })
      setState(prev => ({ ...prev, saving: false }))
      onFilterChange?.(newActive)
    } catch (e: any) {
      setState(prev => ({ ...prev, activeTables: state.activeTables, saving: false, error: e.response?.data?.detail || 'Update failed' }))
    } finally { isSavingRef.current = false }
  }

  const selectAll = async () => {
    if (state.saving) return
    isSavingRef.current = true
    setState(prev => ({ ...prev, activeTables: [...prev.allTables], saving: true }))
    try {
      await axios.post(`${API_BASE}/api/datasources/update-table-filter`, { tables: state.allTables })
      setState(prev => ({ ...prev, saving: false }))
      onFilterChange?.(state.allTables)
    } catch { setState(prev => ({ ...prev, saving: false })) }
    finally { isSavingRef.current = false }
  }

  if (!state.loading && !state.connected) {
    return (
      <div style={{
        width: '200px', minWidth: '200px', padding: '20px 14px',
        backgroundColor: '#F7F4EF', borderLeft: '1px solid #D8D0C4',
        display: 'flex', flexDirection: 'column', alignItems: 'center',
        justifyContent: 'center', gap: '10px',
      }}>
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#C9C1B1" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
        </svg>
        <span style={{ textAlign: 'center', fontSize: '12px', color: '#C9C1B1', lineHeight: 1.5 }}>No database connected. Connect one in Data Sources.</span>
      </div>
    )
  }

  return (
    <div style={{
      width: expanded ? '210px' : '38px', minWidth: expanded ? '210px' : '38px',
      backgroundColor: '#F7F4EF', borderLeft: '1px solid #D8D0C4',
      display: 'flex', flexDirection: 'column',
      transition: 'width 0.2s ease, min-width 0.2s ease',
      overflow: 'hidden', position: 'relative',
    }}>

      {/* Collapse toggle */}
      <button
        onClick={() => setExpanded(p => !p)}
        title={expanded ? 'Collapse' : 'Expand'}
        style={{
          position: 'absolute', top: '12px',
          right: expanded ? '10px' : '7px',
          width: '22px', height: '22px', borderRadius: '5px',
          border: '1px solid #D8D0C4', backgroundColor: '#EEE9DF',
          color: '#7FA3C0', cursor: 'pointer', fontSize: '12px',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          zIndex: 10, flexShrink: 0,
        }}
      >
        {expanded ? '›' : '‹'}
      </button>

      {expanded && (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>

          {/* Header */}
          <div style={{ padding: '14px 14px 10px', borderBottom: '1px solid #D8D0C4', paddingRight: '36px', flexShrink: 0 }}>
            <div style={{ fontSize: '10px', fontWeight: 700, color: '#4A6B8A', letterSpacing: '0.7px', textTransform: 'uppercase', marginBottom: '8px' }}>
              Table Filter
            </div>

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
              <span style={{
                fontSize: '10px', padding: '2px 8px', backgroundColor: '#EEE9DF',
                color: '#4A6B8A', borderRadius: '20px', border: '1px solid #D8D0C4',
                fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.4px',
              }}>
                {state.isFederated ? `${Object.keys(state.sources).length} sources` : (state.dbType || 'DB')}
              </span>
              <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                <div style={{
                  width: '6px', height: '6px', borderRadius: '50%',
                  backgroundColor: sseConnected ? '#10b981' : '#FFB162',
                }} />
                <span style={{ fontSize: '10px', color: '#C9C1B1' }}>
                  {state.activeTables.length}/{state.allTables.length}
                </span>
              </div>
            </div>

            {state.allTables.length > 0 && (
              <button
                onClick={selectAll} disabled={state.saving}
                style={{
                  width: '100%', padding: '5px 0',
                  backgroundColor: 'transparent', border: '1px solid #C9C1B1',
                  borderRadius: '5px', color: '#4A6B8A', fontSize: '11px',
                  cursor: state.saving ? 'not-allowed' : 'pointer',
                  fontFamily: 'DM Sans, sans-serif', fontWeight: 500,
                  transition: 'all 0.12s',
                }}
                onMouseEnter={e => { if (!state.saving) { e.currentTarget.style.borderColor = '#FFB162'; e.currentTarget.style.color = '#2C3B4D' } }}
                onMouseLeave={e => { e.currentTarget.style.borderColor = '#C9C1B1'; e.currentTarget.style.color = '#4A6B8A' }}
              >
                Select All
              </button>
            )}
          </div>

          {/* Error */}
          {state.error && (
            <div style={{
              margin: '8px 10px', padding: '7px 10px', borderRadius: '6px',
              backgroundColor: 'rgba(163,81,57,0.07)', border: '1px solid rgba(163,81,57,0.2)',
              color: '#A35139', fontSize: '11px',
            }}>
              {state.error}
            </div>
          )}

          {/* Loading */}
          {state.loading && (
            <div style={{ padding: '20px 14px', fontSize: '12px', color: '#C9C1B1', textAlign: 'center' }}>
              Connecting…
            </div>
          )}

          {/* Table list */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '6px 0' }}>
            {state.allTables.map(table => {
              const isActive = state.activeTables.includes(table)
              const dotIdx = table.indexOf('.')
              const alias = dotIdx > -1 ? table.substring(0, dotIdx) : null
              const tableName = dotIdx > -1 ? table.substring(dotIdx + 1) : table

              return (
                <div
                  key={table}
                  onClick={() => toggleTable(table)}
                  title={`${isActive ? 'Exclude' : 'Include'} "${table}"`}
                  style={{
                    display: 'flex', flexDirection: 'column', alignItems: 'flex-start',
                    padding: '8px 12px', cursor: state.saving ? 'not-allowed' : 'pointer',
                    backgroundColor: isActive ? 'rgba(255,177,98,0.08)' : 'transparent',
                    borderLeft: `3px solid ${isActive ? '#FFB162' : 'transparent'}`,
                    transition: 'all 0.12s',
                  }}
                  onMouseEnter={e => { if (!isActive) e.currentTarget.style.backgroundColor = 'rgba(44,59,77,0.03)' }}
                  onMouseLeave={e => { if (!isActive) e.currentTarget.style.backgroundColor = 'transparent' }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', width: '100%' }}>
                    <div style={{
                      width: '14px', height: '14px', borderRadius: '3px',
                      border: `1.5px solid ${isActive ? '#FFB162' : '#C9C1B1'}`,
                      backgroundColor: isActive ? '#FFB162' : 'transparent',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      flexShrink: 0, transition: 'all 0.12s',
                    }}>
                      {isActive && (
                        <svg width="8" height="8" viewBox="0 0 12 12" fill="none" stroke="#1B2632" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="2 6 5 9 10 3"/>
                        </svg>
                      )}
                    </div>
                    <span style={{
                      fontSize: '12px', fontWeight: isActive ? 500 : 400,
                      color: isActive ? '#2C3B4D' : '#7FA3C0',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1,
                    }}>
                      {tableName}
                    </span>
                  </div>
                  {alias && (
                    <div style={{ paddingLeft: '22px', marginTop: '3px' }}>
                      <span style={{
                        fontSize: '10px', padding: '1px 6px', borderRadius: '3px',
                        backgroundColor: '#1B2632', color: '#C9C1B1',
                        fontWeight: 600, letterSpacing: '0.3px',
                      }}>
                        {alias}
                      </span>
                    </div>
                  )}
                </div>
              )
            })}

            {!state.loading && state.allTables.length === 0 && (
              <div style={{ padding: '20px 14px', fontSize: '12px', color: '#C9C1B1', textAlign: 'center' }}>
                No tables found.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default TableFilterPanel