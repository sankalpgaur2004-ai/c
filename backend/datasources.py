# datasources.py
import os
import re
import json
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Dict, List, Optional, Set
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import tempfile

# Thread pool dedicated to blocking schema-generation calls (Groq API + I/O).
# Using a separate pool prevents these long-running calls from starving the
# default executor used by FastAPI for sync route handlers.
_schema_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="schema-gen")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/datasources")

from federated_query_engine import FederatedQueryEngine

# ── Per-user state registries ──────────────────────────────────────────────────
# Each dict is keyed by user_id so every user has fully isolated state.
# A user_id of None is the legacy/unauthenticated fallback (single-user mode).

_user_sources:             Dict[str, Dict[str, object]]       = {}
_user_engines:             Dict[str, FederatedQueryEngine]    = {}
_user_subscribers:         Dict[str, Set[asyncio.Queue]]      = {}
_user_schema_gen:          Dict[str, Set[str]]                = {}
_user_schema_versions:     Dict[str, int]                     = {}

_ANON = "__anon__"   # key used when no user is authenticated

# ── Database schema build progress tracker ────────────────────────────────────
# Mirrors powerbi_service._build_progress — keyed by "user_id:alias"
# so each user's per-source progress is fully isolated.
_db_schema_build_progress: Dict[str, dict] = {}


def _set_db_progress(user_id: Optional[str], alias: str, status: str, step: str, percent: int, detail: str = ""):
    """Record schema-build progress for a single DB source (alias)."""
    key = f"{user_id or _ANON}:{alias}"
    _db_schema_build_progress[key] = {
        "status":  status,
        "step":    step,
        "percent": percent,
        "detail":  detail,
    }
    logger.info(f"[db-schema-progress] [{alias}] {percent}% — {step}")


def get_db_build_progress(user_id: Optional[str], alias: str) -> dict:
    """Return the latest progress record for this user+alias, defaulting to idle."""
    key = f"{user_id or _ANON}:{alias}"
    return _db_schema_build_progress.get(key, {
        "status": "idle", "step": None, "percent": 0, "detail": ""
    })


# ── Per-user accessors (create on first access) ───────────────────────────────

def _get_user_sources(user_id: Optional[str]) -> Dict[str, object]:
    uid = user_id or _ANON
    if uid not in _user_sources:
        _user_sources[uid] = {}
    return _user_sources[uid]


def _get_user_engine(user_id: Optional[str]) -> FederatedQueryEngine:
    uid = user_id or _ANON
    if uid not in _user_engines:
        _user_engines[uid] = FederatedQueryEngine()
    return _user_engines[uid]


def _get_user_subscribers(user_id: Optional[str]) -> Set[asyncio.Queue]:
    uid = user_id or _ANON
    if uid not in _user_subscribers:
        _user_subscribers[uid] = set()
    return _user_subscribers[uid]


def _get_user_schema_gen(user_id: Optional[str]) -> Set[str]:
    uid = user_id or _ANON
    if uid not in _user_schema_gen:
        _user_schema_gen[uid] = set()
    return _user_schema_gen[uid]


def _get_user_schema_version(user_id: Optional[str]) -> int:
    return _user_schema_versions.get(user_id or _ANON, 0)


def _inc_user_schema_version(user_id: Optional[str]):
    uid = user_id or _ANON
    _user_schema_versions[uid] = _user_schema_versions.get(uid, 0) + 1


# ── Convenience shims used by main.py / hydration.py ─────────────────────────

# Legacy aliases kept for compatibility
def _sources_for(user_id):           return _get_user_sources(user_id)
def _engine_for(user_id):            return _get_user_engine(user_id)
def _subscribers_for(user_id):       return _get_user_subscribers(user_id)

# ── Notebook-scoped in-memory accessors ───────────────────────────────────────
# Key pattern: "user_id::notebook_id"
# This allows the same alias (e.g. "sales_db") to exist in multiple projects
# without clashing in the shared _user_sources dict.

def _nb_key(user_id: Optional[str], notebook_id: Optional[str]) -> str:
    uid = user_id or _ANON
    if notebook_id:
        return f"{uid}::{notebook_id}"
    return uid

def _get_nb_sources(user_id: Optional[str], notebook_id: Optional[str]) -> Dict[str, object]:
    key = _nb_key(user_id, notebook_id)
    if key not in _user_sources:
        _user_sources[key] = {}
    return _user_sources[key]

def _get_nb_engine(user_id: Optional[str], notebook_id: Optional[str]) -> FederatedQueryEngine:
    key = _nb_key(user_id, notebook_id)
    if key not in _user_engines:
        _user_engines[key] = FederatedQueryEngine()
    return _user_engines[key]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_alias(requested: Optional[str], db_type: str) -> str:
    """Return requested alias if given, otherwise use db_type directly (no counter)."""
    if requested and requested.strip():
        return requested.strip()
    return db_type

def _get_overlapping_tables(new_tables: List[str], user_id: Optional[str] = None, exclude_alias: Optional[str] = None, notebook_id: Optional[str] = None) -> Optional[str]:
    """Overlap check disabled — duplicate table names across different sources are allowed."""
    return None

def get_active_db_manager(user_id: Optional[str] = None, notebook_id: Optional[str] = None):
    """Return FederatedQueryEngine for this notebook when sources connected, else None.
    Returns engine as long as at least one source is registered, regardless of selected_tables."""
    sources = _get_nb_sources(user_id, notebook_id)
    if sources:
        return _get_nb_engine(user_id, notebook_id)
    # Fallback: if notebook_id given but no scoped sources, check global user sources
    # (handles hydrated sources that were saved without notebook_id)
    if notebook_id:
        global_sources = _get_user_sources(user_id)
        if global_sources:
            return _get_user_engine(user_id)
    return None

def _sources_summary(user_id: Optional[str] = None, notebook_id: Optional[str] = None) -> Dict[str, str]:
    """Return {alias: db_type} dict for all connected sources of this notebook."""
    result = {}
    for alias, mgr in _get_nb_sources(user_id, notebook_id).items():
        result[alias] = getattr(mgr, "db_type", "unknown")
    return result

