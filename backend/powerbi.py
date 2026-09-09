"""
api/routers/powerbi.py
----------------------
Power BI endpoints:

  GET  /api/powerbi/{id}/embed-token        – Azure AD token + embed URL
  GET  /api/powerbi/{id}/schema             – enriched schema (YAML cache)
  POST /api/powerbi/{id}/schema/rebuild     – force full schema rebuild
  GET  /api/powerbi/{id}/schema/progress    – schema build progress (poll during rebuild)
  GET  /api/powerbi/{id}/pages              – list all report pages
  POST /api/powerbi/{id}/dax               – execute a DAX query
  POST /api/powerbi/{id}/chat              – agentic chat (specific + descriptive)
  POST /api/powerbi/{id}/analyze-page      – page summary using exported visual data
"""

import json
import logging
import httpx
from fastapi import APIRouter, HTTPException, Depends

from auth import get_current_user
from database import get_dashboard_by_id
from powerbi_models import (
    EmbedTokenResponse,
    DaxRequest,
    PowerBIChatRequest,
    AnalyzePageRequest,
    AnalyzePageResponse,
    SchemaBuildProgress,
)
import powerbi_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Helper ─────────────────────────────────────────────────────────────────────

def _get_powerbi_dashboard(dashboard_id: int, user: dict) -> dict:
    dashboard = get_dashboard_by_id(dashboard_id, user["user_id"])
    if not dashboard:
        raise HTTPException(status_code=404, detail=f"Dashboard {dashboard_id} not found.")
    if dashboard["type"] != "powerbi":
        raise HTTPException(status_code=400, detail=f"Dashboard {dashboard_id} is not a Power BI dashboard.")
    return dashboard


# ── Embed token ────────────────────────────────────────────────────────────────

