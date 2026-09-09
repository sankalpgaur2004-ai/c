"""
pbix_service.py
----------------
Direct .pbix file upload as an alternative to the credentials-based Power BI
connection in powerbi_service.py.

Architecture (revised): NO SQL/SQLite layer at all. Once a pbix is imported
into the shared workspace via Power BI's Import API, it becomes a genuinely
live, DAX-queryable Analysis Services dataset — rendering from the file's
own embedded data snapshot, no live gateway connection needed back to
whatever database originally fed it. That means the EXISTING DAX pipeline
in powerbi_service.py (Stage 1/Stage 2, execute_dax, run_chat) works on it
completely unmodified — a pbix-imported dashboard's config is, from that
point on, indistinguishable from a credentialed dashboard's config.

What THIS module owns:
  1. Schema extraction via pbixray instead of a live XMLA connection — this
     avoids the IsHidden/hidden-table problems and DAX sample-value fetch
     failures the XMLA path can hit, since sample values come straight from
     materialized rows instead of live queries. The resulting schema is
     saved into the EXACT SAME location/format as credentialed dashboards'
     schemas (powerbi_service.SCHEMA_DIR, via _save_yaml_schema /
     _load_yaml_schema, keyed by report_id) — once saved, it's genuinely
     indistinguishable from an XMLA-built schema to every downstream
     consumer (Stage 1 selection, Stage 2 DAX generation, meta-question
     answering, page scoping — none of it needs to know where the schema
     came from).
  2. Publishing the pbix into the shared workspace (import_pbix_to_workspace),
     using the same Azure AD service principal already hardcoded in the
     credentialed connection flow.
  3. Content-based dedup so re-uploading identical content (even under a
     different filename) never repeats either the LLM enrichment or the
     Import API call — both keyed off a fingerprint of the model's actual
     structure, not the file's bytes (Power BI Desktop can re-zip an
     unchanged model with different internal timestamps, changing the
     file's bytes without changing what's in it).
  4. Dashboard row registration — per-user (dashboard ownership in this
     app's schema is scoped by user_id), reusing an existing row for that
     user+fingerprint if one still exists, and tolerating the case where a
     previous row was deleted (schema/import must never be deleted along
     with it — see process_pbix_upload's staleness check).

Schema build and workspace import run CONCURRENTLY (not sequentially) on a
genuinely new upload, since they're independent work — one is local
computation, the other is a network round-trip to Power BI Service.
"""
from __future__ import annotations

import os
import json
import time
import hashlib
import logging
import zipfile
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd
from pbixray import PBIXRay

logger = logging.getLogger(__name__)

# ── Storage layout ──────────────────────────────────────────────────────────
# Only the fingerprint dedup index lives here now — the actual schema is
# saved into powerbi_service.SCHEMA_DIR, same as every credentialed
# dashboard's schema, so it's found by the exact same code path.
BASE_DIR = Path(os.path.dirname(__file__)) / "data" / "pbix_sources"
INDEX_PATH = BASE_DIR / "fingerprint_index.json"
BASE_DIR.mkdir(parents=True, exist_ok=True)


# ── Shared Power BI credentials ──────────────────────────────────────────────
# Same service principal + workspace already hardcoded in the credentialed
# flow (AddSourcesOverlay.tsx's AddDashboardFlow). Used here to publish an
# uploaded pbix into that same workspace via the Import API, so the existing
# embed-token flow (powerbi.py) works completely unchanged afterward.
PBI_CREDS = {
    "azure_client_id":     os.getenv("AZURE_CLIENT_ID", ""),
    "azure_client_secret":  os.getenv("AZURE_CLIENT_SECRET", ""),
    "azure_tenant_id":     os.getenv("AZURE_TENANT_ID", ""),
    "workspace_id":        os.getenv("WORKSPACE_ID", ""),
    "workspace_name":      "Agentic BI",
}

POWERBI_API_URL = "https://api.powerbi.com/v1.0/myorg"


