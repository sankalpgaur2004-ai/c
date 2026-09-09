"""
config.py
---------
Single source of truth for all environment variables and feature flags.
Import from here everywhere else — never call os.getenv() directly in routers.

Usage:
    from config import cfg
    print(cfg.openai_api_key)
    print(cfg.enable_powerbi)
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from dataclasses import dataclass, field

# Load .env from project root (parent of backend/) — same as main.py
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


def _bool(key: str, default: str = "true") -> bool:
    return os.getenv(key, default).strip().lower() == "true"


def _str(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


@dataclass(frozen=True)
class Config:
    # ── Feature flags ──────────────────────────────────────────────────────────
    enable_powerbi: bool = field(default_factory=lambda: _bool("ENABLE_POWERBI", "true"))
    enable_tableau: bool = field(default_factory=lambda: _bool("ENABLE_TABLEAU", "true"))

    # ── LLM ────────────────────────────────────────────────────────────────────
    openai_api_key: str = field(default_factory=lambda: _str("OPENAI_API_KEY"))
    llm_model: str = field(default_factory=lambda: _str("LLM_MODEL", "gpt-4o"))

    # ── Auth ───────────────────────────────────────────────────────────────────
    secret_key: str = field(default_factory=lambda: _str("SECRET_KEY", "change-me-in-production"))
    token_expire_hours: int = 24

    # ── CORS ───────────────────────────────────────────────────────────────────
    cors_origins: list = field(
        default_factory=lambda: [
            o.strip()
            for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
            if o.strip()
        ]
    )

    # ── Power BI REST API ──────────────────────────────────────────────────────
    powerbi_api_url: str = field(
        default_factory=lambda: _str("POWERBI_API_URL", "https://api.powerbi.com/v1.0/myorg")
    )
    powerbi_scope: str = field(
        default_factory=lambda: _str(
            "POWERBI_SCOPE", "https://analysis.windows.net/powerbi/api/.default"
        )
    )

    # ── Tableau REST API ───────────────────────────────────────────────────────
    tableau_token_expiry_minutes: int = 10

    # ── SharePoint (Azure AD app credentials) ─────────────────────────────────
    # Register these in your .env file:
    #   SHAREPOINT_TENANT_ID=<your-tenant-id>
    #   SHAREPOINT_CLIENT_ID=<your-application-client-id>
    #   SHAREPOINT_CLIENT_SECRET=<your-client-secret>
    sharepoint_tenant_id:     str = field(default_factory=lambda: _str("SHAREPOINT_TENANT_ID"))
    sharepoint_client_id:     str = field(default_factory=lambda: _str("SHAREPOINT_CLIENT_ID"))
    sharepoint_client_secret: str = field(default_factory=lambda: _str("SHAREPOINT_CLIENT_SECRET"))


# Singleton — import `cfg` everywhere
cfg = Config()


# ── Public config endpoint payload ────────────────────────────────────────────
def get_public_config() -> dict:
    return {
        "enable_powerbi": cfg.enable_powerbi,
        "enable_tableau": cfg.enable_tableau,
    }