@router.get("/{dashboard_id}/embed-token", response_model=EmbedTokenResponse)
async def get_embed_token(
    dashboard_id: int,
    user: dict = Depends(get_current_user),
):
    dashboard = _get_powerbi_dashboard(dashboard_id, user)
    config = dashboard["config"]
    try:
        async with httpx.AsyncClient() as client:
            token_resp = await client.post(
                f"https://login.microsoftonline.com/{config['azure_tenant_id']}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": config["azure_client_id"],
                    "client_secret": config["azure_client_secret"],
                    "scope": "https://analysis.windows.net/powerbi/api/.default"
                }
            )
            token_resp.raise_for_status()
            access_token = token_resp.json()["access_token"]

            embed_resp = await client.post(
                f"https://api.powerbi.com/v1.0/myorg/groups/{config['workspace_id']}/reports/{config['report_id']}/GenerateToken",
                json={"accessLevel": "view", "datasetId": config["dataset_id"]},
                headers={"Authorization": f"Bearer {access_token}"}
            )
            embed_resp.raise_for_status()
            embed_data = embed_resp.json()

            report_resp = await client.get(
                f"https://api.powerbi.com/v1.0/myorg/groups/{config['workspace_id']}/reports/{config['report_id']}",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            report_resp.raise_for_status()

        return {
            "token":      embed_data["token"],
            "tokenId":    embed_data.get("tokenId", embed_data["token"]),
            "expiration": embed_data["expiration"],
            "embedUrl":   report_resp.json()["embedUrl"],
        }
    except Exception as e:
        logger.error(f"Embed token error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Schema ─────────────────────────────────────────────────────────────────────

@router.get("/{dashboard_id}/schema")
def get_schema(
    dashboard_id: int,
    user: dict = Depends(get_current_user),
):
    dashboard = _get_powerbi_dashboard(dashboard_id, user)
    return powerbi_service.get_schema(dashboard["config"], dashboard_id)


@router.post("/{dashboard_id}/schema/rebuild")
def rebuild_schema(
    dashboard_id: int,
    user: dict = Depends(get_current_user),
):
    dashboard = _get_powerbi_dashboard(dashboard_id, user)
    try:
        return powerbi_service.build_enriched_schema(
            dashboard["config"], dashboard_id, force_rebuild=True
        )
    except RuntimeError as e:
        # Raised by build_enriched_schema when this is a PBIX-origin
        # dashboard with no cached schema to reuse — a real, actionable
        # error for the user (re-upload the file), not a server failure.
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{dashboard_id}/schema/progress", response_model=SchemaBuildProgress)
def get_schema_progress(
    dashboard_id: int,
    user: dict = Depends(get_current_user),
):
    """Poll this endpoint during a schema rebuild to show progress to the user."""
    _get_powerbi_dashboard(dashboard_id, user)
    return powerbi_service.get_build_progress(dashboard_id)


@router.get("/{dashboard_id}/schema/exists")
def schema_exists(
    dashboard_id: int,
    user: dict = Depends(get_current_user),
):
    """Quick check — returns whether a cached schema YAML exists for this dashboard."""
    dashboard = _get_powerbi_dashboard(dashboard_id, user)
    report_id = dashboard["config"].get("report_id", "")
    exists = bool(report_id and powerbi_service._load_yaml_schema(report_id))
    return {"exists": exists, "report_id": report_id}


# ── Pages ──────────────────────────────────────────────────────────────────────

@router.get("/{dashboard_id}/pages")
def get_pages(
    dashboard_id: int,
    user: dict = Depends(get_current_user),
):
    """Return all pages in the report with name, displayName, order."""
    dashboard = _get_powerbi_dashboard(dashboard_id, user)
    return powerbi_service.get_report_pages(dashboard["config"])


# ── DAX ────────────────────────────────────────────────────────────────────────

@router.post("/{dashboard_id}/dax")
def run_dax(
    dashboard_id: int,
    req: DaxRequest,
    user: dict = Depends(get_current_user),
):
    dashboard = _get_powerbi_dashboard(dashboard_id, user)
    return powerbi_service.execute_dax(dashboard["config"], req.query)


# ── Chat ───────────────────────────────────────────────────────────────────────

@router.post("/{dashboard_id}/chat")
def chat(
    dashboard_id: int,
    req: PowerBIChatRequest,
    user: dict = Depends(get_current_user),
):
    """
    Unified chat endpoint.
    - page_name: active page displayName from the embedded report
    - active_filters: slicer state from the embedded report (frontend captures this)
    - page_visual_data: exportData() results from all visuals (frontend captures this)
    - page_visual_inventory: full unfiltered {title, type} list for every visual on the
      page — used to answer meta questions like "how many visuals are on this page"
    """
    dashboard = _get_powerbi_dashboard(dashboard_id, user)
    return powerbi_service.run_chat(
        creds=dashboard["config"],
        dashboard_id=dashboard_id,
        message=req.message,
        conversation_history=req.conversation_history or [],
        page_name=req.page_name,
        active_filters=req.active_filters or [],
        page_visual_data=req.page_visual_data or [],
        page_visual_inventory=req.page_visual_inventory or [],
    )


# ── Analyze page ───────────────────────────────────────────────────────────────

@router.post("/{dashboard_id}/analyze-page", response_model=AnalyzePageResponse)
def analyze_page(
    dashboard_id: int,
    req: AnalyzePageRequest,
    user: dict = Depends(get_current_user),
):
    """
    Descriptive page summary endpoint.
    Uses exported visual data from the frontend — no DAX needed.
    Falls back to multi-DAX if no visual data provided.
    Meta questions (dashboard title, visual/page counts, schema structure)
    are answered directly, bypassing both paths.
    """
    dashboard = _get_powerbi_dashboard(dashboard_id, user)

    if powerbi_service._is_meta_question(req.question):
        schema = powerbi_service.get_schema(dashboard["config"], dashboard_id)
        return powerbi_service.answer_meta_question(
            req.question, schema, dashboard["config"],
            page_name=req.page_name,
            visual_inventory=req.page_visual_inventory or [],
        )

    if req.page_visual_data:
        # Fast path — use exported visual data directly
        return powerbi_service.run_page_summary(
            message=req.question,
            page_name=req.page_name,
            page_visual_data=req.page_visual_data,
            active_filters=req.active_filters or [],
            conversation_history=req.conversation_history or [],
        )
    else:
        # Fallback — use multi-DAX with page context
        return powerbi_service.run_chat(
            creds=dashboard["config"],
            dashboard_id=dashboard_id,
            message=req.question,
            conversation_history=req.conversation_history or [],
            page_name=req.page_name,
            active_filters=req.active_filters or [],
            page_visual_data=[],
            page_visual_inventory=req.page_visual_inventory or [],
        )