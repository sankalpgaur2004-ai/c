# document_manager.py
"""
DocumentManager — parse, store, and retrieve documents for RAG.

PPTX extraction strategy (inspired by RAGAlchamy + our own research):

  PRIMARY: shape.chart.part.chart_workbook.xlsx_part.blob
    → pd.read_excel() → clean DataFrame with real labels and values.
    This is what python-pptx exposes from the embedded Excel workbook.
    Works for any .xlsx-backed chart (line, bar, column, area, scatter…).

  FALLBACK A (pie/donut with .xlsb binary workbook):
    Labels are rendered as floating text boxes on the slide.
    → Spatial proximity matching pairs "18 (19%)" value boxes with
      their nearest label boxes to reconstruct "Other: 18 (19%)".

  FALLBACK B (any chart where both above fail):
    Raw XML parsing of c:ser/c:cat/c:val elements via ZIP inspection.

  GRANULARITY: Slide-level (RAGAlchamy approach).
    Each slide becomes one document block so chart data, slide title,
    table headers, and narrative text are always stored together.
    No chunk boundary can split a series from its chart title.
"""

import csv
import hashlib
import io
import json
import logging
import math
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import pdfplumber
from docx import Document as DocxDocument
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

logger = logging.getLogger(__name__)

# ── XML namespaces (fallback XML extractor only) ──────────────────────────────
_NS_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"


# ══════════════════════════════════════════════════════════════════════════════
# Slide-level text helpers
# ══════════════════════════════════════════════════════════════════════════════

def _clean(text: str) -> str:
    return text.replace("\n", " ").replace("\x0b", " ").replace("\r", " ").strip()


def _slide_title(slide, fallback: str) -> str:
    """Best short title from a slide's title placeholder or first short text."""
    try:
        t = slide.shapes.title
        if t and t.text.strip():
            return _clean(t.text)
    except Exception:
        pass
    SKIP = ("source:", "note:", "*", "potentially", "only ", "across ", "81%")
    for shape in slide.shapes:
        if hasattr(shape, "text") and shape.text.strip():
            t = _clean(shape.text)
            if 3 < len(t) < 100 and not any(t.lower().startswith(s) for s in SKIP):
                return t
    return fallback


# ══════════════════════════════════════════════════════════════════════════════
# PRIMARY chart extractor — xlsx_part.blob → DataFrame
# ══════════════════════════════════════════════════════════════════════════════

def _fmt_val(v) -> str:
    """Format a numeric value cleanly."""
    try:
        n = float(v)
        return str(int(n)) if n == int(n) else str(round(n, 2))
    except (ValueError, TypeError):
        return str(v)


def _fmt_period(idx) -> str:
    """Convert a DataFrame index value to a human-readable period string."""
    if hasattr(idx, "strftime"):
        return idx.strftime("%b-%y")
    s = str(idx).strip()
    # ISO timestamp → Month-YY
    m = re.match(r"(\d{4})-(\d{2})-\d{2}", s)
    if m:
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), 1)
            return d.strftime("%b-%y")
        except Exception:
            pass
    return s


def _df_to_fact_lines(df: pd.DataFrame, chart_title: str = "") -> str:
    """
    Convert a chart DataFrame (index=periods, columns=series) into
    RAG-ready text lines:

      SeriesName for Period: Value        ← one per data point
      SeriesName summary: P1=V1, P2=V2   ← full series on one line

    Weekly data (multiple rows per month-label) is summed to monthly.
    """
    # Drop fully-empty rows/cols and unnamed columns
    df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")
    df = df[[c for c in df.columns if not str(c).startswith("Unnamed")]]
    if df.empty:
        return ""

    lines: List[str] = []
    if chart_title:
        lines.append(f"CHART TITLE: {chart_title}")

    for col in df.columns:
        series_name = _clean(str(col))
        if not series_name or series_name.lower() in ("nan", "none", ""):
            continue

        raw_vals = df[col].dropna()
        if raw_vals.empty:
            continue

        # Build period→value mapping (aggregate duplicates by summing)
        agg: OrderedDict = OrderedDict()
        for idx, val in raw_vals.items():
            period = _fmt_period(idx)
            try:
                agg.setdefault(period, []).append(float(val))
            except (ValueError, TypeError):
                agg.setdefault(period, []).append(val)

        # Emit one line per period
        summary_pairs: List[str] = []
        for period, bucket in agg.items():
            try:
                total = sum(float(x) for x in bucket)
                fval = _fmt_val(total)
            except (ValueError, TypeError):
                fval = str(bucket[-1])
            lines.append(f"{series_name} for {period}: {fval}")
            summary_pairs.append(f"{period}={fval}")

        lines.append(f"{series_name} summary: {', '.join(summary_pairs)}")

    return "\n".join(lines)