def _build_state_payload(user_id: Optional[str] = None, notebook_id: Optional[str] = None) -> dict:
    """Build the current state dict pushed to SSE clients for this user/notebook."""
    sources = _get_nb_sources(user_id, notebook_id)
    schema_ver = _get_user_schema_version(user_id)
    empty = {"connected": False, "db_type": None, "all_tables": [], "active_tables": [],
             "sources": {}, "is_federated": False, "schema_version": schema_ver}
    if not sources:
        return empty
    try:
        all_tables: List[str] = []
        active_tables: List[str] = []
        for alias, mgr in sources.items():
            sel = mgr.selected_tables or []
            if not sel:
                continue
            all_tables.extend(f"{alias}.{t}" for t in sel)
            active_tables.extend(f"{alias}.{t}" for t in sel)
        src_summary = _sources_summary(user_id, notebook_id)
        db_types = list(src_summary.values())
        return {
            "connected": bool(sources),
            "db_type": db_types[0] if len(db_types) == 1 else "federated",
            "all_tables": all_tables, "active_tables": active_tables,
            "sources": src_summary, "is_federated": len(sources) > 1,
            "schema_version": schema_ver,
        }
    except Exception as e:
        logger.error(f"_build_state_payload error: {e}")
        return empty


async def _push_to_user(user_id: Optional[str] = None):
    payload = _build_state_payload(user_id)
    data = json.dumps(payload)
    dead = set()
    for q in _get_user_subscribers(user_id):
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            dead.add(q)
    _get_user_subscribers(user_id).difference_update(dead)


def set_active_db_manager(manager, user_id: Optional[str] = None):
    """Backwards-compat shim: clears all sources and registers manager as 'default'."""
    sources = _get_user_sources(user_id)
    sources.clear()
    sources["default"] = manager
    engine = _get_user_engine(user_id)
    engine.clear()
    engine.add_source("default", manager)
    logger.info(f"Active DB manager set (compat): {getattr(manager, 'db_type', None)}")
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_push_to_user(None))
    except RuntimeError:
        pass

def bump_and_push(user_id: Optional[str] = None):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_push_to_user(user_id))
    except RuntimeError:
        pass

async def _run_schema_generation(manager, tables: List[str], openai_api_key: str,
                                  user_id: Optional[str] = None,
                                  notebook_id: Optional[str] = None):
    """Run set_selected_tables in a thread executor (non-blocking). On completion,
    bump the per-user schema version and push an SSE update.

    Progress is tracked in _db_schema_build_progress — mirrors the pattern in
    powerbi_service.build_enriched_schema / _set_progress so the frontend can
    poll GET /api/datasources/{alias}/schema/progress for live feedback.
    """
    # Search notebook-scoped sources first, then fall back to global user sources
    nb_sources = _get_nb_sources(user_id, notebook_id) if notebook_id else {}
    sources = nb_sources or _get_user_sources(user_id)
    alias = next((a for a, m in sources.items() if m is manager), None)
    gen_set = _get_user_schema_gen(user_id)
    if alias:
        gen_set.add(alias)
        _set_db_progress(user_id, alias, "building", "Connecting to database...", 5)

    if openai_api_key:
        logger.info(f"[{alias}] Schema generation starting (key length={len(openai_api_key)})")
    else:
        logger.warning(f"[{alias}] Schema generation starting WITHOUT API key")

    try:
        if alias:
            table_count = len(tables)
            _set_db_progress(user_id, alias, "building",
                             f"Fetching metadata for {table_count} table{'s' if table_count != 1 else ''}...", 15)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            _schema_executor,
            partial(manager.set_selected_tables, tables, openai_api_key=openai_api_key)
        )
        _inc_user_schema_version(user_id)
        bump_and_push(user_id)

        if alias:
            _set_db_progress(user_id, alias, "done", "Schema ready", 100)
    except Exception as exc:
        if alias:
            _set_db_progress(user_id, alias, "error", f"Schema build failed: {exc}", 0)
        raise
    finally:
        if alias:
            gen_set.discard(alias)

def _evict_stale_sources(db_type: str, new_alias: str, user_id: Optional[str] = None):
    """Remove failed/stale managers of the same db_type that never connected."""
    sources = _get_user_sources(user_id)
    engine  = _get_user_engine(user_id)
    to_remove = [
        alias for alias, mgr in sources.items()
        if alias != new_alias
        and getattr(mgr, "db_type", None) == db_type
        and not (getattr(mgr, "_table_cache", None) or {}).get("tables")
        and not getattr(mgr, "selected_tables", None)
    ]
    for alias in to_remove:
        logger.info(f"Evicting stale/failed source: {alias}")
        del sources[alias]
        engine.remove_source(alias)


def _replace_existing_source(alias: str, user_id: Optional[str] = None):
    """If alias is already registered, cleanly remove it so the caller can reconnect."""
    sources = _get_user_sources(user_id)
    if alias not in sources:
        return
    old_mgr = sources.pop(alias)
    _get_user_engine(user_id).remove_source(alias)
    try:
        old_mgr.cleanup()
    except Exception:
        pass
    logger.info(f"Replaced existing source '{alias}' with new connection")


def _namespace_rel(rel: str, alias: str) -> str:
    """
    Ensure both sides of a relationship string carry the alias prefix.

    The YAML stores relationships as bare "table.col = table.col" strings
    (relative to the source's db_type prefix).  schema_preview must return
    them as "alias.table.col = alias.table.col" so the frontend can match
    them against its namespaced table keys.

    If a side already starts with "<alias>." it is left untouched (idempotent).
    """
    if " = " not in rel:
        return rel
    left, right = rel.split(" = ", 1)

    def add_prefix(side: str) -> str:
        side = side.strip()
        if side.lower().startswith(alias.lower() + "."):
            return side  # already namespaced
        return f"{alias}.{side}"

    return f"{add_prefix(left)} = {add_prefix(right)}"


def _strip_alias_from_rel(rel: str, alias: str) -> str:
    """
    Strip the alias prefix from both sides of a relationship string before
    storing it in a per-source DatabaseManager (which uses bare names).

    "alias.table.col = alias2.table2.col2" → "table.col = alias2.table2.col2"
    Only the *left* side's alias is stripped since a manager only owns its own
    tables; cross-source references on the right are kept fully qualified so
    they survive a round-trip through the YAML.
    """
    if " = " not in rel:
        return rel
    left, right = rel.split(" = ", 1)
    prefix = alias + "."
    if left.strip().lower().startswith(prefix.lower()):
        left = left.strip()[len(prefix):]
    return f"{left.strip()} = {right.strip()}"


def _uid(request: Request) -> Optional[str]:
    """Extract user_id from session cookie, or None for unauthenticated requests."""
    import auth as _auth
    user = _auth.get_optional_user(request)
    return user["user_id"] if user else None

