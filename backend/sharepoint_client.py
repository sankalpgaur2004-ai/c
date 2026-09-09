# sharepoint_client.py
"""
SharePoint integration via Microsoft Graph API.
Uses MSAL client-credentials flow — no MFA, no user login.
Credentials are read from config (env vars), not from the request.
"""

import logging
import requests
import msal
from typing import Dict, Any, List
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

GRAPH_BASE     = "https://graph.microsoft.com/v1.0"
DB_EXTENSIONS  = {".csv", ".xlsx", ".xls"}
DOC_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".txt"}
ALL_EXTENSIONS = DB_EXTENSIONS | DOC_EXTENSIONS


def _get_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """Acquire app-only token via client credentials — never triggers MFA."""
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        client_credential=client_secret,
    )
    result = app.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )
    if "access_token" not in result:
        err = result.get("error_description") or result.get("error") or str(result)
        raise ValueError(f"SharePoint token acquisition failed: {err}")
    return result["access_token"]


def _resolve_site_id(token: str, site_url: str) -> str:
    """Convert SharePoint site URL to Graph site ID."""
    parsed = urlparse(site_url.rstrip("/"))
    host   = parsed.netloc   # circulantsolutions.sharepoint.com
    path   = parsed.path     # /sites/Circulants2026Training
    resp = requests.get(
        f"{GRAPH_BASE}/sites/{host}:{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    if resp.status_code == 403:
        raise PermissionError(
            "Access denied (403). The app's Sites.Selected permission has not been "
            "granted to this site. Ask IT to complete the site-level permission grant."
        )
    resp.raise_for_status()
    return resp.json()["id"]


def _list_files(token: str, site_id: str) -> List[Dict[str, Any]]:
    """Recursively list all supported files in the site's default document library."""
    headers = {"Authorization": f"Bearer {token}"}
    files: List[Dict[str, Any]] = []

    drive_resp = requests.get(
        f"{GRAPH_BASE}/sites/{site_id}/drive",
        headers=headers, timeout=20,
    )
    drive_resp.raise_for_status()
    drive_id = drive_resp.json()["id"]

    def _recurse(item_id: str, current_path: str):
        url = f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/items/{item_id}/children"
        while url:
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("value", []):
                if "folder" in item:
                    _recurse(item["id"], f"{current_path}/{item['name']}")
                elif "file" in item:
                    name = item["name"]
                    ext  = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
                    if ext in ALL_EXTENSIONS:
                        files.append({
                            "name":      name,
                            "extension": ext,
                            "size":      item.get("size", 0),
                            "path":      f"{current_path}/{name}".lstrip("/"),
                            "item_id":   item["id"],
                            "drive_id":  drive_id,
                            "pipeline":  "database" if ext in DB_EXTENSIONS else "document",
                        })
            url = data.get("@odata.nextLink")

    _recurse("root", "")
    logger.info(f"SharePoint: found {len(files)} supported files in site {site_id}")
    return files


def download_file(token: str, site_id: str, drive_id: str, item_id: str) -> bytes:
    """Download a file by item_id, returns raw bytes."""
    headers = {"Authorization": f"Bearer {token}"}
    meta = requests.get(
        f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/items/{item_id}",
        headers=headers, timeout=20,
    )
    meta.raise_for_status()
    download_url = meta.json().get("@microsoft.graph.downloadUrl")
    if not download_url:
        raise ValueError(f"No download URL for item {item_id}")
    content = requests.get(download_url, timeout=60)
    content.raise_for_status()
    return content.content


def browse_site(site_url: str) -> Dict[str, Any]:
    """
    Main entry point — credentials come from config/env, not from the caller.
    Returns { site_id, db_files, doc_files, total }.
    """
    from config import cfg
    if not cfg.sharepoint_tenant_id or not cfg.sharepoint_client_id or not cfg.sharepoint_client_secret:
        raise ValueError(
            "SharePoint credentials not configured. "
            "Set SHAREPOINT_TENANT_ID, SHAREPOINT_CLIENT_ID, SHAREPOINT_CLIENT_SECRET in your .env"
        )
    token   = _get_token(cfg.sharepoint_tenant_id, cfg.sharepoint_client_id, cfg.sharepoint_client_secret)
    site_id = _resolve_site_id(token, site_url)
    files   = _list_files(token, site_id)
    return {
        "site_id":   site_id,
        "token":     token,   # returned so connect step can reuse it without re-authing
        "db_files":  [f for f in files if f["pipeline"] == "database"],
        "doc_files": [f for f in files if f["pipeline"] == "document"],
        "total":     len(files),
    }