def _extract_chart_via_blob(shape) -> str:
    """
    PRIMARY: Use python-pptx's chart workbook API to read the embedded
    Excel file and convert it to structured fact lines.
    Returns "" if the workbook is empty/unreadable (use fallback).
    """
    try:
        blob = shape.chart.part.chart_workbook.xlsx_part.blob
        if not blob:
            return ""

        chart_title = ""
        if shape.chart.has_title:
            try:
                chart_title = _clean(shape.chart.chart_title.text_frame.text)
            except Exception:
                pass

        # Try standard xlsx first, then pyxlsb for binary workbooks
        df = None
        for engine in (None, "pyxlsb"):
            try:
                kwargs = {"index_col": 0}
                if engine:
                    kwargs["engine"] = engine
                df = pd.read_excel(io.BytesIO(blob), **kwargs)
                break
            except Exception:
                continue

        if df is None or df.empty:
            return ""

        result = _df_to_fact_lines(df, chart_title)
        return result

    except Exception as e:
        logger.debug(f"xlsx_part.blob extraction failed: {e}")
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# FALLBACK A — pie/donut with binary workbook: spatial text-box matching
# ══════════════════════════════════════════════════════════════════════════════

_PCT_RE = re.compile(r"^(\d+)\s*\(([\d.]+)%\)$")

_PIE_SKIP = (
    "source:", "note:", "potentially", "of the ", "hub",
    "discontinuation reasons", "cancellation reasons",
    "935", "773", "345", "68", "94", "422", "144",
    "patients'", "patient disposition",
)


def _extract_pie_from_shapes(slide, chart_shape) -> str:
    """
    FALLBACK A: Extract pie/donut data from floating slide text boxes.

    PowerPoint renders pie slice labels as individual text boxes positioned
    visually adjacent to each slice. This function pairs "N (X%)" value
    boxes with their nearest pure-label boxes using Euclidean distance,
    then emits "Label: N (X%)" lines.
    """
    def center(s):
        return (s.left + s.width / 2, s.top + s.height / 2)

    def dist(a, b):
        return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)

    chart_cx, chart_cy = center(chart_shape)
    MAX_DIST = 4_500_000  # ~5 inches in EMU

    value_items: List[dict] = []
    label_items: List[dict] = []

    for shape in slide.shapes:
        if shape == chart_shape:
            continue
        if not (hasattr(shape, "text") and shape.text.strip()):
            continue
        raw = _clean(shape.text)
        if not raw or len(raw) > 80:
            continue
        if any(raw.lower().startswith(p) for p in _PIE_SKIP):
            continue

        scx, scy = center(shape)
        if dist((scx, scy), (chart_cx, chart_cy)) > MAX_DIST:
            continue

        m = _PCT_RE.match(raw)
        if m:
            value_items.append({"text": raw, "count": m.group(1),
                                 "pct": m.group(2), "cx": scx, "cy": scy})
        else:
            label_items.append({"text": raw, "cx": scx, "cy": scy})

    if not value_items:
        return ""

    # Pair each value box with its nearest unused label box
    used: set = set()
    pairs: List[tuple] = []
    for v in value_items:
        best_d, best_l, best_i = float("inf"), None, None
        for i, l in enumerate(label_items):
            if i in used:
                continue
            d = dist((v["cx"], v["cy"]), (l["cx"], l["cy"]))
            if d < best_d:
                best_d, best_l, best_i = d, l["text"], i
        if best_l and best_d < 3_000_000:
            used.add(best_i)
            pairs.append((best_l, v["count"], v["pct"]))
        else:
            pairs.append(("Segment", v["count"], v["pct"]))

    if not pairs:
        return ""

    lines = [f"{label}: {count} ({pct}%)"
             for label, count, pct in sorted(pairs, key=lambda x: -int(x[2]))]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# FALLBACK B — raw XML via ZIP (handles any chart type without workbook)