def _schema_alias(user_id: Optional[str], alias: str) -> str:
    """Namespace YAML schema key by user so each user's schema is isolated."""
    return f"{user_id[:8]}_{alias}" if user_id else alias

# ── Request Models ─────────────────────────────────────────────────────────────

class SQLiteConnectRequest(BaseModel):
    db_path: str
    alias: Optional[str] = None
    notebook_id: Optional[str] = None

class MySQLConnectRequest(BaseModel):
    host: str
    port: int = 3306
    user: str
    password: str
    database: str
    alias: Optional[str] = None
    notebook_id: Optional[str] = None

class PostgreSQLConnectRequest(BaseModel):
    host: str
    port: int = 5432
    user: str
    password: str
    database: str
    alias: Optional[str] = None
    notebook_id: Optional[str] = None

class DatabricksConnectRequest(BaseModel):
    server_hostname: str
    http_path: str
    access_token: str
    schema: str = "default"
    catalog: Optional[str] = None
    alias: Optional[str] = None
    notebook_id: Optional[str] = None

class SnowflakeConnectRequest(BaseModel):
    account: str
    user: str
    password: str
    database: str
    warehouse: str
    schema: str = "PUBLIC"
    alias: Optional[str] = None
    notebook_id: Optional[str] = None

class DisconnectSourceRequest(BaseModel):
    alias: str

class TableFilterRequest(BaseModel):
    tables: List[str]

class SchemaEditRequest(BaseModel):
    """
    Carries only the fields the user actually changed in the schema editor.
    All keys are optional — omit anything that was not modified.

    Example payload:
    {
        "tables": {
            "orders": {
                "description": "All customer orders",
                "columns": {
                    "order_id": {"description": "Unique order identifier"}
                }
            }
        },
        "relationships": ["orders.customer_id -> customers.id"]
    }
    """
    tables: Optional[dict] = None
    relationships: Optional[List[str]] = None


class RelationshipsUpdateRequest(BaseModel):
    """
    Full replacement list of relationship strings.
    Each string must be in the form:
        "alias.table.col = alias2.table2.col2"
    """
    relationships: List[str]


# ── SSE Stream Endpoint ────────────────────────────────────────────────────────

@router.get("/stream")
async def datasource_stream(request: Request):
    """
    Per-user SSE endpoint. Each authenticated user only receives their own
    state changes. Unauthenticated clients fall back to the shared anon stream.
    """
    import auth as auth_module
    user = auth_module.get_optional_user(request)
    user_id = user["user_id"] if user else None

    queue: asyncio.Queue = asyncio.Queue(maxsize=20)
    _get_user_subscribers(user_id).add(queue)

    async def event_generator():
        initial = json.dumps(_build_state_payload(user_id))
        yield f"data: {initial}\n\n"

        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _get_user_subscribers(user_id).discard(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disables nginx buffering
            "Connection": "keep-alive",
        },
    )

# ── Connect Endpoints ──────────────────────────────────────────────────────────

@router.post("/connect-sqlite")
async def connect_sqlite(req: SQLiteConnectRequest, request: Request):
    from database_manager import DatabaseManager
    uid = _uid(request); openai_api_key = os.getenv("OPENAI_API_KEY"); nb_id = req.notebook_id
    try:
        alias = _make_alias(req.alias, "sqlite")
        manager = DatabaseManager(db_path=req.db_path, db_type="sqlite", schema_alias=_schema_alias(uid, alias))
        tables = manager.get_all_tables_unfiltered()
        dup = _get_overlapping_tables(tables, uid, exclude_alias=alias, notebook_id=nb_id)
        if dup: raise HTTPException(status_code=400, detail=dup)
        _get_nb_sources(uid, nb_id)[alias] = manager
        _get_nb_engine(uid, nb_id).add_source(alias, manager)
        bump_and_push(uid)
        import user_store; user_store.save_data_source(uid or "anon", alias, "sqlite", {"db_path": req.db_path}, [], notebook_id=nb_id)
        return {"success": True, "db_type": "sqlite", "tables": tables, "alias": alias, "sources": _sources_summary(uid, nb_id)}
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=400, detail=str(e))


@router.post("/pbix-upload")
async def upload_pbix(
    request: Request,
    file: UploadFile = File(...),
    notebook_id: Optional[str] = Form(None),
    alias: Optional[str] = Form(None),
):
    """
    Direct .pbix upload — alternative to the credentials-based Power BI
    connection. Extracts the model with pbixray, builds a schema in the
    exact same format/location as a credentialed dashboard's schema, and
    publishes the file into the shared workspace so it embeds and is
    DAX-queryable exactly like a credentialed dashboard from that point on
    — see pbix_service.process_pbix_upload for the full pipeline. No
    SQL/SQLite layer is involved at all.

    If an identical model (by content, not filename) was already uploaded,
    reuses the existing schema AND the existing workspace import instead of
    redoing either — see pbix_service.compute_model_fingerprint.
    """
    if not file.filename.lower().endswith(".pbix"):
        raise HTTPException(status_code=400, detail="File must be a .pbix")

    uid = _uid(request)

    # Stage the upload to a temp path — pbixray needs a real file path, not a stream.
    with tempfile.NamedTemporaryFile(suffix=".pbix", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        import pbix_service
        result = pbix_service.process_pbix_upload(
            pbix_path=tmp_path,
            user_id=uid,
            notebook_id=notebook_id,
            requested_alias=alias,
            original_filename=file.filename,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"pbix-upload failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass




@router.post("/connect-mysql")
async def connect_mysql(req: MySQLConnectRequest, request: Request):
    from database_manager import DatabaseManager
    uid = _uid(request); openai_api_key = os.getenv("OPENAI_API_KEY"); nb_id = req.notebook_id
    try:
        alias = _make_alias(req.alias, "mysql")
        cfg = {"host": req.host, "port": req.port, "user": req.user, "password": req.password, "database": req.database}
        manager = DatabaseManager(db_type="mysql", connection_config=cfg, schema_alias=_schema_alias(uid, alias))
        tables = manager.get_all_tables_unfiltered()
        dup = _get_overlapping_tables(tables, uid, exclude_alias=alias, notebook_id=nb_id)
        if dup: raise HTTPException(status_code=400, detail=dup)
        _get_nb_sources(uid, nb_id)[alias] = manager
        _get_nb_engine(uid, nb_id).add_source(alias, manager)
        bump_and_push(uid)
        import user_store; user_store.save_data_source(uid or "anon", alias, "mysql", cfg, [], notebook_id=nb_id)
        return {"success": True, "db_type": "mysql", "tables": tables, "alias": alias, "sources": _sources_summary(uid, nb_id)}
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=400, detail=str(e))

@router.post("/connect-postgresql")
async def connect_postgresql(req: PostgreSQLConnectRequest, request: Request):
    from database_manager import DatabaseManager
    uid = _uid(request); openai_api_key = os.getenv("OPENAI_API_KEY"); nb_id = req.notebook_id
    try:
        alias = _make_alias(req.alias, "postgresql")
        cfg = {"host": req.host, "port": req.port, "user": req.user, "password": req.password, "database": req.database}
        manager = DatabaseManager(db_type="postgresql", connection_config=cfg, schema_alias=_schema_alias(uid, alias))
        tables = manager.get_all_tables_unfiltered()
        dup = _get_overlapping_tables(tables, uid, exclude_alias=alias, notebook_id=nb_id)
        if dup: raise HTTPException(status_code=400, detail=dup)
        _get_nb_sources(uid, nb_id)[alias] = manager
        _get_nb_engine(uid, nb_id).add_source(alias, manager)
        bump_and_push(uid)
        import user_store; user_store.save_data_source(uid or "anon", alias, "postgresql", cfg, [], notebook_id=nb_id)
        return {"success": True, "db_type": "postgresql", "tables": tables, "alias": alias, "sources": _sources_summary(uid, nb_id)}
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=400, detail=str(e))