def _get_pbi_access_token() -> str:
    resp = requests.post(
        f"https://login.microsoftonline.com/{PBI_CREDS['azure_tenant_id']}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": PBI_CREDS["azure_client_id"],
            "client_secret": PBI_CREDS["azure_client_secret"],
            "scope": "https://analysis.windows.net/powerbi/api/.default",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def import_pbix_to_workspace(pbix_path: str, dataset_display_name: str,
                              timeout_seconds: int = 180) -> dict:
    """
    Publishes a .pbix file into the shared workspace via Power BI's Import
    API, giving it a real report_id/dataset_id — after which it's just a
    normal Power BI Service report, embeddable through the EXISTING
    embed-token endpoint with zero changes there, and DAX-queryable through
    the EXISTING run_chat/execute_dax pipeline with zero changes there either.

    Raises on failure (network error, Power BI rejecting the file, import
    timing out) — caller decides how to handle/report that.
    """
    token = _get_pbi_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    with open(pbix_path, "rb") as f:
        pbix_bytes = f.read()

    import_url = (
        f"{POWERBI_API_URL}/groups/{PBI_CREDS['workspace_id']}/imports"
        f"?datasetDisplayName={dataset_display_name}&nameConflict=GenerateUniqueName"
    )
    resp = requests.post(
        import_url, headers=headers,
        files={"file": (f"{dataset_display_name}.pbix", pbix_bytes, "application/octet-stream")},
        timeout=120,
    )
    resp.raise_for_status()
    import_id = resp.json()["id"]

    # Import is asynchronous — poll until Power BI finishes processing it.
    status_url = f"{POWERBI_API_URL}/groups/{PBI_CREDS['workspace_id']}/imports/{import_id}"
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        status_resp = requests.get(status_url, headers=headers, timeout=30)
        status_resp.raise_for_status()
        status_data = status_resp.json()
        state = status_data.get("importState")

        if state == "Succeeded":
            report = status_data["reports"][0]
            dataset = status_data["datasets"][0]
            logger.info(
                f"pbix_service: published to workspace — "
                f"report_id={report['id']} dataset_id={dataset['id']}"
            )
            return {
                "report_id": report["id"],
                "dataset_id": dataset["id"],
                "workspace_id": PBI_CREDS["workspace_id"],
            }
        if state == "Failed":
            raise RuntimeError(f"Power BI import failed: {status_data.get('error')}")

        time.sleep(3)

    raise TimeoutError(f"Power BI import did not finish within {timeout_seconds}s")


# ── Fingerprint index (dedup) ────────────────────────────────────────────────
# Maps content fingerprint -> {report_id, dataset_id, workspace_id,
# dashboards: {user_id: dashboard_id}}. This is the ONLY thing that tells us
# "have we already imported this exact content, and does its schema already
# exist" without re-parsing/re-hashing every table on every upload attempt.
def _load_index() -> dict:
    if INDEX_PATH.exists():
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_index(index: dict):
    # Defensive re-assert: closes the race/edge-case where BASE_DIR was
    # removed, not yet materialized (e.g. cloud-sync folders like OneDrive),
    # or otherwise missing by the time this write happens, even though the
    # module-level mkdir() ran earlier at import time. Cheap and idempotent
    # when the directory already exists.
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)


def get_fingerprint_for_sqlite_path(db_path: str) -> Optional[str]:
    """
    Looks up the pbix content-fingerprint (if any) associated with a given
    sqlite db_path.

    Under the current architecture (see module docstring), a pbix upload
    is imported straight into Power BI as a DAX-queryable dataset — there
    is NO sqlite layer for pbix-derived data at all. That means, today,
    this will always return None for every sqlite source: sqlite sources
    come from the credentials-based/CSV/db ingestion path, never from a
    pbix upload. This function exists so callers (e.g. sql_generator's
    _pbix_context_block) can safely ask the question without needing to
    know that fact — it degrades to a no-op instead of raising, matching
    the "safe no-op for any source that isn't a pbix upload" contract.

    If a future revision reintroduces a sqlite mirror for pbix sources,
    this is the place to add the real db_path -> fingerprint lookup
    (e.g. by recording db_path in the fingerprint index entries at
    upload time and matching against it here).
    """
    if not db_path:
        return None
    try:
        index = _load_index()
    except Exception as e:
        logger.warning(f"pbix_service: fingerprint index unreadable, skipping pbix context: {e}")
        return None

    for fingerprint, entry in index.items():
        if entry.get("db_path") == db_path:
            return fingerprint
    return None