# ══════════════════════════════════════════════════════════════════════════════

def _excel_date(serial: str, fmt: str = "") -> str:
    try:
        n = float(serial)
        if not (35000 < n < 60000):
            return serial
        d = datetime(1899, 12, 30) + timedelta(days=int(n))
        if any(x in fmt.lower() for x in ("mmm", "m/d", "yy")):
            return d.strftime("%b-%y")
        return d.strftime("%b %d, %Y")
    except Exception:
        return serial


def _read_xml_cache(node: Optional[ET.Element], fmt: str = "") -> List[str]:
    """Read strCache, numCache, or multiLvlStrCache from a cat/val node."""
    if node is None:
        return []

    def clean(t): return t.replace("\n", " ").replace("\x0b", " ").strip()

    # strCache
    sc = node.find(f"{{{_NS_C}}}strRef/{{{_NS_C}}}strCache")
    if sc is not None:
        return [clean(p.find(f"{{{_NS_C}}}v").text)
                for p in sc.findall(f"{{{_NS_C}}}pt")
                if p.find(f"{{{_NS_C}}}v") is not None and p.find(f"{{{_NS_C}}}v").text]

    # numCache (may be date serials)
    nc = node.find(f"{{{_NS_C}}}numRef/{{{_NS_C}}}numCache")
    if nc is not None:
        fc_elem = nc.find(f"{{{_NS_C}}}formatCode")
        cache_fmt = (fc_elem.text if fc_elem is not None and fc_elem.text else "") or fmt
        raw = [p.find(f"{{{_NS_C}}}v").text for p in nc.findall(f"{{{_NS_C}}}pt")
               if p.find(f"{{{_NS_C}}}v") is not None and p.find(f"{{{_NS_C}}}v").text]
        if not raw:
            return []
        try:
            if 35000 < float(raw[0]) < 60000:
                return [_excel_date(v, cache_fmt) for v in raw]
        except (ValueError, TypeError):
            pass
        return raw

    # multiLvlStrCache
    ml = node.find(f"{{{_NS_C}}}multiLvlStrRef/{{{_NS_C}}}multiLvlStrCache")
    if ml is not None:
        levels = ml.findall(f"{{{_NS_C}}}lvl")
        if not levels:
            return []
        all_lvl = [[p.find(f"{{{_NS_C}}}v").text or ""
                    for p in lvl.findall(f"{{{_NS_C}}}pt")
                    if p.find(f"{{{_NS_C}}}v") is not None]
                   for lvl in levels]
        max_len = max(len(l) for l in all_lvl)
        return [" | ".join(l[i] for l in all_lvl if i < len(l) and l[i]).strip()
                for i in range(max_len)]

    return []