@router.post("/connect-databricks")
async def connect_databricks(req: DatabricksConnectRequest, request: Request):
    from database_manager import DatabaseManager
    uid = _uid(request); openai_api_key = os.getenv("OPENAI_API_KEY"); nb_id = req.notebook_id
    try:
        alias = _make_alias(req.alias, "databricks")
        cfg = {"server_hostname": req.server_hostname, "http_path": req.http_path,
               "access_token": req.access_token, "schema": req.schema, "catalog": req.catalog}
        manager = DatabaseManager(db_type="databricks", connection_config=cfg, schema_alias=_schema_alias(uid, alias))
        manager.invalidate_table_cache()
        tables = manager.get_all_tables_unfiltered()
        dup = _get_overlapping_tables(tables, uid, exclude_alias=alias, notebook_id=nb_id)
        if dup: raise HTTPException(status_code=400, detail=dup)
        _get_nb_sources(uid, nb_id)[alias] = manager
        _get_nb_engine(uid, nb_id).add_source(alias, manager)
        bump_and_push(uid)
        import user_store; user_store.save_data_source(uid or "anon", alias, "databricks", cfg, [], notebook_id=nb_id)
        return {"success": True, "db_type": "databricks", "tables": tables, "alias": alias, "sources": _sources_summary(uid, nb_id)}
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=400, detail=str(e))

@router.post("/connect-snowflake")
async def connect_snowflake(req: SnowflakeConnectRequest, request: Request):
    from database_manager import DatabaseManager
    uid = _uid(request); openai_api_key = os.getenv("OPENAI_API_KEY")
    try:
        alias = _make_alias(req.alias, "snowflake")
        cfg = {"account": req.account, "user": req.user, "password": req.password,
               "database": req.database, "warehouse": req.warehouse, "schema": req.schema}
        manager = DatabaseManager(db_type="snowflake", connection_config=cfg, schema_alias=_schema_alias(uid, alias))
        _replace_existing_source(alias, uid)
        tables = manager.get_all_tables_unfiltered()
        dup = _get_overlapping_tables(tables, uid, exclude_alias=alias)
        if dup: raise HTTPException(status_code=400, detail=dup)
        _get_user_sources(uid)[alias] = manager
        _get_user_engine(uid).add_source(alias, manager)
        _evict_stale_sources("snowflake", alias, uid)
        bump_and_push(uid)
        import user_store; user_store.save_data_source(uid or "anon", alias, "snowflake", cfg, [], notebook_id=req.notebook_id)
        return {"success": True, "db_type": "snowflake", "tables": tables, "alias": alias, "sources": _sources_summary(uid)}
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=400, detail=str(e))

@router.post("/connect-csv")
async def connect_csv(request: Request, file: UploadFile = File(...), alias: Optional[str] = Form(None), notebook_id: Optional[str] = Form(None)):
    from database_manager import DatabaseManager
    import shutil
    from pathlib import Path
    uid = _uid(request); openai_api_key = os.getenv("OPENAI_API_KEY"); nb_id = notebook_id
    try:
        if not alias or not alias.strip():
            csv_basename = os.path.splitext(file.filename)[0]
            alias = re.sub(r'[^a-zA-Z0-9_]', '_', csv_basename)
            if alias and alias[0].isdigit(): alias = f"csv_{alias}"
            if not alias: alias = None
        resolved_alias = _make_alias(alias, "csv")

        csv_dir = Path(__file__).resolve().parent / "data" / "user_csvs" / uid
        csv_dir.mkdir(parents=True, exist_ok=True)

        suffix = os.path.splitext(file.filename)[1] or ".csv"
        csv_filename = f"{resolved_alias}{suffix}"
        csv_path = csv_dir / csv_filename

        with open(csv_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        manager = DatabaseManager.from_csv(
            csv_path=str(csv_path), schema_alias=_schema_alias(uid, resolved_alias),
            original_filename=file.filename
        )
        tables = manager.get_all_tables_unfiltered()
        dup = _get_overlapping_tables(tables, uid, exclude_alias=resolved_alias, notebook_id=nb_id)
        if dup: raise HTTPException(status_code=400, detail=dup)
        nb_sources = _get_nb_sources(uid, nb_id)
        old_manager = nb_sources.get(resolved_alias)
        if old_manager is not None and old_manager is not manager:
            try: old_manager.cleanup()
            except Exception as e: logger.warning(f"cleanup() failed for replaced source '{resolved_alias}': {e}")
        nb_sources[resolved_alias] = manager
        _get_nb_engine(uid, nb_id).add_source(resolved_alias, manager)
        asyncio.ensure_future(_run_schema_generation(manager, tables, openai_api_key or "", uid, notebook_id=nb_id))
        bump_and_push(uid)
        import user_store
        user_store.save_data_source(uid or "anon", resolved_alias, "csv",
                                    {"csv_path": str(csv_path), "original_filename": file.filename}, [], notebook_id=nb_id)
        return {"success": True, "db_type": "csv", "tables": tables, "alias": resolved_alias, "sources": _sources_summary(uid, nb_id)}
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=400, detail=str(e))

