import React, { useState, useRef, useCallback, useEffect } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from './lib/utils'

/* ── Types ──────────────────────────────────────────────────────── */

export type ResizeEdge = 'right' | 'left' | 'none'

interface ResizablePanelProps {
  /** Initial width in pixels */
  defaultWidth: number
  /** Minimum width when expanded */
  minWidth: number
  /** Maximum width */
  maxWidth: number
  /** Which edge has the drag handle */
  resizeEdge?: ResizeEdge
  /** Whether this panel can be minimised */
  collapsible?: boolean
  /** Collapsed icon shown in the strip when minimised */
  collapsedIcon?: React.ReactNode
  /** Collapsed label shown on hover in the strip */
  collapsedLabel?: string
  /** Panel header content */
  header?: React.ReactNode
  /** Panel body content */
  children: React.ReactNode
  className?: string
  /** Called whenever width changes (for sibling panels to react) */
  onWidthChange?: (width: number) => void
}

/* ════════════════════════════════════════════════════════════════ */

const COLLAPSED_WIDTH = 48   // icon strip width when minimised

const ResizablePanel: React.FC<ResizablePanelProps> = ({
  defaultWidth,
  minWidth,
  maxWidth,
  resizeEdge = 'none',
  collapsible = false,
  collapsedIcon,
  collapsedLabel,
  header,
  children,
  className,
  onWidthChange,
}) => {
  const [width, setWidth]         = useState(defaultWidth)
  const [collapsed, setCollapsed] = useState(false)
  const [dragging, setDragging]   = useState(false)
  const panelRef  = useRef<HTMLDivElement>(null)
  const startX    = useRef(0)
  const startW    = useRef(0)

  /* ── Notify parent of width changes ── */
  useEffect(() => {
    onWidthChange?.(collapsed ? COLLAPSED_WIDTH : width)
  }, [width, collapsed, onWidthChange])

  /* ── Mouse drag handlers ── */
  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    startX.current = e.clientX
    startW.current = width
    setDragging(true)
  }, [width])

  useEffect(() => {
    if (!dragging) return

    const onMove = (e: MouseEvent) => {
      const delta = resizeEdge === 'right'
        ? e.clientX - startX.current
        : startX.current - e.clientX
      const next = Math.min(maxWidth, Math.max(minWidth, startW.current + delta))
      setWidth(next)
    }

    const onUp = () => setDragging(false)

    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [dragging, resizeEdge, minWidth, maxWidth])

  /* ── Collapse / expand ── */
  const toggleCollapse = () => setCollapsed(c => !c)

  /* ── Drag handle element ── */
  const DragHandle = (
    <div
      onMouseDown={onMouseDown}
      className={cn(
        'absolute top-0 bottom-0 w-1 z-10 group',
        'hover:bg-[#2D5A8C]/20 active:bg-[#2D5A8C]/40',
        'transition-colors duration-150 cursor-col-resize',
        resizeEdge === 'right' ? 'right-0' : 'left-0',
        dragging && 'bg-[#2D5A8C]/30',
      )}
    >
      {/* Visual line */}
      <div className={cn(
        'absolute inset-y-0 w-px bg-border group-hover:bg-[#2D5A8C]/40',
        'transition-colors duration-150',
        resizeEdge === 'right' ? 'right-0' : 'left-0',
      )} />
    </div>
  )

  /* ── Collapsed strip ── */
  if (collapsed) {
    return (
      <div
        style={{ width: COLLAPSED_WIDTH }}
        className={cn(
          'relative flex flex-col items-center shrink-0',
          'bg-white border-r border-border',
          'transition-all duration-200',
        )}
      >
        {/* Expand button */}
        <button
          onClick={toggleCollapse}
          title={collapsedLabel ?? 'Expand'}
          className={cn(
            'w-full flex flex-col items-center gap-3 pt-4 pb-3',
            'text-ink-faint hover:text-[#2D5A8C] hover:bg-[#2D5A8C]-muted',
            'transition-colors duration-150',
          )}
        >
          <span className="text-[#2D5A8C]">{collapsedIcon}</span>
          {resizeEdge === 'right'
            ? <ChevronRight size={14} />
            : <ChevronLeft size={14} />
          }
        </button>
      </div>
    )
  }

  /* ── Expanded panel ── */
  return (
    <div
      ref={panelRef}
      style={{ width, minWidth: width, maxWidth: width }}
      className={cn(
        'relative flex flex-col shrink-0 overflow-hidden h-full',
        'bg-white',
        dragging && 'select-none',
        className,
      )}
    >
      {/* Drag handle */}
      {resizeEdge !== 'none' && DragHandle}

      {/* Header */}
      {header && (
        <div className="panel-header shrink-0">
          <div className="flex-1 min-w-0">{header}</div>

          {/* Collapse button */}
          {collapsible && (
            <button
              onClick={toggleCollapse}
              title="Collapse panel"
              className={cn(
                'ml-2 p-1 rounded-md text-ink-faint shrink-0',
                'hover:bg-[#2D5A8C]-muted hover:text-[#2D5A8C]',
                'transition-colors duration-150',
              )}
            >
              {resizeEdge === 'right'
                ? <ChevronLeft size={14} />
                : <ChevronRight size={14} />
              }
            </button>
          )}
        </div>
      )}

      {/* Body — min-h-0 prevents flex overflow */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {children}
      </div>
    </div>
  )
}

export default ResizablePanel