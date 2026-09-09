import React, { useState, useEffect, useRef } from 'react'
import {
  Download, Loader2, AlertCircle, X, ArrowLeft, FileText, ZoomIn, ZoomOut, RotateCcw, Trash2
} from 'lucide-react'
import axios from 'axios'
import { cn } from './lib/utils'
import { API_BASE } from './config'

// Lazy-loaded on demand so the initial bundle doesn't pay for all three
// libraries unless the user actually opens that file type.
async function loadPdfjs() {
  const pdfjs = await import('pdfjs-dist')
  // Worker must be served statically — see setup note below.
  pdfjs.GlobalWorkerOptions.workerSrc = new URL(
    'pdfjs-dist/build/pdf.worker.min.mjs',
    import.meta.url
  ).toString()
  return pdfjs
}

interface DocumentViewerProps {
  docId: string
  onClose?: () => void
  onDelete?: () => void
  embedded?: boolean
}

interface DocumentMetadata {
  filename: string
  file_extension: string
  category: string
  description: string
  upload_timestamp: string
  file_size: number
}

const PREVIEWABLE_EXTENSIONS = new Set(['pdf', 'docx', 'doc', 'xlsx', 'xls', 'csv', 'txt', 'pptx', 'ppt'])

// Zoom only applies to the canvas-rendered view (real PDFs + converted
// PPTX/PPT, which share the same pdf.js pipeline). Canvases are rendered
// once at BASE_RENDER_SCALE — zooming just resizes the already-rendered
// bitmap via CSS width/height, so it's instant with no re-render flicker.
// The base scale is a bit higher than the old fixed 1.3 so there's some
// headroom before zooming in starts to look soft.
const BASE_RENDER_SCALE = 1.5
const ZOOM_STEP = 0.15
const MIN_ZOOM = 0.5
const MAX_ZOOM = 3
const ZOOMABLE_EXTENSIONS = new Set(['pdf', 'pptx', 'ppt'])