def _extract_chart_via_xml(file_path: str, chart_num: int) -> str:
    """FALLBACK B: Parse chart XML directly from the PPTX ZIP."""
    SCATTER = {"scatterChart", "bubbleChart"}
    ALL_TYPES = {
        "lineChart", "barChart", "bar3DChart", "pieChart", "pie3DChart",
        "doughnutChart", "areaChart", "area3DChart", "scatterChart",
        "bubbleChart", "radarChart", "stockChart", "surfaceChart",
    }
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            cp = f"ppt/charts/chart{chart_num}.xml"
            if cp not in zf.namelist():
                return ""
            root = ET.fromstring(zf.read(cp).decode("utf-8"))

        # axis fmt hint
        fmt = ""
        for ax_tag in (f"{{{_NS_C}}}dateAx", f"{{{_NS_C}}}catAx"):
            ax = root.find(f".//{ax_tag}")
            if ax is not None:
                nf = ax.find(f"{{{_NS_C}}}numFmt")
                if nf is not None:
                    fc = nf.get("formatCode", "")
                    if fc and fc not in ("General", "0", ""):
                        fmt = fc
                        break

        # chart type
        chart_type = "unknown"
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag in ALL_TYPES:
                chart_type = tag
                break
        is_scatter = chart_type in SCATTER

        lines: List[str] = []
        # title
        for tp in (
            f".//{{{_NS_C}}}title/{{{_NS_C}}}tx/{{{_NS_C}}}rich/{{{_NS_A}}}p/{{{_NS_A}}}r/{{{_NS_A}}}t",
            f".//{{{_NS_C}}}title//{{{_NS_A}}}t",
        ):
            e = root.find(tp)
            if e is not None and e.text and e.text.strip():
                lines.append(f"CHART TITLE: {_clean(e.text)}")
                break

        g_cat = root.find(f".//{{{_NS_C}}}{'xVal' if is_scatter else 'cat'}")
        global_cats = _read_xml_cache(g_cat, fmt)

        for idx, ser in enumerate(root.findall(f".//{{{_NS_C}}}ser")):
            # series name
            sname = f"Series {idx+1}"
            for sp in (
                f".//{{{_NS_C}}}tx/{{{_NS_C}}}strRef/{{{_NS_C}}}strCache/{{{_NS_C}}}pt/{{{_NS_C}}}v",
                f".//{{{_NS_C}}}tx/{{{_NS_C}}}rich/{{{_NS_A}}}p/{{{_NS_A}}}r/{{{_NS_A}}}t",
            ):
                e = ser.find(sp)
                if e is not None and e.text and e.text.strip():
                    sname = _clean(e.text)
                    break

            cat_node = ser.find(f"{{{_NS_C}}}{'xVal' if is_scatter else 'cat'}")
            cats = _read_xml_cache(cat_node, fmt) or global_cats
            val_node = ser.find(f"{{{_NS_C}}}{'yVal' if is_scatter else 'val'}")
            raw_vals = _read_xml_cache(val_node, fmt)

            if not raw_vals:
                continue

            vals = [_fmt_val(v) for v in raw_vals]

            if cats and len(cats) == len(vals):
                seen: OrderedDict = OrderedDict()
                for cat, val in zip(cats, vals):
                    try:
                        seen.setdefault(cat, []).append(float(val))
                    except (ValueError, TypeError):
                        seen.setdefault(cat, []).append(val)
                has_dupes = any(len(v) > 1 for v in seen.values())
                pairs: List[str] = []
                for cat, bucket in seen.items():
                    try:
                        total = sum(float(x) for x in bucket)
                        fval = _fmt_val(total)
                    except (ValueError, TypeError):
                        fval = str(bucket[-1])
                    lines.append(f"{sname} for {cat}: {fval}")
                    pairs.append(f"{cat}={fval}")
                lines.append(f"{sname} summary: {', '.join(pairs)}")
            elif cats:
                for i, val in enumerate(vals):
                    lines.append(f"{sname} for {cats[i] if i < len(cats) else f'Point {i+1}'}: {val}")
            else:
                lines.append(f"{sname}: {', '.join(vals)}")

        return "\n".join(lines)

    except Exception as e:
        logger.debug(f"XML fallback failed for chart {chart_num}: {e}")
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# DocumentManager
# ══════════════════════════════════════════════════════════════════════════════

def _nearest_title(slide, chart_shape, max_dist_inches: float = 4.0) -> str:
    """
    Find the nearest short title-like text shape to a chart.

    Many slides have multiple charts with section-level titles (e.g.
    "Discontinuation Reasons" / "Cancellation Reasons") stored as
    separate AUTO_SHAPE text boxes rather than embedded chart titles.
    This returns the closest one so each chart block gets the right label.

    Skips: long prose, source notes, percentage/number fragments,
           the chart's own data labels.
    """
    import math
    MAX_EMU = int(max_dist_inches * 914400)
    SKIP = ("source:", "note:", "potentially", "*", "of the ",
            "across ", "81%", "only ", "as we ")
    PCT_RE = re.compile(r"^\d+\s*\(?[\d.]+%?\)?$")

    def center(s):
        return (s.left + s.width / 2, s.top + s.height / 2)

    chart_cx, chart_cy = center(chart_shape)

    best_text = ""
    best_dist = float("inf")

    for shape in slide.shapes:
        if shape == chart_shape:
            continue
        if not (hasattr(shape, "text") and shape.text.strip()):
            continue
        text = _clean(shape.text)
        if not text or len(text) > 80 or len(text) < 4:
            continue
        if any(text.lower().startswith(s) for s in SKIP):
            continue
        if PCT_RE.match(text):
            continue

        scx, scy = center(shape)
        d = math.sqrt((scx - chart_cx) ** 2 + (scy - chart_cy) ** 2)
        if d < best_dist and d < MAX_EMU:
            best_dist = d
            best_text = text

    return best_text