# ── Table Filter ───────────────────────────────────────────────────────────────

@router.post("/update-table-filter")
async def update_table_filter(req: TableFilterRequest, request: Request, notebook_id: Optional[str] = None):
    uid = _uid(request)
    sources = _get_nb_sources(uid, notebook_id) if notebook_id else _get_user_sources(uid)
    logger.info(f"update-table-filter: uid={uid!r} notebook_id={notebook_id!r} sources_found={bool(sources)}")
    # Fallback: scan ALL scopes for this user (handles uid/notebook_id mismatch between requests)
    if not sources:
        nb_key_prefix = (uid or _ANON) + "::"
        for key, src_dict in _user_sources.items():
            if key.startswith(nb_key_prefix) and src_dict:
                sources = src_dict
                notebook_id = key[len(nb_key_prefix):]
                logger.info(f"update-table-filter: fallback matched nb-scoped key={key!r}")
                break
    # Also try global user scope (sources stored without notebook_id)
    if not sources:
        global_sources = _get_user_sources(uid)
        if global_sources:
            sources = global_sources
            notebook_id = None
            logger.info(f"update-table-filter: fallback matched global user scope")
    if not sources:
        raise HTTPException(status_code=400, detail="No data source connected")
    if not req.tables:
        raise HTTPException(status_code=400, detail="At least one table must be selected")
    openai_api_key = os.getenv("OPENAI_API_KEY")
    try:
        tables_by_alias: dict = {}
        for namespaced in req.tables:
            if "." in namespaced:
                alias, bare = namespaced.split(".", 1)
            else:
                alias = next(iter(sources))
                bare = namespaced
            tables_by_alias.setdefault(alias, []).append(bare)

        gen_set = _get_user_schema_gen(uid)
        for alias, mgr in sources.items():
            if alias not in tables_by_alias:
                continue
            selected = tables_by_alias[alias]
            existing_tables = set((getattr(mgr, "schema_info", None) or {}).get("tables", {}).keys())
            new_tables = [t for t in selected if t not in existing_tables]

            if new_tables:
                if alias in gen_set:
                    logger.info(f"[{alias}] Schema gen in progress — updating selection only")
                    mgr.selected_tables = selected
                else:
                    logger.info(f"[{alias}] {len(new_tables)} new tables need schema gen")
                    asyncio.ensure_future(_run_schema_generation(mgr, selected, openai_api_key or "", uid))
            else:
                logger.info(f"[{alias}] All tables already in schema — skipping LLM")
                mgr.selected_tables = selected

            import user_store; user_store.update_selected_tables(uid or "anon", alias, selected)

        bump_and_push(uid)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/update-schema")
async def update_schema(req: SchemaEditRequest, request: Request, notebook_id: Optional[str] = None):
    uid = _uid(request)
    sources = _get_nb_sources(uid, notebook_id) if notebook_id else _get_user_sources(uid)
    if not sources: raise HTTPException(status_code=400, detail="No data source connected")
    if not req.tables: return {"success": True, "changed": False}
    try:
        edits_by_alias: dict = {}
        for namespaced_key, tschema in (req.tables or {}).items():
            if "." in namespaced_key:
                alias, bare = namespaced_key.split(".", 1)
            else:
                alias = next(iter(sources)); bare = namespaced_key
            edits_by_alias.setdefault(alias, {})[bare] = tschema
        for alias, bare_tables in edits_by_alias.items():
            mgr = sources.get(alias)
            if not mgr: logger.warning(f"update-schema: unknown alias '{alias}' — skipped"); continue
            mgr.apply_schema_edits({"tables": bare_tables})
        bump_and_push(uid)
        return {"success": True, "changed": True}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))


# ── Relationships Endpoints ────────────────────────────────────────────────────
#
# NOTE: these previously used _get_user_sources(uid) — a legacy, non-notebook-
# scoped lookup keyed by bare user_id. Every source in the current data model
# is registered under "{user_id}::{notebook_id}" via _get_nb_sources(), so the
# old lookup always returned an empty dict and these endpoints always failed
# with "No data source connected" for any notebook. Fixed to accept
# notebook_id like every other endpoint in this router.

@router.get("/relationships")
async def get_relationships(request: Request, notebook_id: Optional[str] = None):
    uid = _uid(request)
    sources = _get_nb_sources(uid, notebook_id) if notebook_id else _get_user_sources(uid)
    if not sources: raise HTTPException(status_code=400, detail="No data source connected")
    try:
        first_mgr = next(iter(sources.values()))
        global_data = first_mgr._read_global_yaml()
    except Exception: global_data = {}
    raw_rels: List[str] = global_data.get("relationships") or []
    table_to_alias: dict = {}
    for alias, mgr in sources.items():
        for bare_table in (getattr(mgr, "schema_info", None) or {}).get("tables", {}).keys():
            table_to_alias[bare_table.lower()] = alias

    def _ns(side: str) -> str:
        side = side.strip()
        for alias in sources:
            if side.lower().startswith(alias.lower() + "."): return side
        parts = side.split(".")
        if len(parts) >= 2:
            detected = table_to_alias.get(parts[0].lower())
            if detected: return f"{detected}.{side}"
        return side

    # Every table actually loaded (selected) in THIS notebook, alias-qualified —
    # built first so relationships can be filtered against it below. The
    # relationships YAML is keyed by schema_alias (user + db alias), not by
    # notebook, so it can carry entries left over from tables that were
    # selected in a different notebook, or later deselected here. Only show
    # relationships where both sides are actually loaded right now.
    tables: dict = {}
    for alias, mgr in sources.items():
        schema = getattr(mgr, "schema_info", None) or {}
        for bare_name, tschema in schema.get("tables", {}).items():
            sel = getattr(mgr, "selected_tables", None)
            if sel is not None and bare_name not in sel:
                continue
            cols = list((tschema.get("columns") or {}).keys())
            tables[f"{alias}.{bare_name}"] = cols

    def _table_key(side: str) -> str:
        # "alias.table.col" -> "alias.table"
        return side.rsplit(".", 1)[0]

    all_rels: List[str] = []
    for rel in raw_rels:
        if " = " not in rel: continue
        l, r = rel.split(" = ", 1)
        ns_l, ns_r = _ns(l), _ns(r)
        if _table_key(ns_l) not in tables or _table_key(ns_r) not in tables:
            continue
        ns = f"{ns_l} = {ns_r}"
        if ns not in all_rels: all_rels.append(ns)

    return {"success": True, "relationships": all_rels, "tables": tables}