const DocumentViewer: React.FC<DocumentViewerProps> = ({ docId, onClose, onDelete, embedded = false }) => {
  const [metadata, setMetadata] = useState<DocumentMetadata | null>(null)
  const [loading, setLoading] = useState(false)
  const [renderError, setRenderError] = useState<string | null>(null)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [zoom, setZoom] = useState(1)

  const containerRef = useRef<HTMLDivElement>(null)   // for docx-preview target
  const pdfContainerRef = useRef<HTMLDivElement>(null) // for pdf.js canvases

  // Fetch metadata first (filename, extension, etc.) — cheap and fast.
  useEffect(() => {
    setFetchError(null)
    axios.get(`${API_BASE}/api/documents/${docId}`, { withCredentials: true })
      .then(r => {
        if (r.data.success) setMetadata(r.data.metadata)
        else setFetchError('Failed to load document metadata')
      })
      .catch(e => setFetchError(e.response?.data?.detail || 'Failed to load document metadata'))
  }, [docId])

  // Once we know the extension, fetch raw bytes and render appropriately.
  useEffect(() => {
    if (!metadata) return
    // Guards against React 18 StrictMode's dev-only double-invoke of effects:
    // without this, both invocations would fetch and render independently,
    // and with async multi-step rendering (pdf.js renders page-by-page) the
    // two runs can interleave and both end up appending content.
    let cancelled = false

    const ext = metadata.file_extension.toLowerCase()
    setLoading(true)
    setRenderError(null)
    setZoom(1)

    // Plain text stays on the extracted-text endpoint — there's no tabular
    // structure to render, so a <pre> block is the correct preview.
    if (ext === 'txt') {
      axios.get(`${API_BASE}/api/documents/${docId}/content`, { withCredentials: true })
        .then(r => {
          if (cancelled || !containerRef.current) return
          const pre = document.createElement('pre')
          pre.className = 'text-xs text-ink whitespace-pre-wrap break-words font-mono'
          pre.textContent = r.data.content ?? ''
          containerRef.current.innerHTML = ''
          containerRef.current.appendChild(pre)
        })
        .catch(e => { if (!cancelled) setRenderError(e.response?.data?.detail || 'Failed to load document text') })
        .finally(() => { if (!cancelled) setLoading(false) })
      return () => { cancelled = true }
    }

    // PPTX/PPT: converted server-side to PDF (LibreOffice, cached per doc_id
    // — see /api/documents/{id}/preview-pdf), then rendered through the SAME
    // pdf.js pipeline as a real PDF below. No separate slide-rendering code
    // needed — a converted deck IS just a PDF from this point on.
    if (ext === 'pptx' || ext === 'ppt') {
      axios.get(`${API_BASE}/api/documents/${docId}/preview-pdf`, {
        withCredentials: true,
        responseType: 'arraybuffer',
      })
        .then(async (r) => {
          if (cancelled) return
          await renderPdf(r.data as ArrayBuffer, () => cancelled)
        })
        .catch(e => {
          if (!cancelled) {
            setRenderError(
              e.response?.status === 503
                ? 'PDF conversion isn\'t available on this server right now — use download instead.'
                : (e.response?.data?.detail || 'Failed to convert this presentation for preview')
            )
          }
        })
        .finally(() => { if (!cancelled) setLoading(false) })
      return () => { cancelled = true }
    }

    // Everything else needs raw bytes.
    axios.get(`${API_BASE}/api/documents/${docId}/download`, {
      withCredentials: true,
      responseType: 'arraybuffer',
    })
      .then(async (r) => {
        if (cancelled) return
        const buffer: ArrayBuffer = r.data

        if (ext === 'pdf') {
          await renderPdf(buffer, () => cancelled)
        } else if (ext === 'docx' || ext === 'doc') {
          if (cancelled) return
          await renderDocx(buffer)
        } else if (ext === 'xlsx' || ext === 'xls' || ext === 'csv') {
          if (cancelled) return
          await renderXlsx(buffer, ext)
        } else if (!cancelled) {
          setRenderError(`Preview not supported for .${ext} files — use download instead.`)
        }
      })
      .catch(e => { if (!cancelled) setRenderError(e.response?.data?.detail || 'Failed to load document') })
      .finally(() => { if (!cancelled) setLoading(false) })

    return () => { cancelled = true }

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [metadata])

  // Applies zoom to whatever canvases are already rendered — pure CSS
  // resize of the existing bitmap, no pdf.js re-render, so it's instant.
  useEffect(() => {
    const container = pdfContainerRef.current
    if (!container) return
    const canvases = container.querySelectorAll<HTMLCanvasElement>('canvas')
    canvases.forEach(canvas => {
      const nw = Number(canvas.dataset.naturalWidth || canvas.width)
      const nh = Number(canvas.dataset.naturalHeight || canvas.height)
      canvas.style.width = `${nw * zoom}px`
      canvas.style.height = `${nh * zoom}px`
    })
  }, [zoom, metadata])

  const zoomIn    = () => setZoom(z => Math.min(MAX_ZOOM, +(z + ZOOM_STEP).toFixed(2)))
  const zoomOut   = () => setZoom(z => Math.max(MIN_ZOOM, +(z - ZOOM_STEP).toFixed(2)))
  const zoomReset = () => setZoom(1)

  const renderPdf = async (buffer: ArrayBuffer, isCancelled: () => boolean) => {
    if (!pdfContainerRef.current || isCancelled()) return
    pdfContainerRef.current.innerHTML = ''

    const pdfjs = await loadPdfjs()
    if (isCancelled()) return
    const pdf = await pdfjs.getDocument({ data: buffer }).promise
    if (isCancelled()) return

    for (let pageNum = 1; pageNum <= pdf.numPages; pageNum++) {
      if (isCancelled()) return
      const page = await pdf.getPage(pageNum)
      const viewport = page.getViewport({ scale: BASE_RENDER_SCALE })

      const canvas = document.createElement('canvas')
      canvas.width = viewport.width
      canvas.height = viewport.height
      canvas.className = 'shadow-sm border border-border mb-4 mx-auto block'
      // Natural (unscaled) pixel size — the zoom effect reads these to
      // compute displayed size, since canvas.width/height stay fixed once
      // rendered (that's the actual bitmap resolution, not display size).
      canvas.dataset.naturalWidth = String(viewport.width)
      canvas.dataset.naturalHeight = String(viewport.height)
      canvas.style.width = `${viewport.width}px`
      canvas.style.height = `${viewport.height}px`

      const ctx = canvas.getContext('2d')
      if (!ctx) continue

      await page.render({ canvas, canvasContext: ctx, viewport }).promise
      if (isCancelled() || !pdfContainerRef.current) return
      pdfContainerRef.current.appendChild(canvas)
    }
  }

  const renderDocx = async (buffer: ArrayBuffer) => {
    if (!containerRef.current) return
    containerRef.current.innerHTML = ''

    const { renderAsync } = await import('docx-preview')
    await renderAsync(buffer, containerRef.current, undefined, {
      className: 'docx-preview',
      inWrapper: true,
      ignoreWidth: false,
      ignoreHeight: false,
    })
  }

  const renderXlsx = async (buffer: ArrayBuffer, ext: string) => {
    if (!containerRef.current) return
    containerRef.current.innerHTML = ''

    const XLSX = await import('xlsx')
    // CSV is plain text, not a binary workbook format — decode it as text
    // first so SheetJS parses delimiters/rows correctly instead of trying
    // to read it as a binary xlsx/xls container.
    const workbook = ext === 'csv'
      ? XLSX.read(new TextDecoder('utf-8').decode(buffer), { type: 'string' })
      : XLSX.read(buffer, { type: 'array' })

    workbook.SheetNames.forEach((sheetName) => {
      const sheet = workbook.Sheets[sheetName]
      const html = XLSX.utils.sheet_to_html(sheet, { editable: false })

      const wrapper = document.createElement('div')
      wrapper.className = 'mb-6'

      // CSV always parses to a single generically-named sheet — the label
      // adds nothing there, only show it for real multi-sheet workbooks.
      if (ext !== 'csv') {
        const title = document.createElement('div')
        title.className = 'text-xs font-semibold text-ink-muted mb-2 px-1'
        title.textContent = sheetName
        wrapper.appendChild(title)
      }

      const tableWrapper = document.createElement('div')
      tableWrapper.className = 'overflow-x-auto border border-border rounded-lg xlsx-preview'
      tableWrapper.innerHTML = html
      wrapper.appendChild(tableWrapper)

      containerRef.current!.appendChild(wrapper)
    })
  }

  if (fetchError) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-6">
        <AlertCircle size={24} className="text-tertiary" />
        <p className="text-xs text-ink-muted">{fetchError}</p>
        {onClose && (
          <button onClick={onClose} className="btn-sm btn-outline mt-2">
            <ArrowLeft size={12} /> Go Back
          </button>
        )}
      </div>
    )
  }

  if (!metadata) {
    return (
      <div className="flex items-center justify-center h-full gap-2 text-ink-faint">
        <Loader2 size={18} className="animate-spin text-[#2D5A8C]" />
        <span className="text-xs">Loading document…</span>
      </div>
    )
  }

  const ext = metadata.file_extension.toLowerCase()
  const canPreview = PREVIEWABLE_EXTENSIONS.has(ext)

  return (
    <div className="flex flex-col h-full">
      {!embedded && (
      <div className="flex items-start justify-between p-4 border-b border-border shrink-0 bg-surface">
        <div className="flex items-start gap-3 flex-1">
          <FileText size={20} className="text-[#2D5A8C] shrink-0 mt-0.5" />
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold text-ink truncate">{metadata.filename}</h2>
            <p className="text-[11px] text-ink-faint mt-1">
              {metadata.file_extension.toUpperCase()} · {(metadata.file_size / 1024).toFixed(1)} KB
              {metadata.category && ` · ${metadata.category.replace(/_/g, ' ')}`}
            </p>
            {metadata.description && (
              <p className="text-[10px] text-ink-muted mt-1">{metadata.description}</p>
            )}
            <p className="text-[10px] text-ink-faint mt-1">
              {new Date(metadata.upload_timestamp).toLocaleDateString()}
            </p>
          </div>
        </div>
        <div className="flex gap-2 shrink-0">
          <a
            href={`${API_BASE}/api/documents/${docId}/download`}
            target="_blank"
            rel="noopener noreferrer"
            className="p-2 rounded-lg text-ink-faint hover:text-[#2D5A8C] hover:bg-[#2D5A8C]-muted transition-colors"
            title="Open in new tab / download"
          >
            <Download size={16} />
          </a>
          {onDelete && (
            <button
              onClick={onDelete}
              className="p-2 rounded-lg text-ink-faint hover:text-tertiary hover:bg-tertiary-muted transition-colors"
              title="Delete document"
            >
              <Trash2 size={16} />
            </button>
          )}
          {onClose && (
            <button
              onClick={onClose}
              className="p-2 rounded-lg text-ink-faint hover:text-[#2D5A8C] hover:bg-[#2D5A8C]-muted transition-colors"
              title="Close"
            >
              <X size={16} />
            </button>
          )}
        </div>
      </div>
      )}

      <div className="flex-1 relative overflow-hidden">
        <div className="h-full overflow-y-auto p-4 scrollbar-thin scrollbar-thumb-border scrollbar-track-transparent">
          {loading && (
            <div className="flex items-center justify-center gap-2 text-ink-faint py-8">
              <Loader2 size={16} className="animate-spin text-[#2D5A8C]" />
              <span className="text-xs">Rendering preview…</span>
            </div>
          )}

          {renderError && (
            <div className="flex flex-col items-center justify-center gap-3 text-center py-8">
              <AlertCircle size={20} className="text-tertiary" />
              <p className="text-xs text-ink-muted">{renderError}</p>
              <a
                href={`${API_BASE}/api/documents/${docId}/download`}
                target="_blank"
                rel="noopener noreferrer"
                className="btn-sm btn-outline"
              >
                <Download size={12} /> Download instead
              </a>
            </div>
          )}

          {!canPreview && !loading && !renderError && (
            <div className="flex flex-col items-center justify-center gap-3 text-center py-8">
              <p className="text-xs text-ink-muted">
                Preview isn't available for .{ext} files yet.
              </p>
              <a
                href={`${API_BASE}/api/documents/${docId}/download`}
                target="_blank"
                rel="noopener noreferrer"
                className="btn-sm btn-outline"
              >
                <Download size={12} /> Download
              </a>
            </div>
          )}

          {/* PDF (and converted PPTX/PPT) renders into its own container (canvas-per-page) */}
          <div ref={pdfContainerRef} className={(ext === 'pdf' || ext === 'pptx' || ext === 'ppt') ? 'block' : 'hidden'} />

          {/* docx / xlsx / txt / csv all render into this generic container */}
          <div ref={containerRef} className={(ext === 'pdf' || ext === 'pptx' || ext === 'ppt') ? 'hidden' : 'block'} />
        </div>

        {/* Floating zoom toolbar — only for the canvas-rendered view (real PDFs
            and converted PPTX/PPT), and only once content has finished loading
            (avoids resizing pages that haven't been appended yet — see the
            zoom effect above). */}
        {ZOOMABLE_EXTENSIONS.has(ext) && !loading && !renderError && (
          <div className="absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-1 bg-surface border border-border shadow-md rounded-full px-2 py-1.5 z-10">
            <button
              onClick={zoomOut}
              disabled={zoom <= MIN_ZOOM}
              className="p-1.5 rounded-full text-ink-faint hover:text-[#2D5A8C] hover:bg-[#2D5A8C]-muted transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              title="Zoom out"
            >
              <ZoomOut size={15} />
            </button>
            <button
              onClick={zoomReset}
              className="text-[11px] font-medium text-ink-muted w-11 text-center hover:text-[#2D5A8C] transition-colors"
              title="Reset zoom"
            >
              {Math.round(zoom * 100)}%
            </button>
            <button
              onClick={zoomIn}
              disabled={zoom >= MAX_ZOOM}
              className="p-1.5 rounded-full text-ink-faint hover:text-[#2D5A8C] hover:bg-[#2D5A8C]-muted transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              title="Zoom in"
            >
              <ZoomIn size={15} />
            </button>
            {zoom !== 1 && (
              <button
                onClick={zoomReset}
                className="p-1.5 rounded-full text-ink-faint hover:text-[#2D5A8C] hover:bg-[#2D5A8C]-muted transition-colors ml-0.5 border-l border-border pl-2"
                title="Reset to 100%"
              >
                <RotateCcw size={14} />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default DocumentViewer