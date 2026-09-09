"""
models/powerbi_models.py
------------------------
Power BI request / response schemas.
"""

from pydantic import BaseModel
from typing import Optional, List, Any, Dict


# ── Dashboard creation ─────────────────────────────────────────────────────────

class PowerBIConfig(BaseModel):
    """Credentials stored in the dashboards.config JSON blob."""
    azure_client_id:     str
    azure_client_secret: str
    azure_tenant_id:     str
    workspace_id:        str
    report_id:           str
    dataset_id:          str
    workspace_name:      str
    dataset_name:        str


class CreatePowerBIDashboardRequest(BaseModel):
    name:   str
    config: PowerBIConfig


# ── Embed token ────────────────────────────────────────────────────────────────

class EmbedTokenResponse(BaseModel):
    token:      str
    tokenId:    str
    expiration: str
    embedUrl:   str


# ── Enriched schema shapes ─────────────────────────────────────────────────────

class SchemaColumn(BaseModel):
    name:          str
    type:          Optional[str] = None
    sample_values: Optional[List[Any]] = []
    description:   Optional[str] = None


class SchemaTable(BaseModel):
    name:        str
    description: Optional[str] = None
    columns:     Optional[List[SchemaColumn]] = []


class SchemaMeasure(BaseModel):
    name:                str
    description:         Optional[str] = None
    expression:          Optional[str] = None
    assigned_table:      Optional[str] = None
    page_group:          Optional[str] = None          # NEW — which page this measure appears on
    dependent_measures:  Optional[List[str]] = []      # NEW — internal measure calls e.g. [MFG Data]


class SchemaRelationship(BaseModel):
    from_table:  str
    from_column: str
    to_table:    str
    to_column:   str


class EnrichedSchemaResponse(BaseModel):
    dataset:       Optional[str] = None
    workspace:     Optional[str] = None
    tables:        List[SchemaTable] = []
    measures:      List[SchemaMeasure] = []
    relationships: List[SchemaRelationship] = []
    pages:         Optional[Dict[str, List[str]]] = {}  # NEW — page_name -> [measure names]


# ── DAX ────────────────────────────────────────────────────────────────────────

class DaxRequest(BaseModel):
    query:        str
    dashboard_id: int


# ── Active filter shape (sent from frontend slicer state) ─────────────────────

class ActiveFilter(BaseModel):
    table:  str
    column: str
    values: List[Any]


# ── Visual data shape (sent from frontend exportData()) ───────────────────────

class VisualData(BaseModel):
    title: Optional[str] = None
    type:  Optional[str] = None
    data:  Optional[str] = None   # CSV string from exportData()


# ── Lightweight visual inventory (title + type only, unfiltered) ──────────────
# Unlike VisualData above, this is NOT restricted to data-exportable visuals —
# it includes every visual on the page (images, textboxes, shapes, etc.) so
# meta questions like "how many visuals are on this page" or "what are the
# titles of the visuals" get an accurate count/list straight from the
# Power BI JS SDK's page.getVisuals(), no DAX or exportData() call needed.

class VisualInfo(BaseModel):
    title: Optional[str] = None
    type:  Optional[str] = None


# ── Chat ───────────────────────────────────────────────────────────────────────

class PowerBIChatRequest(BaseModel):
    dashboard_id:          int
    message:               str
    conversation_history:  Optional[List[dict]] = []
    page_name:             Optional[str] = None          # active page displayName
    active_filters:        Optional[List[dict]] = []     # slicer state from frontend
    page_visual_data:      Optional[List[dict]] = []     # exportData() results
    page_visual_inventory: Optional[List[VisualInfo]] = []  # NEW — full unfiltered visual list (title/type)


class PowerBIChatResponse(BaseModel):
    status:     str
    query_type: Optional[str]  = None
    message:    Optional[str]  = None
    filters:    Optional[List] = []
    dax_query:  Optional[str]  = None
    needs_dax:  Optional[bool] = False
    insight:    Optional[str]  = None
    rows:       Optional[List] = []


# ── Analyze page (descriptive summary endpoint) ───────────────────────────────

class AnalyzePageRequest(BaseModel):
    question:              str
    page_name:             Optional[str] = None
    page_visual_data:      Optional[List[dict]] = []     # exportData() results per visual
    page_visual_inventory: Optional[List[VisualInfo]] = []  # NEW — full unfiltered visual list
    active_filters:        Optional[List[dict]] = []     # active slicer state
    conversation_history:  Optional[List[dict]] = []


class AnalyzePageResponse(BaseModel):
    status:  str
    insight: Optional[str] = None
    message: Optional[str] = None


# ── Schema build progress ─────────────────────────────────────────────────────

class SchemaBuildProgress(BaseModel):
    status:  str            # "idle" | "building" | "done" | "error"
    step:    Optional[str] = None
    percent: int = 0
    detail:  Optional[str] = None