@router.post("/relationships")
async def update_relationships(req: RelationshipsUpdateRequest, request: Request, notebook_id: Optional[str] = None):
    uid = _uid(request)
    sources = _get_nb_sources(uid, notebook_id) if notebook_id else _get_user_sources(uid)
    if not sources: raise HTTPException(status_code=400, detail="No data source connected")
    try:
        valid_rels = [r for r in req.relationships if " = " in r]
        first_mgr = next(iter(sources.values()))

        # The relationships list is stored globally per (user, alias) — not
        # per notebook — so it can be shared by another notebook reusing the
        # same alias. Only replace the slice that touches tables loaded HERE;
        # leave every other entry untouched instead of overwriting the whole
        # list, matching the same scoping GET /relationships now applies.
        tables_here: set = set()
        for alias, mgr in sources.items():
            schema = getattr(mgr, "schema_info", None) or {}
            sel = getattr(mgr, "selected_tables", None)
            for bare_name in schema.get("tables", {}).keys():
                if sel is not None and bare_name not in sel:
                    continue
                tables_here.add(f"{alias}.{bare_name}".lower())

        def _table_key(side: str) -> str:
            return side.strip().rsplit(".", 1)[0].lower()

        def _touches_here(rel: str) -> bool:
            if " = " not in rel: return False
            l, r = rel.split(" = ", 1)
            return _table_key(l) in tables_here or _table_key(r) in tables_here

        with first_mgr._global_schema_lock():
            gd = first_mgr._read_global_yaml()
            foreign = [r for r in (gd.get("relationships") or []) if not _touches_here(r)]
            merged  = foreign + valid_rels
            gd["relationships"] = merged
            first_mgr._write_global_yaml(gd)
        for alias, mgr in sources.items():
            if hasattr(mgr, "schema_info") and isinstance(mgr.schema_info, dict):
                mgr.schema_info["relationships"] = merged
        bump_and_push(uid)
        return {"success": True, "relationships": valid_rels}
    except Exception as e:
        logger.error(f"update_relationships error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/disconnect-source")
async def disconnect_source(req: DisconnectSourceRequest, request: Request, notebook_id: Optional[str] = None):
    logger.info(f"disconnect_source called: alias={req.alias}, notebook_id={notebook_id}")
    uid = _uid(request)
    sources = _get_nb_sources(uid, notebook_id) if notebook_id else _get_user_sources(uid)

    found = False

    # Try finding in the current scope
    if req.alias in sources:
        mgr = sources.pop(req.alias)
        _get_nb_engine(uid, notebook_id).remove_source(req.alias)
        # If it's a PowerBI dashboard, delete from dashboards table
        if hasattr(mgr, 'db_type') and mgr.db_type == 'powerbi' and hasattr(mgr, 'dashboard_id'):
            try:
                from database import delete_dashboard
                delete_dashboard(mgr.dashboard_id, uid)
            except Exception as e:
                logger.warning(f"Failed to delete dashboard {mgr.dashboard_id}: {e}")
        try: mgr.cleanup()
        except Exception as e: logger.warning(f"cleanup() failed for '{req.alias}': {e}")
        found = True
    else:
        # Try finding across all notebook scopes
        for key, src_dict in list(_user_sources.items()):
            if key.startswith(uid) and req.alias in src_dict:
                mgr = src_dict.pop(req.alias)
                # If it's a PowerBI dashboard, delete from dashboards table
                if hasattr(mgr, 'db_type') and mgr.db_type == 'powerbi' and hasattr(mgr, 'dashboard_id'):
                    try:
                        from database import delete_dashboard
                        delete_dashboard(mgr.dashboard_id, uid)
                    except Exception as e:
                        logger.warning(f"Failed to delete dashboard {mgr.dashboard_id}: {e}")
                try: mgr.cleanup()
                except: pass
                found = True
                break

    # If not found in memory, try finding PowerBI dashboard by name in dashboards table
    if not found:
        try:
            from database import get_dashboards_for_user, delete_dashboard
            dashboards = get_dashboards_for_user(uid)
            for dash in dashboards:
                if dash["name"] == req.alias:
                    logger.info(f"Found legacy dashboard by name: {req.alias} (id={dash['id']})")
                    delete_dashboard(dash["id"], uid)
                    found = True
                    break
        except Exception as e:
            logger.warning(f"Failed to check dashboards table: {e}")

    if not found:
        raise HTTPException(status_code=404, detail=f"Source '{req.alias}' not found")

    import user_store
    user_store.delete_data_source(uid or "anon", req.alias)
    bump_and_push(uid)
    return {"success": True, "sources": _sources_summary(uid, notebook_id)}


@router.get("/sources")
async def list_sources(request: Request, notebook_id: Optional[str] = None):
    uid = _uid(request)
    sources = _get_nb_sources(uid, notebook_id)
    summary = _sources_summary(uid, notebook_id)
    return {"sources": summary, "is_federated": len(sources) > 1}


@router.post("/disconnect")
async def disconnect(request: Request):
    uid = _uid(request); sources = _get_user_sources(uid)
    for alias, mgr in list(sources.items()):
        try: mgr.cleanup()
        except Exception as e: logger.warning(f"cleanup() failed for '{alias}': {e}")
    sources.clear()
    _get_user_engine(uid).clear()
    import user_store
    for alias in list(sources.keys()):
        user_store.delete_data_source(uid or "anon", alias)
    bump_and_push(uid)
    return {"success": True}