def load_enriched_schema(fingerprint: str) -> Optional[dict]:
    """
    Loads the enriched schema (measures, relationships, descriptions) for
    a given pbix content-fingerprint, by resolving fingerprint -> report_id
    via the dedup index and then reading the same YAML file every
    credentialed dashboard's schema lives in (powerbi_service.SCHEMA_DIR).

    Returns None if the fingerprint is unknown or nothing has been saved
    for its report_id yet — callers should treat that as "no extra
    context available" rather than an error.
    """
    if not fingerprint:
        return None
    try:
        index = _load_index()
        entry = index.get(fingerprint)
        if not entry:
            return None
        report_id = entry.get("report_id")
        if not report_id:
            return None
        import powerbi_service
        return powerbi_service._load_yaml_schema(report_id)
    except Exception as e:
        logger.warning(f"pbix_service: failed to load enriched schema for fingerprint={fingerprint}: {e}")
        return None


def is_pbix_origin_report(report_id: str) -> bool:
    """
    Backward-compatible detector for dashboards created BEFORE dashboard
    configs carried an explicit "source": "pbix" marker — cross-references
    the fingerprint dedup index, since every report_id this module has ever
    published via the Import API is recorded there. Callers should prefer
    checking config.get("source") == "pbix" directly when available (it's
    free — no disk read); this exists as a fallback so already-created
    dashboards aren't silently treated as credentialed just because they
    predate the marker.
    """
    if not report_id:
        return False
    try:
        index = _load_index()
        return any(entry.get("report_id") == report_id for entry in index.values())
    except Exception:
        return False


# ── Extraction (pbixray) ──────────────────────────────────────────────────────
def extract_pbix_model(pbix_path: str) -> dict:
    """
    Tables (as real DataFrames), schema, relationships, measures, and
    page->visual mapping from the report layout. Returns a dict, not yet
    fingerprinted or saved anywhere.
    """
    model = PBIXRay(pbix_path)

    tables: dict[str, pd.DataFrame] = {}
    for tname in model.tables:
        df = model.get_table(tname)
        if df is None or len(df.columns) == 0:
            continue  # calculated tables (e.g. Clm type, Brand_Mapping) — no materialized rows via get_table()
        tables[tname] = df

    schema_df = model.schema  # TableName, ColumnName, PandasDataType
    schema = schema_df.to_dict("records")

    rel_df = model.relationships
    relationships = [
        {
            "from_table": r["FromTableName"],
            "from_column": r["FromColumnName"],
            "to_table": r["ToTableName"],
            "to_column": r["ToColumnName"],
            "is_active": bool(r.get("IsActive", True)),
        }
        for _, r in rel_df.iterrows()
    ] if rel_df is not None and len(rel_df) else []

    measures_df = model.dax_measures
    measures = [
        {
            "name": r["Name"],
            "expression": r["Expression"],
            "assigned_table": r["TableName"],
        }
        for _, r in measures_df.iterrows()
    ] if measures_df is not None and len(measures_df) else []

    pages = _extract_pages(pbix_path)

    return {
        "tables": tables,
        "schema": schema,
        "relationships": relationships,
        "measures": measures,
        "pages": pages,
    }


