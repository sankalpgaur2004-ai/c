import React from 'react'

interface Props {
  data: Record<string, any>[]
  columns: string[]
}

const DataTable: React.FC<Props> = ({ data, columns }) => {
  if (!data || data.length === 0 || !columns || columns.length === 0) {
    return (
      <div className="py-4 text-center text-sm text-ink-faint">
        No data returned
      </div>
    )
  }

  const displayData = data.slice(0, 100)
  const truncated   = data.length > 100

  return (
    <div className="mt-3">
      {/* Row count */}
      <p className="text-[11px] text-ink-faint font-medium mb-2">
        {data.length.toLocaleString()} row{data.length !== 1 ? 's' : ''}
        {truncated && ' — displaying first 100'}
      </p>

      {/* Table wrapper */}
      <div className="overflow-auto max-h-72 rounded-xl border border-border">
        <table className="w-full border-collapse text-xs" style={{ minWidth: '100%', tableLayout: 'auto' }}>
          <thead>
            <tr className="sticky top-0 z-10">
              {columns.map(col => (
                <th
                  key={col}
                  className="px-4 py-2.5 text-left text-white font-semibold uppercase tracking-wide whitespace-nowrap border-r border-[#2D5A8C]-light last:border-r-0"
                  style={{ backgroundColor: '#2D5A8C', fontSize: '10px', letterSpacing: '0.5px' }}
                >
                  {col.replace(/_/g, ' ')}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {displayData.map((row, i) => (
              <tr
                key={i}
                style={{ backgroundColor: i % 2 === 0 ? '#FFFFFF' : '#F5F6FA' }}
                onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'rgba(45, 90, 140, 0.08)')}
                onMouseLeave={e => (e.currentTarget.style.backgroundColor = i % 2 === 0 ? '#FFFFFF' : '#F5F6FA')}
                className="border-b border-border transition-colors"
              >
                {columns.map(col => {
                  const val      = row[col]
                  const isNum    = typeof val === 'number'
                  const isNull   = val === null || val === undefined
                  return (
                    <td
                      key={col}
                      className="px-4 py-2 border-r border-border last:border-r-0 whitespace-nowrap overflow-hidden text-ellipsis"
                      style={{
                        color: isNull ? '#8B93AD' : '#0F172A',
                        fontVariantNumeric: isNum ? 'tabular-nums' : undefined,
                        minWidth: '100px',
                        maxWidth: '220px',
                      }}
                    >
                      {isNull ? '—' : isNum ? val.toLocaleString() : String(val)}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default DataTable