@router.get("/table-preview")
async def table_preview(request: Request, alias: str, table: str, notebook_id: Optional[str] = None, limit: int = 50):
    """Return first N rows of a table directly without going through the AI pipeline."""
    uid = _uid(request)
    # Try notebook-scoped sources first, fall back to global
    sources = _get_nb_sources(uid, notebook_id) if notebook_id else {}
    if not sources:
        sources = _get_user_sources(uid)
    if not sources:
        raise HTTPException(status_code=400, detail="No data source connected")
    mgr = sources.get(alias)
    if not mgr:
        raise HTTPException(status_code=404, detail=f"Source '{alias}' not found in notebook")
    try:
        import pandas as pd
        # Quoting: backticks for sqlite/mysql/csv/databricks, double-quote for postgresql/snowflake
        db_type = getattr(mgr, 'db_type', '')
        if db_type in ('sqlite', 'mysql', 'csv', 'databricks'):
            query = f'SELECT * FROM `{table}` LIMIT {limit}'
        else:
            query = f'SELECT * FROM "{table}" LIMIT {limit}'
        df = mgr.execute_query(query)
        if not isinstance(df, pd.DataFrame) or df.empty:
            return {"success": True, "columns": list(df.columns) if isinstance(df, pd.DataFrame) else [], "rows": [], "count": 0}
        import math, numpy as np
        columns = list(df.columns)
        # Convert all non-JSON-safe values (NaN, Inf, NaT, numpy types) to None/str
        def safe(v):
            if v is None: return None
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)): return None
            if isinstance(v, (np.integer,)): return int(v)
            if isinstance(v, (np.floating,)): return None if np.isnan(v) else float(v)
            if isinstance(v, np.bool_): return bool(v)
            if isinstance(v, pd.Timestamp): return str(v)
            try:
                import pandas as _pd
                if _pd.isna(v): return None
            except: pass
            return v
        rows = [{c: safe(row[c]) for c in columns} for row in df.to_dict(orient='records')]
        return {"success": True, "columns": columns, "rows": rows, "count": len(rows)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"table_preview error for {alias}.{table}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/schema-preview")
async def schema_preview(request: Request, notebook_id: Optional[str] = None, alias: Optional[str] = None):
    uid = _uid(request)
    sources = _get_nb_sources(uid, notebook_id) if notebook_id else _get_user_sources(uid)

    # If alias is provided but not found in sources, check if it's a dashboard
    if alias and not sources.get(alias):
        try:
            from database import get_dashboards_for_user
            dashboards = get_dashboards_for_user(uid)
            for dash in dashboards:
                if dash["name"] == alias:
                    # Return empty schema for dashboards (they have their own structure from Power BI)
                    return {"success": True, "tables": {}, "selected_tables": {alias: []}}
        except Exception as e:
            logger.warning(f"Failed to check dashboards for schema preview: {e}")

        raise HTTPException(status_code=400, detail=f"Source '{alias}' not found")

    if not sources: raise HTTPException(status_code=400, detail="No data source connected")
    merged_tables: dict = {}
    selected: dict = {}
    for src_alias, mgr in sources.items():
        if alias and src_alias != alias:
            continue
        schema     = getattr(mgr, "schema_info", None) or {}
        sel_tables = getattr(mgr, "selected_tables", None)
        selected[src_alias] = sel_tables or []
        for bare_name, tschema in schema.get("tables", {}).items():
            # schema_info accumulates every table ever seen (additive cache) —
            # selected_tables is the actual user-chosen filter. Match the same
            # "None = show all, list = show only these" semantics used by
            # DatabaseManager.get_available_tables()/get_schema_context() so the
            # preview stays consistent with what the chat engine can query.
            if sel_tables is not None and bare_name not in sel_tables:
                continue
            merged_tables[f"{src_alias}.{bare_name}"] = tschema
    return {"success": True, "tables": merged_tables, "selected_tables": selected}


@router.get("/has-schema")
async def has_schema(request: Request):
    uid = _uid(request)
    state = _build_state_payload(uid)
    all_tables = state.get("all_tables", [])
    connected = state.get("connected", False) and len(all_tables) > 0
    return {"has_schema": connected, "table_count": len(all_tables), "tables": all_tables}


# ══════════════════════════════════════════════════════════════════════════════
# Database schema progress endpoints
# Mirrors the PowerBI pattern in powerbi.py:
#   GET  /api/powerbi/{id}/schema/exists
#   GET  /api/powerbi/{id}/schema/progress
#   POST /api/powerbi/{id}/schema/rebuild
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/{alias}/schema/exists")
async def db_schema_exists(alias: str, request: Request, notebook_id: Optional[str] = None):
    """
    Quick check — returns whether a fully-described schema already exists for
    this source alias.  Mirrors GET /api/powerbi/{id}/schema/exists.

    A schema is considered "ready" when:
      • The manager has schema_info loaded, AND
      • At least one table carries the enriched=True flag (set by LLM description).
    This lets the frontend skip the progress bar when reconnecting to a source
    whose schema was already built in a prior session.
    """
    uid = _uid(request)
    sources = _get_nb_sources(uid, notebook_id) if notebook_id else {}
    if not sources:
        sources = _get_user_sources(uid)

    mgr = sources.get(alias)
    if not mgr:
        raise HTTPException(status_code=404, detail=f"Source '{alias}' not found")

    schema_tables = (getattr(mgr, "schema_info", None) or {}).get("tables", {})
    enriched = any(
        isinstance(info, dict) and info.get("enriched", False)
        for info in schema_tables.values()
    )
    return {"exists": enriched, "alias": alias, "table_count": len(schema_tables)}


@router.get("/{alias}/schema/progress")
async def db_schema_progress(alias: str, request: Request):
    """
    Poll this endpoint during schema generation to show progress to the user.
    Mirrors GET /api/powerbi/{id}/schema/progress.

    Response shape (same as SchemaBuildProgress in powerbi_models.py):
      { status: "idle"|"building"|"done"|"error", step: str|null, percent: int }
    """
    uid = _uid(request)
    return get_db_build_progress(uid, alias)


@router.post("/{alias}/schema/rebuild")
async def db_schema_rebuild(alias: str, request: Request, notebook_id: Optional[str] = None):
    """
    Force a full schema rebuild for an existing source — clears the enriched
    flag on all tables then re-runs LLM description in the background.
    Mirrors POST /api/powerbi/{id}/schema/rebuild.
    """
    uid = _uid(request)
    sources = _get_nb_sources(uid, notebook_id) if notebook_id else {}
    if not sources:
        sources = _get_user_sources(uid)

    mgr = sources.get(alias)
    if not mgr:
        raise HTTPException(status_code=404, detail=f"Source '{alias}' not found")

    openai_api_key = os.getenv("OPENAI_API_KEY")
    tables = getattr(mgr, "selected_tables", None) or list(
        (getattr(mgr, "schema_info", None) or {}).get("tables", {}).keys()
    )
    if not tables:
        raise HTTPException(status_code=400, detail=f"No tables selected for source '{alias}'")

    # Clear enriched flag so set_selected_tables re-describes everything
    schema_tables = (getattr(mgr, "schema_info", None) or {}).get("tables", {})
    for tinfo in schema_tables.values():
        if isinstance(tinfo, dict):
            tinfo.pop("enriched", None)

    _set_db_progress(uid, alias, "building", "Rebuild requested...", 2)
    asyncio.ensure_future(_run_schema_generation(mgr, tables, openai_api_key or "", uid, notebook_id=notebook_id))
    logger.info(f"[{alias}] Schema rebuild started by user {uid}")
    return {"success": True, "alias": alias, "tables": tables}


