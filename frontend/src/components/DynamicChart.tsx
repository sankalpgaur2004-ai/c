import React from 'react'
import Plot from 'react-plotly.js'

// ── Circulants palette ────────────────────────────────────────────
const P = {
  navy:        '#2D5A8C',
  navyLight:   '#3A6BA8',
  navyMuted:   'rgba(45, 90, 140, 0.1)',
  maroon:      '#8B1A2B',
  maroonLight: '#B02235',
  white:       '#FFFFFF',
  surface:     '#F5F6FA',
  border:      '#E2E6F0',
  textPrimary: '#0F172A',
  textMid:     '#4B5577',
  textMuted:   '#8B93AD',
}

// ── Trace color sequence — navy blue + maroon + complementary tones ────
const TRACE_COLORS = [
  '#2D5A8C', // Navy blue
  '#8B1A2B', // Maroon
  '#3A6BA8', // Navy light
  '#B02235', // Maroon light
  '#4A7BA8', // Navy variant
  '#C45C70', // Rose
  '#5A8BC0', // Navy muted
  '#A31F32', // Maroon hover
  '#6A9BD8', // Navy bright
  '#D4808F', // Blush
]

interface Props {
  chartData: Record<string, any> | null
  chartType: string
}

const DynamicChart: React.FC<Props> = ({ chartData, chartType }) => {
  if (!chartData || chartType === 'none' || chartType === 'table') return null

  const rawTraces: any[] = chartData.data || []
  const plotlyLayout     = chartData.layout || {}

  /* ── Apply palette to each trace ── */
  const plotlyData = rawTraces.map((trace, i) => {
    const color = TRACE_COLORS[i % TRACE_COLORS.length]

    if (trace.type === 'pie') {
      return {
        ...trace,
        marker: {
          ...trace.marker,
          colors: TRACE_COLORS,
          line: { color: P.white, width: 2 },
        },
        textfont: { color: P.white },
      }
    }

    if (trace.type === 'bar' || trace.type === 'histogram') {
      return {
        ...trace,
        marker: {
          ...trace.marker,
          color,
          line: { color: P.white, width: 0.8 },
          opacity: 0.9,
        },
      }
    }

    if (trace.type === 'scatter' || trace.type === 'scattergl') {
      return {
        ...trace,
        line:   { ...trace.line,   color, width: trace.line?.width ?? 2.5 },
        marker: { ...trace.marker, color, size: trace.marker?.size ?? 6, line: { color: P.white, width: 1.5 } },
      }
    }

    return { ...trace, marker: { ...trace.marker, color } }
  })

  /* ── Layout override ── */
  const layout = {
    ...plotlyLayout,
    paper_bgcolor: P.surface,
    plot_bgcolor:  P.white,
    font: {
      color:  P.textPrimary,
      family: 'DM Sans, system-ui, sans-serif',
      size:   12,
    },
    margin: { t: 48, b: 52, l: 56, r: 28 },
    colorway: TRACE_COLORS,

    xaxis: {
      ...plotlyLayout.xaxis,
      gridcolor:     P.border,
      linecolor:     P.border,
      tickcolor:     P.border,
      tickfont:      { color: P.textMuted, size: 11 },
      zerolinecolor: P.border,
      title: {
        ...plotlyLayout.xaxis?.title,
        font: { color: P.textMid, size: 12 },
      },
    },

    yaxis: {
      ...plotlyLayout.yaxis,
      gridcolor:     P.border,
      linecolor:     P.border,
      tickcolor:     P.border,
      tickfont:      { color: P.textMuted, size: 11 },
      zerolinecolor: P.border,
      title: {
        ...plotlyLayout.yaxis?.title,
        font: { color: P.textMid, size: 12 },
      },
    },

    legend: {
      ...plotlyLayout.legend,
      bgcolor:     P.surface,
      bordercolor: P.border,
      borderwidth: 1,
      font:        { color: P.textMid, size: 11 },
    },

    title: plotlyLayout.title ? {
      ...plotlyLayout.title,
      font: { color: P.navy, size: 14, family: 'DM Sans, sans-serif' },
    } : undefined,
  }

  return (
    <div style={{
      backgroundColor: P.surface,
      borderRadius:    '12px',
      padding:         '12px',
      border:          `1px solid ${P.border}`,
      width:           '100%',
      overflow:        'hidden',
    }}>
      <Plot
        data={plotlyData}
        layout={{ ...layout, autosize: true, height: chartType === 'indicator' ? 260 : 420, width: undefined }}
        config={{
          responsive:              true,
          displayModeBar:          true,
          modeBarButtonsToRemove:  ['lasso2d', 'select2d'],
          toImageButtonOptions:    { format: 'png', scale: 2 },
        }}
        style={{ width: '100%', height: chartType === 'indicator' ? '260px' : '420px' }}
        useResizeHandler
      />
    </div>
  )
}

export default DynamicChart