class DocumentManager:
    """Parse, store, and retrieve documents for RAG ingestion."""

    def __init__(self, storage_path: str = None):
        if storage_path is None:
            backend_dir = os.path.dirname(os.path.abspath(__file__))
            storage_path = os.path.join(backend_dir, "data", "documents")
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.metadata_file = self.storage_path / "metadata.json"
        self.metadata = self._load_metadata()
        logger.info(f"DocumentManager: {self.storage_path}")

    # ── Metadata ──────────────────────────────────────────────────────────────

    def _load_metadata(self) -> Dict[str, Any]:
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Metadata load failed: {e}")
        return {}

    def _save_metadata(self):
        try:
            with open(self.metadata_file, "w", encoding="utf-8") as f:
                json.dump(self.metadata, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Metadata save failed: {e}")

    def _generate_doc_id(self, filename: str, content: str) -> str:
        h = hashlib.md5(content.encode()).hexdigest()[:8]
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        safe = "".join(c if c.isalnum() else "_" for c in filename)
        return f"{safe}_{ts}_{h}"

    # ── PDF ───────────────────────────────────────────────────────────────────

    def parse_pdf(self, file_path: str) -> str:
        parts: List[str] = []
        try:
            with pdfplumber.open(file_path) as pdf:
                for page_num, page in enumerate(pdf.pages, 1):
                    pg = [f"PAGE {page_num}"]
                    text = page.extract_text()
                    if text and text.strip():
                        pg.append(text.strip())
                    for ti, table in enumerate(page.extract_tables() or []):
                        rows = [" | ".join(str(c).strip() if c else "" for c in row)
                                for row in table if any(c and str(c).strip() for c in row)]
                        if rows:
                            pg.append(f"[TABLE {ti+1} — Page {page_num}]\n" + "\n".join(rows))
                    parts.append("\n\n".join(pg))
            return "\n\n".join(parts)
        except Exception as e:
            logger.error(f"PDF parse failed: {e}")
            raise ValueError(f"PDF parsing failed: {e}")

    # ── DOCX ──────────────────────────────────────────────────────────────────

    def parse_docx(self, file_path: str) -> str:
        try:
            doc = DocxDocument(file_path)
            parts: List[str] = []
            paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
            if paras:
                parts.append("\n\n".join(paras))
            for ti, table in enumerate(doc.tables):
                rows = [" | ".join(c.text.strip() for c in row.cells)
                        for row in table.rows if any(c.text.strip() for c in row.cells)]
                if rows:
                    parts.append(f"[TABLE {ti+1}]\n" + "\n".join(rows))
            return "\n\n".join(parts)
        except Exception as e:
            logger.error(f"DOCX parse failed: {e}")
            raise ValueError(f"DOCX parsing failed: {e}")

    # ── PPTX ──────────────────────────────────────────────────────────────────

    def parse_pptx(self, file_path: str) -> str:
        """
        Extract all content from a PPTX, one block per slide.

        Chart extraction priority:
          1. xlsx_part.blob → pandas DataFrame  (most reliable)
          2. Spatial text-box matching          (pie/donut with binary workbook)
          3. Raw XML via ZIP                    (any other chart)

        Each slide block is self-contained: slide title + all text +
        all table data + all chart data. RAG retrieval at slide level
        ensures chart series are never split from their chart title.
        """
        try:
            prs = Presentation(file_path)
        except Exception as e:
            raise ValueError(f"PPTX open failed: {e}")

        slide_blocks: List[str] = []
        chart_counter = 0

        for slide_num, slide in enumerate(prs.slides, 1):
            stitle = _slide_title(slide, f"Slide {slide_num}")
            lines: List[str] = [
                "=" * 60,
                f"SLIDE {slide_num} — {stitle}",
                "=" * 60,
            ]

            for shape in slide.shapes:
                try:
                    # ── TABLE ──────────────────────────────────────────────
                    if shape.shape_type == MSO_SHAPE_TYPE.TABLE:
                        tlines = self._extract_table(shape, stitle)
                        lines.extend(tlines)

                    # ── CHART ──────────────────────────────────────────────
                    elif shape.shape_type == MSO_SHAPE_TYPE.CHART:
                        chart_counter += 1
                        chart_text = ""

                        # 1. Primary: xlsx_part.blob
                        chart_text = _extract_chart_via_blob(shape)

                        # 2. Fallback A: spatial text-box matching (pie/donut)
                        if not chart_text:
                            chart_text = _extract_pie_from_shapes(slide, shape)

                        # 3. Fallback B: raw XML
                        if not chart_text:
                            chart_text = _extract_chart_via_xml(file_path, chart_counter)

                        if chart_text:
                            # Use the nearest title-like text as label so
                            # "Discontinuation Reasons" and "Cancellation Reasons"
                            # are never both labelled with just the slide title
                            chart_label = _nearest_title(slide, shape) or stitle
                            lines.append(f"\n[CHART {chart_counter} — {chart_label}]")
                            lines.append(chart_text)
                            logger.info(
                                f"Slide {slide_num} chart {chart_counter}: "
                                f"{len(chart_text)} chars"
                            )

                    # ── TEXT ───────────────────────────────────────────────
                    elif hasattr(shape, "text") and shape.text.strip():
                        text = _clean(shape.text)
                        # Skip pure numeric / percent fragments
                        if re.match(r"^[\d.]+$", text):
                            continue
                        if re.match(r"^\([\d.]+%\)$", text):
                            continue
                        lines.append(text)

                except Exception as e:
                    logger.debug(f"Shape error slide {slide_num}: {e}")

            if len(lines) > 3:
                slide_blocks.append("\n".join(lines))

        if not slide_blocks:
            logger.warning(f"No content from {file_path}")
            return ""

        return "\n\n".join(slide_blocks)

    def _extract_table(self, shape, slide_title: str) -> List[str]:
        """Extract table with self-contained rows (header prepended to each data row)."""
        try:
            table = shape.table
            if not table.rows:
                return []
            all_rows = []
            for row in table.rows:
                cells = [_clean(cell.text) if hasattr(cell, "text") else ""
                         for cell in row.cells]
                if any(cells):
                    all_rows.append(cells)
            if not all_rows:
                return []

            header = " | ".join(all_rows[0])
            result = [f"\n[TABLE: {slide_title}]", header]
            for row in all_rows[1:]:
                result.append(f"{header}\n{' | '.join(row)}")
            result.append("\n[FULL TABLE]\n" +
                          "\n".join(" | ".join(r) for r in all_rows))
            return result
        except Exception as e:
            logger.debug(f"Table extraction failed: {e}")
            return []

    # ── CSV ───────────────────────────────────────────────────────────────────

    def parse_csv(self, file_path: str) -> str:
        for enc in ("utf-8", "latin-1", "cp1252"):
            try:
                with open(file_path, "r", encoding=enc) as f:
                    rows = [r for r in csv.reader(f) if any(c.strip() for c in r)]
                if not rows:
                    return ""
                return "[TABLE FROM CSV]\n" + "\n".join(
                    " | ".join(c.strip() for c in row) for row in rows)
            except UnicodeDecodeError:
                continue
            except Exception as e:
                raise ValueError(f"CSV parsing failed: {e}")
        raise ValueError("CSV: could not decode")

    # ── TXT ───────────────────────────────────────────────────────────────────

    def parse_txt(self, file_path: str) -> str:
        for enc in ("utf-8", "latin-1"):
            try:
                with open(file_path, "r", encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
            except Exception as e:
                raise ValueError(f"TXT parsing failed: {e}")
        raise ValueError("TXT: could not decode")

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def parse_document(self, file_path: str, file_extension: str) -> str:
        ext = file_extension.lower().lstrip(".")
        dispatch = {"pdf": self.parse_pdf, "docx": self.parse_docx,
                    "doc": self.parse_docx, "pptx": self.parse_pptx,
                    "txt": self.parse_txt, "csv": self.parse_csv}
        if ext not in dispatch:
            raise ValueError(f"Unsupported format: {ext}")
        return dispatch[ext](file_path)

    # ── Upload ────────────────────────────────────────────────────────────────

    async def upload_document(
        self,
        filename: str,
        content: bytes,
        category: str = "general",
        description: str = "",
        metadata_tags: Dict[str, Any] = None,
        notebook_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        ext = Path(filename).suffix.lower().lstrip(".")
        if ext not in ("pdf", "docx", "doc", "pptx", "txt", "csv"):
            raise ValueError(f"Unsupported file format: {ext}")

        temp_path = self.storage_path / f"temp_{filename}"
        try:
            with open(temp_path, "wb") as f:
                f.write(content)
            text_content = self.parse_document(str(temp_path), ext)
            if not text_content.strip():
                raise ValueError("Document appears empty or unreadable")

            doc_id = self._generate_doc_id(filename, text_content)
            final_path = self.storage_path / f"{doc_id}.{ext}"
            os.rename(temp_path, final_path)

            meta = {
                "doc_id": doc_id, "filename": filename,
                "stored_filename": f"{doc_id}.{ext}", "file_extension": ext,
                "category": category, "description": description,
                "upload_timestamp": datetime.now().isoformat(),
                "file_size": len(content), "text_length": len(text_content),
                "tags": metadata_tags or {}, "notebook_id": notebook_id,
            }
            self.metadata[doc_id] = meta
            self._save_metadata()
            logger.info(f"Uploaded: {doc_id} ({filename}, {len(text_content)} chars)")
            return {"doc_id": doc_id, "filename": filename,
                    "text_content": text_content, "metadata": meta}
        except Exception as e:
            if temp_path.exists():
                os.remove(temp_path)
            logger.error(f"Upload failed for {filename}: {e}")
            raise

    # ── Retrieval & management ────────────────────────────────────────────────

    def get_document_text(self, doc_id: str) -> Optional[str]:
        if doc_id not in self.metadata:
            return None
        meta = self.metadata[doc_id]
        path = self.storage_path / meta["stored_filename"]
        if not path.exists():
            return None
        try:
            return self.parse_document(str(path), meta["file_extension"])
        except Exception as e:
            logger.error(f"Read failed {doc_id}: {e}")
            return None

    def list_documents(self, category: Optional[str] = None,
                       notebook_id: Optional[str] = None) -> List[Dict[str, Any]]:
        out = []
        for doc_id, meta in self.metadata.items():
            if category is not None and meta.get("category") != category:
                continue
            if notebook_id is not None and meta.get("notebook_id") != notebook_id:
                continue
            out.append({k: meta.get(k) for k in
                        ("doc_id", "filename", "file_extension", "category",
                         "description", "upload_timestamp", "file_size",
                         "text_length", "notebook_id", "tags")})
        return out

    def delete_document(self, doc_id: str) -> bool:
        if doc_id not in self.metadata:
            return False
        path = self.storage_path / self.metadata[doc_id]["stored_filename"]
        try:
            if path.exists():
                os.remove(path)
            del self.metadata[doc_id]
            self._save_metadata()
            return True
        except Exception as e:
            logger.error(f"Delete failed {doc_id}: {e}")
            return False

    def get_document_metadata(self, doc_id: str) -> Optional[Dict[str, Any]]:
        return self.metadata.get(doc_id)

    def update_document_metadata(self, doc_id: str, category: Optional[str] = None,
                                  description: Optional[str] = None,
                                  tags: Optional[Dict[str, Any]] = None) -> bool:
        if doc_id not in self.metadata:
            return False
        if category is not None:
            self.metadata[doc_id]["category"] = category
        if description is not None:
            self.metadata[doc_id]["description"] = description
        if tags is not None:
            self.metadata[doc_id]["tags"].update(tags)
        self._save_metadata()
        return True