# ══════════════════════════════════════════════════════════════════════════════
# SharePoint endpoints
# Credentials live in .env / config — only site_url comes from the frontend.
# ══════════════════════════════════════════════════════════════════════════════

class SharePointBrowseRequest(BaseModel):
    site_url:    str
    notebook_id: Optional[str] = None

class SharePointFileItem(BaseModel):
    name:      str
    extension: str
    item_id:   str
    drive_id:  str
    path:      str
    size:      int
    pipeline:  str   # "database" or "document"

class SharePointConnectRequest(BaseModel):
    site_url:    str
    site_id:     str
    token:       str
    files:       List[SharePointFileItem]
    notebook_id: Optional[str] = None
    category:    str = "general"


@router.post("/sharepoint-browse")
async def sharepoint_browse(req: SharePointBrowseRequest, request: Request):
    """Authenticate with SharePoint and return classified file list. Creds from .env."""
    try:
        from sharepoint_client import browse_site
        result = browse_site(site_url=req.site_url)
        return {
            "success":   True,
            "site_id":   result["site_id"],
            "db_files":  result["db_files"],
            "doc_files": result["doc_files"],
            "total":     result["total"],
            "_token":    result["token"],
        }
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"sharepoint_browse error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sharepoint-connect")
async def sharepoint_connect(req: SharePointConnectRequest, request: Request):
    """
    Download selected SharePoint files and route to the correct pipeline.
    CSV/Excel  → DatabaseManager with db_type="sharepoint_csv" → shown under Databases as SharePoint.
    PDF/DOCX/PPTX/TXT → document_manager + RAG → shown under Documents as SharePoint.
    Schema is preserved on disconnect (files deleted, YAML kept for reuse).
    """
    import io
    from pathlib import Path
    from main import document_manager as doc_mgr, rag_engine as global_rag_engine

    uid            = _uid(request)
    openai_api_key = os.getenv("OPENAI_API_KEY")
    nb_id          = req.notebook_id

    from sharepoint_client import download_file

    connected_db_sources: List[dict] = []
    ingested_documents:   List[dict] = []
    errors:               List[dict] = []

    for item in req.files:
        try:
            logger.info(f"[SharePoint] Downloading: {item.path}")
            content = download_file(
                token=req.token,
                site_id=req.site_id,
                drive_id=item.drive_id,
                item_id=item.item_id,
            )

            # ── Database pipeline: CSV / Excel ─────────────────────────────
            if item.pipeline == "database":
                from database_manager import DatabaseManager

                csv_dir = Path(__file__).resolve().parent / "data" / "user_csvs" / (uid or "anon")
                csv_dir.mkdir(parents=True, exist_ok=True)

                base_name  = item.name.rsplit(".", 1)[0]
                safe_alias = re.sub(r"[^a-zA-Z0-9_]", "_", base_name)
                if safe_alias and safe_alias[0].isdigit():
                    safe_alias = f"sp_{safe_alias}"
                alias = safe_alias or "sharepoint_file"

                # Excel → convert to CSV in-memory
                if item.extension in (".xlsx", ".xls"):
                    import pandas as pd
                    df        = pd.read_excel(io.BytesIO(content))
                    csv_bytes = df.to_csv(index=False).encode("utf-8")
                    save_name = f"{alias}.csv"
                else:
                    csv_bytes = content
                    save_name = f"{alias}.csv"

                csv_path = csv_dir / save_name
                csv_path.write_bytes(csv_bytes)

                manager = DatabaseManager.from_csv(
                    csv_path=str(csv_path),
                    schema_alias=_schema_alias(uid, alias),
                    original_filename=item.name,
                )
                # Tag as sharepoint so SourcesPanel can show the right icon/label
                manager.source_origin = "sharepoint"

                tables = manager.get_all_tables_unfiltered()
                nb_sources = _get_nb_sources(uid, nb_id)
                old_manager = nb_sources.get(alias)
                if old_manager is not None and old_manager is not manager:
                    try: old_manager.cleanup()
                    except Exception as e: logger.warning(f"cleanup() failed for replaced SharePoint source '{alias}': {e}")
                nb_sources[alias] = manager
                _get_nb_engine(uid, nb_id).add_source(alias, manager)
                asyncio.ensure_future(
                    _run_schema_generation(manager, tables, openai_api_key or "", uid, notebook_id=nb_id)
                )
                import user_store
                # Save as db_type="sharepoint" so it surfaces as SharePoint in the UI
                user_store.save_data_source(
                    uid or "anon", alias, "sharepoint",
                    {"csv_path": str(csv_path), "original_filename": item.name,
                     "sharepoint_site": req.site_url},
                    [], notebook_id=nb_id,
                )
                connected_db_sources.append({"alias": alias, "tables": tables, "file": item.name})
                logger.info(f"[SharePoint] DB connected: {alias} ({len(tables)} tables)")

            # ── Document pipeline: PDF / DOCX / PPTX / TXT ────────────────
            elif item.pipeline == "document":
                from rag_engine import RAGEngine
                nb_rag = RAGEngine(notebook_id=nb_id) if nb_id else global_rag_engine

                result = await doc_mgr.upload_document(
                    filename=item.name,
                    content=content,
                    category=req.category,
                    description=f"Imported from SharePoint: {item.path}",
                    notebook_id=nb_id,
                    metadata_tags={"source_type": "sharepoint", "sharepoint_path": item.path},
                )
                chunk_count = nb_rag.ingest_document(
                    doc_id=result["doc_id"],
                    text=result["text_content"],
                    metadata={
                        "filename":        item.name,
                        "category":        req.category,
                        "source_type":     "sharepoint",
                        "sharepoint_path": item.path,
                    },
                )
                ingested_documents.append({
                    "doc_id":   result["doc_id"],
                    "filename": item.name,
                    "chunks":   chunk_count,
                })
                logger.info(f"[SharePoint] Doc ingested: {item.name} ({chunk_count} chunks)")

        except Exception as e:
            logger.error(f"[SharePoint] Failed to process {item.name}: {e}", exc_info=True)
            errors.append({"file": item.name, "error": str(e)})

    bump_and_push(uid)
    return {
        "success":               True,
        "connected_db_sources":  connected_db_sources,
        "ingested_documents":    ingested_documents,
        "errors":                errors,
    }