def _extract_pages(pbix_path: str) -> list:
    """Report/Layout JSON -> page/visual/measure/column mapping — exact
    ground truth from the report's own layout, not fuzzy title-matching
    like the XMLA path's REST-API-based page mapping has to do. Non-fatal:
    a layout-read failure shouldn't block the data-extraction path."""
    try:
        with zipfile.ZipFile(pbix_path, "r") as z:
            raw = z.read("Report/Layout")
        try:
            layout = json.loads(raw.decode("utf-16-le"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            layout = json.loads(raw.decode("utf-8"))

        pages = []
        for section in layout.get("sections", []):
            visuals = []
            for v in section.get("visualContainers", []):
                try:
                    config = json.loads(v.get("config", "{}"))
                except json.JSONDecodeError:
                    continue
                sv = config.get("singleVisual", {})

                title = None
                try:
                    title = sv["vcObjects"]["title"][0]["properties"]["text"]["expr"]["Literal"]["Value"]
                    title = title.strip("'").strip() if title else None
                except (KeyError, IndexError, TypeError):
                    pass

                measures, columns = [], []
                for sel in sv.get("prototypeQuery", {}).get("Select", []):
                    name = sel.get("Name", "")
                    if "Measure" in sel:
                        measures.append(name.split(".", 1)[-1])
                    elif "Column" in sel:
                        columns.append(name)

                if title or measures or columns:
                    visuals.append({
                        "visual_type": sv.get("visualType"),
                        "title": title,
                        "measures": measures,
                        "columns": columns,
                    })
            pages.append({
                "name": section.get("displayName"),
                "visuals": visuals,
            })
        return pages
    except Exception as e:
        logger.warning(f"pbix_service: failed to extract report pages (non-fatal): {e}")
        return []


# ── Fingerprinting ────────────────────────────────────────────────────────────
def compute_model_fingerprint(model: dict) -> str:
    """
    Hashes model CONTENT, not file bytes — Power BI Desktop can re-zip an
    unchanged model with different internal timestamps, which would change
    the file's bytes without changing what's actually in it. Deliberately
    excludes actual row data (only structure: table/column names+types,
    relationships, measure names+expressions) so this stays fast and isn't
    sensitive to incidental row-order differences.
    """
    canonical = {
        "tables": sorted(
            [{"name": tname, "columns": sorted(list(df.columns))}
             for tname, df in model["tables"].items()],
            key=lambda x: x["name"],
        ),
        "schema": sorted(
            [(r["TableName"], r["ColumnName"], str(r["PandasDataType"])) for r in model["schema"]]
        ),
        "relationships": sorted(
            [(r["from_table"], r["from_column"], r["to_table"], r["to_column"]) for r in model["relationships"]]
        ),
        "measures": sorted(
            [(m["name"], m["expression"]) for m in model["measures"]]
        ),
    }
    blob = json.dumps(canonical, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:24]


# ── Measure description batching ─────────────────────────────────────────────
def _generate_batch_measure_descriptions(measures_batch: list) -> dict:
    """
    Describes up to ~8 measures in one GPT call — mirrors the column-batching
    pattern powerbi_service.py already uses for columns, but doesn't exist
    anywhere for measures yet (even the XMLA path calls
    _generate_measure_description one measure at a time). Batching is the
    direct fix for call count.
    """
    from powerbi_service import _gpt
    import json as _json

    measure_block = "\n\n".join([
        f"- {m['name']}:\n  DAX: {m['expression'][:400]}"
        for m in measures_batch
    ])
    prompt = f"""Describe each DAX measure below in 2-3 sentences: what it
calculates, which tables/columns it uses, and what filter conditions it applies.
Return valid JSON only, no markdown:
{{"measure_name": "description", ...}}

Measures:
{measure_block}"""
    raw = _gpt(
        "You are a precise data analyst describing Power BI DAX measures. "
        "Return only valid JSON, no markdown or backticks.",
        prompt, max_tokens=1500,
    )
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        return _json.loads(raw)
    except Exception:
        return {}  # fallback: batch gets blank descriptions, not a crash


def _build_pbix_page_measure_map(pages_raw: list) -> dict:
    """
    Converts our exact pbix-extracted page/visual list into the SAME
    {page_displayName: [measure_name, ...]} shape powerbi_service.py's XMLA
    path produces via REST-API + fuzzy LLM title-matching
    (_build_page_mappings) — ours is exact/ground-truth instead of
    fuzzy-matched, since we read the real visual field bindings directly
    from the report's own layout JSON.
    """
    page_map = {}
    for page in pages_raw:
        name = page.get("name") or ""
        measures = set()
        for v in page.get("visuals", []):
            measures.update(v.get("measures", []))
        page_map[name] = sorted(measures)
    return page_map


def build_pbix_schema(model: dict, dataset_name: str) -> dict:
    """
    Builds the enriched schema dict in the EXACT shape powerbi_service.py's
    build_enriched_schema() assembles as final_schema — reuses its pure
    LLM-prompting helpers directly (_generate_table_description,
    _generate_batch_column_descriptions, _find_dependent_measures). Does
    NOT save it — saving requires a report_id, which only exists once the
    workspace import completes, and these two run concurrently (see
    process_pbix_upload) so the report_id may not be known yet when this
    returns.
    """
    from powerbi_service import (
        _generate_table_description,
        _generate_batch_column_descriptions,
        _find_dependent_measures,
    )

    tables = model["tables"]  # {tname: DataFrame}
    measures = model["measures"]

    sample_values_all: dict = {}
    sample_rows_all: dict = {}
    for tname, df in tables.items():
        sv_map = {}
        for col in df.columns:
            non_null = df[col].dropna().astype(str)
            sv_map[col] = non_null.unique()[:5].tolist()
        sample_values_all[tname] = sv_map
        sample_rows_all[tname] = df.head(3).to_dict("records")

    # ── Column descriptions (batched 10/call, parallel) ──
    col_desc_results: dict = {}

    def _describe_col_batch(tname, batch, sv_map):
        try:
            return tname, _generate_batch_column_descriptions(tname, batch, sv_map)
        except Exception as e:
            logger.warning(f"pbix_service: column description batch failed for {tname}: {e}")
            return tname, {}

    col_futures = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for tname, df in tables.items():
            cols = [{"name": c, "type": str(df[c].dtype)} for c in df.columns]
            sv_map = sample_values_all[tname]
            for i in range(0, len(cols), 10):
                batch = cols[i:i + 10]
                col_futures.append(ex.submit(_describe_col_batch, tname, batch, sv_map))
        for fut in as_completed(col_futures):
            tname, result = fut.result()
            for col_name, desc in result.items():
                col_desc_results[f"{tname}.{col_name}"] = desc

    # ── Table descriptions (parallel) ──
    table_desc_results: dict = {}

    def _describe_table(tname, df):
        cols_with_samples = [
            {"name": c, "type": str(df[c].dtype), "sample_values": sample_values_all[tname].get(c, [])}
            for c in df.columns
        ]
        try:
            return tname, _generate_table_description(tname, cols_with_samples, sample_rows_all[tname])
        except Exception as e:
            logger.warning(f"pbix_service: table description failed for {tname}: {e}")
            return tname, ""

    with ThreadPoolExecutor(max_workers=10) as ex:
        t_futures = {ex.submit(_describe_table, tname, df): tname for tname, df in tables.items()}
        for fut in as_completed(t_futures):
            tname, desc = fut.result()
            table_desc_results[tname] = desc

    # ── Measure descriptions — batched, parallel ──
    measure_desc_results: dict = {}

    def _describe_measure_batch(batch):
        try:
            return _generate_batch_measure_descriptions(batch)
        except Exception as e:
            logger.warning(f"pbix_service: measure description batch failed: {e}")
            return {}

    measure_batches = [measures[i:i + 8] for i in range(0, len(measures), 8)]
    with ThreadPoolExecutor(max_workers=8) as ex:
        m_futures = [ex.submit(_describe_measure_batch, batch) for batch in measure_batches]
        for fut in as_completed(m_futures):
            measure_desc_results.update(fut.result())

    enriched_measures = []
    for m in measures:
        deps = _find_dependent_measures(m["expression"], measures)
        enriched_measures.append({
            "name": m["name"],
            "description": measure_desc_results.get(m["name"], ""),
            "expression": m["expression"],
            "assigned_table": m["assigned_table"],
            "page_group": "",
            "dependent_measures": deps,
        })

    enriched_tables = []
    for tname, df in tables.items():
        cols_enriched = []
        for c in df.columns:
            key = f"{tname}.{c}"
            cols_enriched.append({
                "name": c,
                "type": str(df[c].dtype),
                "sample_values": sample_values_all[tname].get(c, []),
                "description": col_desc_results.get(key, ""),
            })
        enriched_tables.append({
            "name": tname,
            "description": table_desc_results.get(tname, ""),
            "columns": cols_enriched,
        })

    page_map = _build_pbix_page_measure_map(model.get("pages", []))

    # Shape matches powerbi_service.py's final_schema EXACTLY — see the
    # assembly right before _save_yaml_schema(report_id, final_schema)
    # there. This is what makes the schema genuinely indistinguishable
    # from an XMLA-built one to every downstream consumer.
    return {
        "dataset": dataset_name,
        "workspace": PBI_CREDS["workspace_name"],
        "report_name": dataset_name,
        "tables": enriched_tables,
        "measures": enriched_measures,
        "relationships": model["relationships"],
        "pages": page_map,
    }


def process_pbix_upload(
    pbix_path: str,
    user_id: Optional[str],
    notebook_id: Optional[str],
    requested_alias: Optional[str],
    original_filename: Optional[str] = None,
) -> dict:
    """
    Full pipeline: extract -> fingerprint -> (schema build + workspace
    import, concurrently, only for whichever piece is genuinely missing) ->
    dashboard registration, indistinguishable from a credentialed dashboard
    from that point on.

    Dedup is checked independently for each expensive step:
      - has_import: this exact content was already published to the
        workspace before (by anyone) — Import API is never called twice
        for the same content.
      - has_schema: this exact content already has a saved schema at
        powerbi_service._schema_path(report_id) — LLM enrichment is never
        repeated for the same content.
      - Dashboard row: reused per-user if this user already has one for
        this fingerprint AND that row still exists (handles the case where
        it was deleted — schema/import are never deleted along with a
        dashboard row, so re-adding must never trigger a rebuild).

    original_filename matters: pbix_path is wherever the upload was staged
    to disk (typically a random tempfile path), which is NOT what should
    show up as the dataset name.
    """
    from database import create_dashboard, connect_dashboard_to_notebook, get_dashboard_by_id
    from powerbi_service import _save_yaml_schema, _load_yaml_schema
    from fastapi import HTTPException

    model = extract_pbix_model(pbix_path)
    if not model["tables"]:
        raise HTTPException(status_code=400, detail="No tables found in this .pbix file")

    fingerprint = compute_model_fingerprint(model)
    index = _load_index()
    entry = index.get(fingerprint, {})
    dataset_name = os.path.splitext(original_filename or os.path.basename(pbix_path))[0]

    has_import = bool(entry.get("report_id") and entry.get("dataset_id"))
    has_schema = has_import and bool(_load_yaml_schema(entry["report_id"]))

    reused_import = has_import
    reused_schema = has_schema

    if not has_import or not has_schema:
        # Run whichever piece(s) are missing CONCURRENTLY — independent
        # work (local LLM calls vs. a network round-trip to Power BI).
        with ThreadPoolExecutor(max_workers=2) as ex:
            import_future = None if has_import else ex.submit(
                import_pbix_to_workspace, pbix_path, f"pbix_{fingerprint}"
            )
            schema_future = None if has_schema else ex.submit(
                build_pbix_schema, model, dataset_name
            )

            if import_future is not None:
                publish_result = import_future.result()
                entry.update(publish_result)
                logger.info(f"pbix_service: published new import for fingerprint={fingerprint}")

            final_schema = schema_future.result() if schema_future is not None else None

        if final_schema is not None:
            # Only knowable now — report_id may have just been produced
            # above, or may already have existed if only schema was missing.
            _save_yaml_schema(entry["report_id"], final_schema)
            logger.info(
                f"pbix_service: schema saved for fingerprint={fingerprint}, "
                f"report_id={entry['report_id']}"
            )

        index[fingerprint] = entry
        _save_index(index)
    else:
        logger.info(
            f"pbix_service: fully reusing existing import+schema for "
            f"fingerprint={fingerprint} — no LLM calls, no Import API call"
        )

    # ── Dashboard row — per-user, since dashboard ownership in this schema
    #    is scoped by user_id (get_dashboards_for_notebook filters by it).
    #    Reuse this user's existing row for this fingerprint if it still
    #    exists; a deleted row must never trigger a rebuild of schema/import,
    #    just creation of a fresh row pointing at the SAME report_id. ──
    dashboards_by_user = entry.setdefault("dashboards", {})
    owner_key = user_id or "anon"
    dashboard_id = dashboards_by_user.get(owner_key)

    if dashboard_id is not None and not get_dashboard_by_id(dashboard_id, owner_key):
        logger.info(
            f"pbix_service: dashboard {dashboard_id} for user={owner_key} no longer "
            f"exists (deleted) — creating a fresh row, reusing existing report_id"
        )
        dashboard_id = None

    if dashboard_id is None:
        dashboard_config = {
            **PBI_CREDS,
            "report_id": entry["report_id"],
            "dataset_id": entry["dataset_id"],
            "dataset_name": dataset_name,
            # Explicit marker — nothing else distinguishes this config from a
            # credentialed dashboard's (same shared service principal, same
            # workspace), so without this, downstream code (schema rebuild
            # especially) has no way to know this dashboard's schema was
            # extracted from the uploaded file via pbixray and should never
            # be regenerated by re-running the XMLA path against it.
            "source": "pbix",
        }
        dashboard = create_dashboard(
            user_id=owner_key, name=dataset_name, type_="powerbi", config=dashboard_config,
        )
        dashboard_id = dashboard["id"]
        dashboards_by_user[owner_key] = dashboard_id
        index[fingerprint] = entry
        _save_index(index)
        logger.info(f"pbix_service: created dashboard {dashboard_id} for user={owner_key}")
    else:
        logger.info(f"pbix_service: reusing existing dashboard {dashboard_id} for user={owner_key}")

    if notebook_id:
        connect_dashboard_to_notebook(notebook_id, dashboard_id, owner_key)

    return {
        "success": True,
        "fingerprint": fingerprint,
        "dashboard_id": dashboard_id,
        "report_id": entry["report_id"],
        "dataset_id": entry["dataset_id"],
        "reused_import": reused_import,
        "reused_schema": reused_schema,
    }