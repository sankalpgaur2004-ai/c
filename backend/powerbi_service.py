"""
services/powerbi_service.py
---------------------------
Schema discovery uses DuckDB + pbi_scanner extension — no ADODB/Windows needed.
Works on Windows, Linux and macOS.
Changes in this version:
  1. Replaced adodbapi/XMLA with cross-platform duckdb + pbi_scanner
  2. Parallel GPT calls for schema build (massive speed improvement)
  3. Batch column descriptions (10 columns per GPT call)
  4. Page → measures mapping via /pages/{name}/visuals REST endpoint
  5. dependent_measures detection per measure
  6. page_group tagging per measure
  7. Question classifier (descriptive vs specific)
  8. Multi-DAX execution for descriptive questions
  9. Active filter injection into DAX
 10. run_page_summary() — uses exported visual data directly, no DAX needed
 11. Updated Stage 1/2 prompts — ALLEXCEPT/dependent measure rules
 12. Schema build progress tracking
 13. Incremental rebuild — skips already-described items
"""

import json
import os
import re
import threading
import requests
import yaml

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional


from fastapi import HTTPException
from openai import OpenAI

from config import cfg

# ── OpenAI client ──────────────────────────────────────────────────────────────
_oai: OpenAI | None = None

def _get_oai() -> OpenAI:
    global _oai
    if _oai is None:
        _oai = OpenAI(api_key=cfg.openai_api_key)
    return _oai


# ── YAML schema store ──────────────────────────────────────────────────────────
SCHEMA_DIR = os.path.join(os.path.dirname(__file__), "schemas")
os.makedirs(SCHEMA_DIR, exist_ok=True)

def _schema_path(report_id: str) -> str:
    return os.path.join(SCHEMA_DIR, f"{report_id}.yaml")

def _load_yaml_schema(report_id: str) -> dict | None:
    path = _schema_path(report_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return None

def _save_yaml_schema(report_id: str, schema: dict):
    path = _schema_path(report_id)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(schema, f, allow_unicode=True, sort_keys=False)
    print(f"  Schema saved to {path}")


# ── Build progress tracking ────────────────────────────────────────────────────
_build_progress: dict = {}

def _set_progress(dashboard_id: int, status: str, step: str, percent: int, detail: str = ""):
    _build_progress[dashboard_id] = {
        "status": status, "step": step, "percent": percent, "detail": detail
    }
    print(f"  [{percent}%] {step}")

def get_build_progress(dashboard_id: int) -> dict:
    return _build_progress.get(dashboard_id, {
        "status": "idle", "step": None, "percent": 0, "detail": ""
    })


# ── Print helpers ──────────────────────────────────────────────────────────────
def _section(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


# ── Azure AD token ─────────────────────────────────────────────────────────────
def get_access_token(creds: dict) -> str:
    _section("Azure AD Token")
    required = ['azure_tenant_id', 'azure_client_id', 'azure_client_secret']
    missing = [k for k in required if not creds.get(k)]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing credentials: {', '.join(missing)}")

    url = (
        f"https://login.microsoftonline.com/{creds['azure_tenant_id']}"
        f"/oauth2/v2.0/token"
    )
    try:
        resp = requests.post(url, data={
            "grant_type":    "client_credentials",
            "client_id":     creds["azure_client_id"],
            "client_secret": creds["azure_client_secret"],
            "scope":         cfg.powerbi_scope,
        }, timeout=15)

        if not resp.ok:
            error_text = resp.text[:200]
            try:
                error_text = resp.json().get('error_description', error_text)
            except:
                pass
            raise HTTPException(status_code=502, detail=f"Azure AD failed: {error_text}")
        data = resp.json()
        print(f"  Token acquired (expires_in={data.get('expires_in')}s)")
        return data["access_token"]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Azure AD error: {str(e)[:200]}")


# ── Embed token ────────────────────────────────────────────────────────────────
def get_embed_token(creds: dict) -> dict:
    _section("Power BI Embed Token")
    required = ['workspace_id', 'report_id', 'dataset_id']
    missing = [k for k in required if not creds.get(k)]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing Power BI resources: {', '.join(missing)}")

    try:
        token   = get_access_token(creds)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        base    = cfg.powerbi_api_url
        ws_id   = creds["workspace_id"]
        rpt_id  = creds["report_id"]
        ds_id   = creds["dataset_id"]

        gen_url = f"{base}/groups/{ws_id}/reports/{rpt_id}/GenerateToken"
        r = requests.post(
            gen_url, headers=headers,
            json={"accessLevel": "view", "datasetId": ds_id},
            timeout=15,
        )
        if not r.ok:
            raise HTTPException(
                status_code=502,
                detail=f"GenerateToken error {r.status_code}: {r.text[:200]}",
            )
        token_data = r.json()

        rpt_r = requests.get(
            f"{base}/groups/{ws_id}/reports/{rpt_id}",
            headers=headers, timeout=15,
        )
        if not rpt_r.ok:
            raise HTTPException(status_code=502, detail=f"Could not fetch report metadata: {rpt_r.status_code}")

        print(f"  Embed token ready (tokenId={token_data.get('tokenId')})")
        return {
            "token":      token_data["token"],
            "tokenId":    token_data["tokenId"],
            "expiration": token_data["expiration"],
            "embedUrl":   rpt_r.json()["embedUrl"],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Embed token error: {str(e)[:200]}")


# ── Page & visual REST calls ───────────────────────────────────────────────────

def get_report_pages(creds: dict) -> list:
    """Fetch all pages in the report. Returns list of {name, displayName, order}."""
    token = get_access_token(creds)
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{cfg.powerbi_api_url}/groups/{creds['workspace_id']}/reports/{creds['report_id']}/pages"
    resp = requests.get(url, headers=headers, timeout=15)
    if not resp.ok:
        raise HTTPException(status_code=502, detail=f"Failed to fetch pages: {resp.status_code} {resp.text[:200]}")
    return resp.json().get("value", [])


def get_page_visuals(creds: dict, page_name: str) -> list:
    """Fetch all visuals on a specific page. Returns list of {title, type, position}."""
    token = get_access_token(creds)
    headers = {"Authorization": f"Bearer {token}"}
    url = (
        f"{cfg.powerbi_api_url}/groups/{creds['workspace_id']}"
        f"/reports/{creds['report_id']}/pages/{page_name}/visuals"
    )
    resp = requests.get(url, headers=headers, timeout=15)
    if not resp.ok:
        print(f"  Warning: could not fetch visuals for page {page_name}: {resp.status_code}")
        return []
    return resp.json().get("value", [])


def _get_report_metadata(creds: dict) -> dict:
    """
    Fetch report-level metadata (display name, webUrl) from the Power BI REST API.
    Used to answer "what is the title of my dashboard" style meta questions.
    Non-fatal — returns empty strings on any failure.
    """
    try:
        token = get_access_token(creds)
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{cfg.powerbi_api_url}/groups/{creds['workspace_id']}/reports/{creds['report_id']}"
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.ok:
            data = resp.json()
            return {"name": data.get("name", ""), "webUrl": data.get("webUrl", "")}
        print(f"  Warning: could not fetch report metadata: {resp.status_code}")
    except Exception as e:
        print(f"  Warning: report metadata fetch failed: {e}")
    return {"name": "", "webUrl": ""}


# ── Raw schema via DuckDB pbi_scanner (cross-platform, replaces ADODB) ────────
def _build_raw_schema(creds: dict) -> dict:
    """
    Fetch tables, columns, measures and relationships from Power BI using
    DuckDB + pbi_scanner community extension.
    Works on Windows, Linux and macOS — no ADODB/COM driver required.

    Install once per environment:
        pip install duckdb
        -- pbi_scanner installs automatically via DuckDB community extensions
    """
    import duckdb

    workspace  = creds.get("workspace_name", "")
    dataset    = creds.get("dataset_name", "")
    tenant_id  = creds["azure_tenant_id"]
    client_id  = creds["azure_client_id"]
    secret     = creds["azure_client_secret"]
    conn_str   = (
        f"Data Source=powerbi://api.powerbi.com/v1.0/myorg/{workspace};"
        f"Initial Catalog={dataset};"
    )

    print(f"  Fetching raw schema via DuckDB pbi_scanner "
          f"(workspace='{workspace}', dataset='{dataset}')")

    con = duckdb.connect()
    con.execute("INSTALL pbi_scanner FROM community;")
    con.execute("LOAD pbi_scanner;")
    con.execute("INSTALL azure; LOAD azure;")
    con.execute(f"""
        CREATE SECRET pbi_sp (
            TYPE azure,
            PROVIDER service_principal,
            TENANT_ID '{tenant_id}',
            CLIENT_ID '{client_id}',
            CLIENT_SECRET '{secret}'
        );
    """)
    print("  DuckDB pbi_scanner connected")

    # ── Tables ───────────────────────────────────────────────────────────────
    try:
        df_tables = con.execute(f"""
            SELECT "[Name]"
            FROM pbi_tables('{conn_str}', secret_name := 'pbi_sp')
            WHERE "[Name]" NOT LIKE 'LocalDateTable%'
            AND "[Name]" NOT LIKE 'DateTableTemplate%'
            AND "[Name]" NOT LIKE '$%'
            AND SUBSTR("[Name]", 1, 1) != '<'
            AND "[IsHidden]" = false
        """).fetchdf()
    except Exception:
        # Older pbi_scanner may not have [IsHidden] — fall back without it
        df_tables = con.execute(f"""
            SELECT "[Name]"
            FROM pbi_tables('{conn_str}', secret_name := 'pbi_sp')
            WHERE "[Name]" NOT LIKE 'LocalDateTable%'
            AND "[Name]" NOT LIKE 'DateTableTemplate%'
            AND "[Name]" NOT LIKE '$%'
            AND SUBSTR("[Name]", 1, 1) != '<'
        """).fetchdf()

    table_names    = df_tables["[Name]"].tolist()
    visible_tables = set(table_names)
    table_map      = {i: name for i, name in enumerate(table_names)}
    tables_schema  = {name: {"columns": []} for name in visible_tables}
    print(f"  Tables — {len(visible_tables)} visible")

    # ── Columns ──────────────────────────────────────────────────────────────
    try:
        df_cols = con.execute(f"""
            SELECT "[Name]", "[Table]", "[DataType]"
            FROM pbi_columns('{conn_str}', secret_name := 'pbi_sp')
            WHERE "[Type]" != 'RowNumber'
            AND "[Name]" NOT LIKE 'RowNumber%'
            AND "[Table]" NOT LIKE 'LocalDateTable%'
            AND "[Table]" NOT LIKE 'DateTableTemplate%'
            AND "[Table]" NOT LIKE '$%'
            AND SUBSTR("[Table]", 1, 1) != '<'
            AND "[IsHidden]" = false
        """).fetchdf()
    except Exception:
        # Older pbi_scanner versions may not expose [IsHidden] — fall back without it
        df_cols = con.execute(f"""
            SELECT "[Name]", "[Table]", "[DataType]"
            FROM pbi_columns('{conn_str}', secret_name := 'pbi_sp')
            WHERE "[Type]" != 'RowNumber'
            AND "[Name]" NOT LIKE 'RowNumber%'
            AND "[Table]" NOT LIKE 'LocalDateTable%'
            AND "[Table]" NOT LIKE 'DateTableTemplate%'
            AND "[Table]" NOT LIKE '$%'
            AND SUBSTR("[Table]", 1, 1) != '<'
        """).fetchdf()

    for _, row in df_cols.iterrows():
        tname = row["[Table]"]
        cname = row["[Name]"]
        dtype = str(row["[DataType]"])
        if tname in tables_schema and cname:
            tables_schema[tname]["columns"].append({
                "name": cname,
                "type": dtype,
            })
    total_cols = sum(len(v["columns"]) for v in tables_schema.values())
    print(f"  Columns — {total_cols} total")

    # ── Measures ─────────────────────────────────────────────────────────────
    df_measures = con.execute(f"""
        SELECT "[Name]", "[Table]", "[Expression]"
        FROM pbi_measures('{conn_str}', secret_name := 'pbi_sp')
    """).fetchdf()

    measures_list = []
    for _, row in df_measures.iterrows():
        mname = row["[Name]"]
        expr  = row["[Expression]"]
        tname = row["[Table]"]
        if mname:
            measures_list.append({
                "name":           mname,
                "expression":     str(expr)[:500] if expr else "",
                "assigned_table": tname if tname else "Unknown",
            })
    print(f"  Measures — {len(measures_list)} total")

    # ── Relationships ─────────────────────────────────────────────────────────
    df_rels = con.execute(f"""
        SELECT "[FromTable]", "[FromColumn]", "[ToTable]", "[ToColumn]"
        FROM pbi_relationships('{conn_str}', secret_name := 'pbi_sp')
        WHERE "[FromTable]" NOT LIKE 'LocalDateTable%'
        AND "[ToTable]" NOT LIKE 'LocalDateTable%'
    """).fetchdf()

    relationships = []
    for _, row in df_rels.iterrows():
        relationships.append({
            "from_table":  row["[FromTable]"],
            "from_column": row["[FromColumn]"],
            "to_table":    row["[ToTable]"],
            "to_column":   row["[ToColumn]"],
        })
    print(f"  Relationships — {len(relationships)} active")

    con.close()

    return {
        "tables":        tables_schema,
        "measures":      measures_list,
        "relationships": relationships,
        "table_map":     table_map,
    }


# ── Sample values via REST executeQueries ─────────────────────────────────────
def _execute_dax_rest(token: str, ws_id: str, ds_id: str, dax: str) -> list:
    url = f"{cfg.powerbi_api_url}/groups/{ws_id}/datasets/{ds_id}/executeQueries"
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"queries": [{"query": dax}], "serializerSettings": {"includeNulls": True}},
        timeout=30,
    )
    if not r.ok:
        print(f"  DAX failed ({r.status_code}): {r.text[:200]}")
        return []
    return r.json()["results"][0]["tables"][0].get("rows", [])


def _get_all_sample_values(token: str, ws_id: str, ds_id: str,
                            table: str, limit: int = 20) -> dict:
    rows = _execute_dax_rest(token, ws_id, ds_id, f"EVALUATE TOPN({limit}, '{table}')")
    col_values: dict[str, set] = {}
    for row in rows:
        for key, val in row.items():
            clean_key = key.replace(f"{table}[", "").replace("]", "")
            col_values.setdefault(clean_key, set())
            if val is not None and str(val).strip():
                col_values[clean_key].add(str(val))
    return {k: list(v)[:5] for k, v in col_values.items()}


def _get_sample_rows(token: str, ws_id: str, ds_id: str,
                      table: str, limit: int = 3) -> list:
    rows = _execute_dax_rest(token, ws_id, ds_id, f"EVALUATE TOPN({limit}, '{table}')")
    return [
        {k.replace(f"{table}[", "").replace("]", ""): v for k, v in row.items()}
        for row in rows
    ]


# ── AI description helpers ─────────────────────────────────────────────────────
def _gpt(system: str, user: str, max_tokens: int = 200) -> str:
    resp = _get_oai().chat.completions.create(
        model=cfg.llm_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        temperature=0,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


def _gpt_json(system: str, user: str, max_tokens: int = 400) -> dict:
    """
    Same as _gpt but requests a JSON object and parses it.

    Truncation safety: a JSON response that gets cut off mid-array/object by
    hitting max_tokens is NOT valid JSON, so json.loads() throws — but the
    raw text still LOOKS like it starts with real content (e.g. a partial
    `{"summary": ["item 1", "item 2",`), so a naive except-fallback that
    treats that raw text as a plain-prose answer ends up showing the user a
    mangled, cut-off JSON blob instead of an answer. Two layers of defense:

    1. If the model's own finish_reason says it was cut off ("length"), retry
       once immediately with a doubled budget (capped) BEFORE ever touching
       the fallback — this is the common case (a long list/table answer)
       and fixing it here means every caller gets more room automatically,
       with no per-caller token-tuning needed.
    2. If parsing still fails (genuinely malformed JSON, not just truncated,
       or retry budget exhausted), fall back to a short, honest message
       instead of ever echoing raw JSON syntax back to the user.
    """
    MAX_TOKENS_CAP = 4000
    resp = _get_oai().chat.completions.create(
        model=cfg.llm_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        temperature=0,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    choice       = resp.choices[0]
    raw          = choice.message.content.strip()
    was_truncated = getattr(choice, "finish_reason", None) == "length"

    try:
        return json.loads(raw)
    except Exception:
        if was_truncated and max_tokens < MAX_TOKENS_CAP:
            print(f"  _gpt_json: response truncated at max_tokens={max_tokens} — retrying with {min(max_tokens * 2, MAX_TOKENS_CAP)}")
            return _gpt_json(system, user, max_tokens=min(max_tokens * 2, MAX_TOKENS_CAP))
        # Genuinely malformed (or retry budget exhausted) — never surface
        # raw JSON syntax to the user; fail into a short, honest message.
        print(f"  _gpt_json: could not parse JSON response (truncated={was_truncated}), len={len(raw)}")
        return {
            "answered": True,
            "format": "prose",
            "summary": "I found relevant data but ran into trouble formatting the full answer — could you ask again, maybe for a shorter or more specific slice of it?",
        }


# Shared JSON-response contract for every insight-generation call site
# (_format_insight, _format_multi_insight, run_page_summary) — kept in one
# place so "table" support, wording, etc. can't drift out of sync between
# them. "table" uses columns/rows instead of free text so the model never
# has to hand-align markdown pipe characters (fragile, especially with long
# rows near a token budget) — _render_insight_summary builds the actual
# table syntax deterministically from this structure.
_INSIGHT_FORMAT_SCHEMA_BLOCK = """Return ONLY a JSON object:
{{
  "answered": true or false — false ONLY if this data genuinely does not answer what the user asked,
  "format": "list" or "table" or "prose",
  "summary":
    - if format is "list": a JSON array of point strings (plain text per item,
      use **bold** for key numbers — no dashes/numbers, the app adds those),
    - if format is "table": an object {{"columns": ["Col1", "Col2", ...], "rows": [["v1","v2",...], ...]}}
      — use this whenever the user asks for a table, or the data naturally
      has several aligned fields per item (e.g. name + title + email),
    - otherwise (format "prose"): a single markdown string with **bold** for key numbers.

Choose the format based on what the user actually asked for (e.g. "in points",
"as a table", "list them", "briefly") — read their own words, don't guess from
a fixed keyword list. Default to "prose" only when they didn't ask for a
specific structure."""


def _render_insight_summary(result: dict) -> str:
    """
    Deterministic join — mirrors main.py's _render_summary. The model
    decides format itself (reading the question directly) and returns
    "summary" as a JSON array when it chose list format, or a
    {"columns": [...], "rows": [[...], ...]} object when it chose table
    format; this renders both deterministically (bullets / markdown table
    syntax) so formatting is always correct, no guessing or hand-aligned
    pipe characters needed from the model.
    """
    summary = result.get("summary") if result else None

    if isinstance(summary, dict) and "columns" in summary:
        cols = [str(c) for c in summary.get("columns", [])]
        rows = summary.get("rows", []) or []
        if not cols:
            return ""
        header    = "| " + " | ".join(cols) + " |"
        separator = "| " + " | ".join("---" for _ in cols) + " |"
        body_lines = []
        for row in rows:
            cells = [str(c) if c is not None else "" for c in row]
            # Pad/truncate defensively in case a row came back short/long
            cells = (cells + [""] * len(cols))[:len(cols)]
            body_lines.append("| " + " | ".join(cells) + " |")
        return "\n".join([header, separator] + body_lines)

    if isinstance(summary, list):
        return "\n".join(f"- {item}" for item in summary if str(item).strip())

    return summary


def _generate_table_description(table_name: str, columns: list, sample_rows: list) -> str:
    col_names = [c["name"] for c in columns]
    prompt = f"""Table: {table_name}
Columns: {col_names}
Sample rows: {json.dumps(sample_rows, indent=2, default=str)[:800]}
Write a 2-3 sentence description of what this table contains. Return plain text only."""
    return _gpt("You are a precise data analyst.", prompt, max_tokens=200)


def _generate_batch_column_descriptions(table_name: str, columns: list, sample_values_map: dict) -> dict:
    """Describe up to 10 columns in one GPT call. Returns {col_name: description}."""
    col_block = "\n".join([
        f"- {c['name']} (type={c['type']}): sample values = {sample_values_map.get(c['name'], [])[:3]}"
        for c in columns
    ])
    prompt = f"""Table: {table_name}
Describe each column in exactly one sentence. Return valid JSON only, no markdown:
{{"column_name": "one sentence description"}}

Columns:
{col_block}"""
    raw = _gpt(
        "You are a data analyst. Return only valid JSON, no markdown or backticks.",
        prompt, max_tokens=1200
    )
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except Exception:
        # fallback: return empty so individual columns get blank descriptions
        return {}


def _generate_measure_description(measure_name: str, expression: str) -> str:
    prompt = f"""Measure: {measure_name}
DAX Expression: {expression}

Analyze the DAX and write a 2-3 sentence description covering:
1. What it calculates based on the DAX logic
2. Which tables/columns it uses
3. What filters can be applied to it
Return plain text only."""
    return _gpt(
        "You are a precise data analyst. Base descriptions on DAX logic, not measure names.",
        prompt, max_tokens=200,
    )


# ── Detect dependent measures in a DAX expression ────────────────────────────
def _find_dependent_measures(expression: str, all_measures: list) -> list:
    """Find other measure names referenced inside this measure's DAX expression."""
    if not expression:
        return []
    measure_names = [m["name"] for m in all_measures]
    deps = []
    for name in measure_names:
        # Look for [MeasureName] pattern in expression
        if f"[{name}]" in expression:
            deps.append(name)
    return deps


# ── Map visual titles → measure names (fuzzy via LLM) ────────────────────────
def _map_visuals_to_measures(visual_titles: list, all_measures: list) -> list:
    """Given a list of visual titles from a page, return the matching measure names."""
    if not visual_titles:
        return []

    measure_names = [m["name"] for m in all_measures]
    prompt = f"""You are a Power BI expert.
Below are visual titles from a report page, and a list of all available measures.
Map each visual title to the most likely measure name(s) from the list.
Return only the matched measure names as a JSON array — no visual titles, no explanation.
If a visual title doesn't match any measure, skip it.

Visual titles: {json.dumps(visual_titles)}
Available measures: {json.dumps(measure_names)}

Return JSON array only: ["Measure Name 1", "Measure Name 2", ...]"""
    raw = _gpt(
        "You are a Power BI expert. Return only a JSON array of measure names.",
        prompt, max_tokens=500
    )
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        matched = json.loads(raw)
        # Validate — only return names that actually exist in schema
        valid = {m["name"] for m in all_measures}
        return [m for m in matched if m in valid]
    except Exception:
        return []


# ── Build page → measures mapping ─────────────────────────────────────────────
def _build_page_mappings(creds: dict, all_measures: list) -> dict:
    """
    Calls REST API to get all pages and their visuals.
    Maps visual titles to measure names via LLM fuzzy match.
    Returns {page_displayName: [measure_name, ...]}
    """
    print("\n  Building page → measures mapping...")
    try:
        pages = get_report_pages(creds)
    except Exception as e:
        print(f"  Warning: could not fetch pages: {e}")
        return {}

    page_map = {}
    for page in pages:
        page_name         = page.get("name", "")
        page_display_name = page.get("displayName", page_name)
        try:
            visuals = get_page_visuals(creds, page_name)
            titles  = [v["title"] for v in visuals if v.get("title")]
            print(f"    Page '{page_display_name}': {len(visuals)} visuals, {len(titles)} titled")
            if titles:
                matched = _map_visuals_to_measures(titles, all_measures)
                print(f"    Mapped {len(matched)} measures: {matched}")
                page_map[page_display_name] = matched
            else:
                page_map[page_display_name] = []
        except Exception as e:
            print(f"    Warning: page '{page_display_name}' visuals failed: {e}")
            page_map[page_display_name] = []

    return page_map


# ── Full enriched schema build (parallel) ─────────────────────────────────────
def _is_pbix_dashboard(creds: dict) -> bool:
    """
    True for dashboards whose schema was extracted from an uploaded .pbix
    file (via pbix_service/pbixray) rather than a live XMLA connection.
    Checked first via the explicit config["source"] marker (free — no disk
    read); falls back to cross-referencing pbix_service's fingerprint index
    for dashboards created before that marker existed, so older PBIX
    dashboards aren't silently misidentified as credentialed.
    """
    if creds.get("source") == "pbix":
        return True
    try:
        import pbix_service
        return pbix_service.is_pbix_origin_report(creds.get("report_id", ""))
    except Exception:
        return False


def build_enriched_schema(creds: dict, dashboard_id: int = None, force_rebuild: bool = False) -> dict:
    """
    Parallel schema build:
      1. XMLA raw schema
      2. Sample values (parallel per table)
      3. Column descriptions (batched — 10 per GPT call, parallel)
      4. Table descriptions (parallel)
      5. Measure descriptions (parallel)
      6. Dependent measure detection
      7. Page → measures mapping (background thread after schema saved)
    """
    did = dashboard_id or 0

    # ── PBIX-origin dashboards never go through the XMLA path ──────────────
    # Their schema was built by pbixray reading the uploaded file directly
    # (deliberately, to avoid the IsHidden/hidden-table and DAX sample-value
    # problems the XMLA path can hit — see pbix_service.py's module
    # docstring). Re-running XMLA extraction against them is not just
    # wasted work, it can actively regress schema quality by reintroducing
    # exactly the problems the .pbix path was built to avoid. This applies
    # regardless of how we got here (a stale/expired cache, a session
    # reopen, or someone hitting the "rebuild schema" endpoint) — the fix
    # is always the same: reuse what's cached, or ask for a re-upload.
    if _is_pbix_dashboard(creds):
        cached = _load_yaml_schema(creds.get("report_id", ""))
        if cached:
            print(f"  build_enriched_schema: dashboard {did} is PBIX-origin — "
                  f"reusing cached schema, skipping XMLA rebuild entirely")
            _set_progress(did, "done", "Schema ready (reused, no rebuild needed)", 100)
            return cached
        _set_progress(did, "error", "PBIX schema missing — re-upload required", 0)
        raise RuntimeError(
            "This dashboard's schema was extracted from an uploaded .pbix file, and its "
            "cached schema is missing (was it deleted?). It cannot be rebuilt via a live "
            "Power BI connection — please re-upload the .pbix file instead."
        )

    _set_progress(did, "building", "Connecting to XMLA endpoint", 5)

    # ── Check existing schema for incremental build ──
    existing = None
    if not force_rebuild:
        existing = _load_yaml_schema(creds.get("report_id", ""))

    _section("Building Enriched Schema")
    _set_progress(did, "building", "Fetching raw schema via XMLA", 10)
    raw = _build_raw_schema(creds)

    token = get_access_token(creds)
    ws_id = creds["workspace_id"]
    ds_id = creds["dataset_id"]

    # ── Build existing lookup maps for incremental skip ──
    existing_table_desc   = {}
    existing_col_desc     = {}
    existing_measure_desc = {}
    if existing:
        for t in existing.get("tables", []):
            existing_table_desc[t["name"]] = t.get("description", "")
            for c in t.get("columns", []):
                existing_col_desc[f"{t['name']}.{c['name']}"] = c.get("description", "")
        for m in existing.get("measures", []):
            existing_measure_desc[m["name"]] = m.get("description", "")

    _set_progress(did, "building", "Fetching sample values (parallel)", 20)

    # ── Parallel sample value fetching ──
    table_names = list(raw["tables"].keys())
    sample_values_all: dict = {}
    sample_rows_all:   dict = {}

    def _fetch_samples(tname):
        sv = _get_all_sample_values(token, ws_id, ds_id, tname)
        sr = _get_sample_rows(token, ws_id, ds_id, tname)
        return tname, sv, sr

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch_samples, t): t for t in table_names}
        for fut in as_completed(futures):
            tname, sv, sr = fut.result()
            sample_values_all[tname] = sv
            sample_rows_all[tname]   = sr

    _set_progress(did, "building", "Generating column descriptions (parallel batches)", 35)

    # ── Parallel batched column descriptions ──
    # Build batch tasks: 10 columns per task per table
    col_desc_results: dict = {}  # {table.col: description}

    def _describe_col_batch(tname, batch_cols, sv_map):
        result = _generate_batch_column_descriptions(tname, batch_cols, sv_map)
        return tname, result

    batch_futures = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for tname, tdata in raw["tables"].items():
            cols    = tdata["columns"]
            sv_map  = sample_values_all.get(tname, {})
            # Split into batches of 10
            batches = [cols[i:i+10] for i in range(0, len(cols), 10)]
            for batch in batches:
                # Skip if all columns in batch already have descriptions
                needs_desc = [
                    c for c in batch
                    if not existing_col_desc.get(f"{tname}.{c['name']}", "")
                ]
                if not needs_desc:
                    # Use existing descriptions
                    for c in batch:
                        key = f"{tname}.{c['name']}"
                        col_desc_results[key] = existing_col_desc.get(key, "")
                    continue
                batch_futures.append(ex.submit(_describe_col_batch, tname, needs_desc, sv_map))

        for fut in as_completed(batch_futures):
            tname, result = fut.result()
            for col_name, desc in result.items():
                col_desc_results[f"{tname}.{col_name}"] = desc

    _set_progress(did, "building", "Generating table descriptions (parallel)", 55)

    # ── Parallel table descriptions ──
    table_desc_results: dict = {}

    def _describe_table(tname, tdata, sv_map, sr):
        if existing_table_desc.get(tname):
            return tname, existing_table_desc[tname]
        cols_with_samples = [
            {"name": c["name"], "type": c["type"],
             "sample_values": sv_map.get(c["name"], [])}
            for c in tdata["columns"]
        ]
        desc = _generate_table_description(tname, cols_with_samples, sr)
        return tname, desc

    with ThreadPoolExecutor(max_workers=10) as ex:
        t_futures = {
            ex.submit(
                _describe_table,
                tname, tdata,
                sample_values_all.get(tname, {}),
                sample_rows_all.get(tname, [])
            ): tname
            for tname, tdata in raw["tables"].items()
        }
        for fut in as_completed(t_futures):
            tname, desc = fut.result()
            table_desc_results[tname] = desc

    _set_progress(did, "building", "Generating measure descriptions (parallel)", 70)

    # ── Parallel measure descriptions ──
    measure_desc_results: dict = {}

    def _describe_measure(m):
        if existing_measure_desc.get(m["name"]):
            return m["name"], existing_measure_desc[m["name"]]
        desc = _generate_measure_description(m["name"], m["expression"])
        return m["name"], desc

    with ThreadPoolExecutor(max_workers=15) as ex:
        m_futures = {ex.submit(_describe_measure, m): m["name"] for m in raw["measures"]}
        for fut in as_completed(m_futures):
            mname, desc = fut.result()
            measure_desc_results[mname] = desc

    _set_progress(did, "building", "Detecting dependent measures", 82)

    # ── Assemble enriched tables ──
    enriched_tables = []
    for tname, tdata in raw["tables"].items():
        sv_map = sample_values_all.get(tname, {})
        cols_enriched = []
        for c in tdata["columns"]:
            key  = f"{tname}.{c['name']}"
            desc = col_desc_results.get(key) or existing_col_desc.get(key, "")
            cols_enriched.append({
                "name":          c["name"],
                "type":          c["type"],
                "sample_values": sv_map.get(c["name"], []),
                "description":   desc,
            })
        enriched_tables.append({
            "name":        tname,
            "description": table_desc_results.get(tname, ""),
            "columns":     cols_enriched,
        })

    # ── Assemble enriched measures with dependent_measures ──
    enriched_measures = []
    for m in raw["measures"]:
        deps = _find_dependent_measures(m["expression"], raw["measures"])

        # Preserve existing page_group if already set
        existing_pg = ""
        if existing:
            for em in existing.get("measures", []):
                if em["name"] == m["name"]:
                    existing_pg = em.get("page_group", "")
                    break

        enriched_measures.append({
            "name":               m["name"],
            "description":        measure_desc_results.get(m["name"], ""),
            "expression":         m["expression"],
            "assigned_table":     m["assigned_table"],
            "page_group":         existing_pg,
            "dependent_measures": deps,
        })

    # Report display name (for "what is the title of my dashboard" style questions).
    # Reuses the existing report metadata already fetched elsewhere (embed-token) —
    # kept non-fatal since it's cosmetic, never blocks schema build.
    report_name = ""
    if existing and existing.get("report_name"):
        report_name = existing["report_name"]
    else:
        report_name = _get_report_metadata(creds).get("name", "")

    final_schema = {
        "dataset":       creds.get("dataset_name", ""),
        "workspace":     creds.get("workspace_name", ""),
        "report_name":   report_name,
        "tables":        enriched_tables,
        "measures":      enriched_measures,
        "relationships": raw["relationships"],
        "pages":         existing.get("pages", {}) if existing else {},
    }

    _set_progress(did, "building", "Saving schema", 88)
    report_id = creds.get("report_id")
    if report_id:
        _save_yaml_schema(report_id, final_schema)

    print(f"\n  Schema assembled: {len(enriched_tables)} tables, "
          f"{len(enriched_measures)} measures, {len(raw['relationships'])} relationships")

    # ── Build page mappings in background (non-blocking) ──
    def _background_page_mapping():
        try:
            _set_progress(did, "building", "Building page → measure mappings", 92)
            page_map = _build_page_mappings(creds, enriched_measures)
            if page_map:
                final_schema["pages"] = page_map
                # Tag each measure with its page_group
                for m in final_schema["measures"]:
                    if not m.get("page_group"):
                        for page_name, measure_list in page_map.items():
                            if m["name"] in measure_list:
                                m["page_group"] = page_name
                                break
                if report_id:
                    _save_yaml_schema(report_id, final_schema)
            _set_progress(did, "done", "Schema complete", 100)
        except Exception as e:
            print(f"  Warning: page mapping failed: {e}")
            _set_progress(did, "done", "Schema complete (page mapping skipped)", 100)

    threading.Thread(target=_background_page_mapping, daemon=True).start()
    _set_progress(did, "building", "Page mappings running in background", 90)

    return final_schema


# ── Public schema endpoint ─────────────────────────────────────────────────────
def get_schema(creds: dict, dashboard_id: int, force_rebuild: bool = False) -> dict:
    report_id = creds.get("report_id")
    if not report_id:
        raise ValueError("report_id not in credentials")
    if not force_rebuild:
        cached = _load_yaml_schema(report_id)
        if cached:
            print(f"  Schema loaded from YAML cache (report_id={report_id})")
            # Older cached schemas (built before report_name was tracked) won't
            # have it — patch it in lazily so meta questions still work without
            # forcing a full rebuild.
            if not cached.get("report_name"):
                try:
                    cached["report_name"] = _get_report_metadata(creds).get("name", "")
                    _save_yaml_schema(report_id, cached)
                except Exception as e:
                    print(f"  Warning: could not backfill report_name: {e}")
            return cached
    return build_enriched_schema(creds, dashboard_id)


# ── DAX execution ──────────────────────────────────────────────────────────────
def _clean_dax(raw: str) -> str:
    return raw.replace("```DAX", "").replace("```dax", "").replace("```", "").strip()


def _refresh_headers(creds: dict) -> dict:
    token = get_access_token(creds)
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ── Hierarchy / self-referencing "reports to" traversal ────────────────────
# Shared between first-pass generation (_dax_generate) and error-driven
# self-correction (_self_correct_dax) so both paths give the model the SAME
# guidance — previously only the first-pass prompt had this, so all 3
# self-correction retries kept reproducing the same broken PATH() call
# instead of ever seeing the fix pattern. Written generically (no dataset-
# specific table/column/sentinel names) so it applies to any dashboard that
# happens to have this kind of self-referencing column, and does nothing for
# dashboards that don't.
_HIERARCHY_FIX_GUIDANCE = """HIERARCHY / "REPORTS TO" / "MANAGER OF" / PARENT-CHILD TRAVERSAL —
when a question asks for entities that relate DIRECTLY OR INDIRECTLY through
a self-referencing column (an ID column and a "parent" column on the SAME
table, both referring to the same kind of entity — e.g. Employee/ReportsTo,
Category/ParentCategory, Account/ParentAccount):

NEVER use PATH() or PATHCONTAINS() for this. PATH() only accepts genuine
physical model columns as both arguments — it CANNOT accept a virtual/
computed column created at query time (e.g. via ADDCOLUMNS or SELECTCOLUMNS),
even one holding exactly the right values in the right row context. Passing
one throws "[ColumnName] is not a valid reference to a column in a table" —
this is a hard engine restriction, not something a cleverer expression can
work around. PATH() also separately requires every non-blank parent value to
exactly match a row in the ID column, which real-world hierarchy data often
violates too (a placeholder like "0", "-", "N/A" for "top of hierarchy"
instead of a true blank) — a second, independent reason to avoid it here.

Always build the closure manually instead: one hierarchy level per VAR,
filtering by the previous level's IDs, then UNION all levels together. This
uses only ordinary FILTER/SELECTCOLUMNS on the real physical columns, so it
never hits either PATH() restriction above — it works regardless of how the
"top of hierarchy" is marked, and regardless of engine limits on virtual
columns. It naturally stops contributing rows once a level's FILTER is empty,
so it's safe to always write a fixed number of levels — typically 6-8 is
enough for any real org/category hierarchy, and extra unneeded levels just
resolve to empty tables at no cost:

EVALUATE
VAR L1 = FILTER('Table', 'Table'[ParentColumn] = "TargetValue")
VAR L1Ids = SELECTCOLUMNS(L1, "ID", 'Table'[IDColumn])
VAR L2 = FILTER('Table', 'Table'[ParentColumn] IN L1Ids)
VAR L2Ids = SELECTCOLUMNS(L2, "ID", 'Table'[IDColumn])
VAR L3 = FILTER('Table', 'Table'[ParentColumn] IN L2Ids)
VAR L3Ids = SELECTCOLUMNS(L3, "ID", 'Table'[IDColumn])
VAR L4 = FILTER('Table', 'Table'[ParentColumn] IN L3Ids)
RETURN
    DISTINCT(UNION(L1, L2, L3, L4))

For a COUNT of direct-or-indirect reports, wrap the same levels in
COUNTROWS(DISTINCT(UNION(...))) instead of returning the table directly."""

# Question-side phrasing that signals a hierarchy/chain traversal is likely
# needed — used to surface the guidance above MORE prominently (not just as
# one bullet among many) whenever it's actually relevant, on any dashboard.
_HIERARCHY_TRIGGER_RE = re.compile(
    r'\b(directly or indirectly|indirectly or directly|report(?:s|ing)?\s*to|'
    r'chain of command|org(?:anization)?al\s+hierarchy|all\s+(?:reports|subordinates)|'
    r'reports?\s+up|downline|down[- ]?line|parent[- ]?child|ancestors?|descendants?|'
    r'everyone\s+(?:under|below)|who\s+works\s+(?:under|below|for))\b',
    re.IGNORECASE,
)

def _needs_hierarchy_guidance(question: str) -> bool:
    return bool(_HIERARCHY_TRIGGER_RE.search(question or ""))


# ── Multiple selected tables with no relationship between them ─────────────
# When Stage 1 selects more than one table for a question, and the schema
# has no relationship connecting them, the model has no way to know from a
# handful of sample values which table actually contains the row being
# searched for (e.g. two independent employee rosters from different
# companies/departments sitting in the same dashboard with no join between
# them). Silently picking one is a coin flip that succeeds half the time
# and returns a clean-looking but wrong "0 rows" the other half — no error
# is thrown, so self-correction never even sees it. The only reliable fix
# is to always search every disconnected table and combine the matches.
_MULTI_TABLE_SEARCH_GUIDANCE = """MULTIPLE UNRELATED TABLES SELECTED — SEARCH ALL OF THEM, NOT JUST ONE:
The tables selected for this question are NOT connected by any relationship
in the schema. That means they are independent datasets (e.g. separate
rosters/entities from different sources) and there is no way to know in
advance — from a handful of sample values — which one actually contains the
row(s) being searched for. NEVER guess a single table and filter only that
one; if you guess wrong, the query still runs successfully but returns 0
rows, which looks like "no data" to the user when the data was actually
right there in the other table.

Instead, run the SAME filter/lookup logic against EVERY selected table that
plausibly contains the entity, using SELECTCOLUMNS to bring each table's
matching columns into the same shape, then UNION the per-table results
together. A table where the filter doesn't match simply contributes zero
rows to the UNION — it costs nothing to include and guarantees you don't
miss the table that actually has the answer:

EVALUATE
VAR FromTableA =
    SELECTCOLUMNS(
        FILTER('TableA', 'TableA'[MatchColumn] = "TargetValue"),
        "Name", 'TableA'[NameColumn], "Detail", 'TableA'[DetailColumn]
    )
VAR FromTableB =
    SELECTCOLUMNS(
        FILTER('TableB', 'TableB'[MatchColumn] = "TargetValue"),
        "Name", 'TableB'[NameColumn], "Detail", 'TableB'[DetailColumn]
    )
RETURN
    DISTINCT(UNION(FromTableA, FromTableB))

If the question also needs hierarchy/PATH-style traversal (see the hierarchy
rule elsewhere in these instructions), apply that traversal separately
WITHIN each table's branch before the final UNION — each unrelated table
has its own independent hierarchy, they don't share one."""


def _tables_are_disconnected(selected_tables: list, schema: dict) -> bool:
    """
    True when 2+ selected tables exist and NONE of the schema's declared
    relationships connects any pair of them — i.e. they are independent
    datasets sitting side by side, not a joinable data model. Generic: does
    not know or care what the tables are actually named/about.
    """
    tables = [t for t in (selected_tables or []) if t]
    if len(tables) < 2:
        return False
    table_set = set(tables)
    for r in schema.get("relationships", []):
        if r.get("from_table") in table_set and r.get("to_table") in table_set:
            return False
    return True


# ── Error-driven detection: was this failure caused by a PATH()-style
# hierarchy issue, regardless of what the question looked like? Covers the
# case where the question's phrasing didn't trip the trigger above but the
# model reached for PATH() anyway and hit the same class of error. ──────────
def _is_hierarchy_error(dax_query: str, error: str) -> bool:
    dax_lower   = (dax_query or "").lower()
    error_lower = (error or "").lower()
    if "path(" not in dax_lower and "pathcontains" not in dax_lower:
        return False
    return any(sig in error_lower for sig in (
        "does not exist", "more than one occurrence", "cannot be found",
        "circular", "path function", "is not a valid reference",
        "cannot be found or may not be used",
    ))


def _self_correct_dax(user_question: str, dax_query: str, error: str, schema: dict) -> str:
    hierarchy_block = (
        f"\n{'='*70}\n{_HIERARCHY_FIX_GUIDANCE}\n{'='*70}\n"
        if _is_hierarchy_error(dax_query, error) else ""
    )
    prompt = f"""You are a DAX expert. The following DAX query failed.

Question: "{user_question}"
Failed DAX: {dax_query}
Error: {error}
{hierarchy_block}
Fix the DAX. Rules:
1. Wrap single value in EVALUATE ROW()
2. Use exact table names with single quotes: 'TableName'[ColumnName]
3. Never replace internal measure calls like [MFG Data] with hardcoded values
4. If the error is a hierarchy/PATH problem, follow the guidance above exactly
   rather than retrying the same PATH() call unchanged.
5. Return DAX only — no markdown, no backticks"""
    raw = _get_oai().chat.completions.create(
        model=cfg.llm_model,
        messages=[
            {"role": "system", "content": "You are a DAX expert. Return only valid DAX."},
            {"role": "user",   "content": prompt},
        ],
        temperature=0,
        max_tokens=700,
    ).choices[0].message.content.strip()
    return _clean_dax(raw)


def execute_dax(creds: dict, dax_query: str,
                user_question: str = "", schema: dict | None = None) -> dict:
    _section("DAX Execution")
    MAX_RETRIES = 3
    ws_id = creds["workspace_id"]
    ds_id = creds["dataset_id"]
    dax   = _clean_dax(dax_query)
    url   = f"{cfg.powerbi_api_url}/groups/{ws_id}/datasets/{ds_id}/executeQueries"
    hdrs  = _refresh_headers(creds)

    result = None
    for attempt in range(MAX_RETRIES):
        print(f"\n  Attempt {attempt + 1}...")
        r = requests.post(
            url, headers=hdrs,
            json={"queries": [{"query": dax}],
                  "serializerSettings": {"includeNulls": True}},
            timeout=30,
        )
        if r.ok:
            rows = r.json()["results"][0]["tables"][0].get("rows", [])
            print(f"  Success — {len(rows)} rows returned")
            result = {"success": True, "rows": rows}
            break
        else:
            error = r.text
            print(f"  Failed: {error[:1000]}")
            if "TokenExpired" in error:
                hdrs = _refresh_headers(creds)
                r2 = requests.post(
                    url, headers=hdrs,
                    json={"queries": [{"query": dax}],
                          "serializerSettings": {"includeNulls": True}},
                    timeout=30,
                )
                if r2.ok:
                    rows = r2.json()["results"][0]["tables"][0].get("rows", [])
                    result = {"success": True, "rows": rows}
                    break
                else:
                    result = {"success": False, "error": r2.text}
            else:
                result = {"success": False, "error": error}

            if attempt < MAX_RETRIES - 1 and schema:
                print("  Self correcting...")
                dax = _self_correct_dax(user_question, dax, result.get("error", ""), schema)

    if not result or not result["success"]:
        raise HTTPException(
            status_code=502,
            detail=f"DAX failed after {MAX_RETRIES} attempts: {result.get('error', '')[:1000]}",
        )
    return result


# ── Meta / "about this dashboard" question handling ────────────────────────────
#
# These are questions ABOUT the dashboard/report/page itself — not about the
# underlying data values — e.g. "what is the title of my dashboard", "how many
# visuals are on this page", "what tables are in this dataset", "summarize
# what this dashboard covers". They never need DAX. Cheap, deterministic
# checks (title, counts, page/visual lists) are tried first using the schema
# and whatever live page/visual context the frontend (Power BI JS SDK) sent
# along; anything more open-ended falls back to a small vector search over
# the schema (tables, measures, pages) followed by an LLM synthesis restricted
# to that retrieved context.

_META_PATTERNS = [
    r'\btitle of (this |my |the )?(dashboard|report)\b',
    r'\bname of (this |my |the )?(dashboard|report)\b',
    r"\bwhat('s| is) (this |my |the )?(dashboard|report)('s)? (title|name)\b",
    r'\bwhat.{0,20}(dashboard|report).{0,15}(called|named)\b',
    r'\bhow many (visuals?|charts?|widgets?)\b',
    r'\bnumber of (visuals?|charts?|widgets?)\b',
    r'\blist (all )?(the )?(visuals?|charts?)\b',
    r'\btitles? of (the |all )?visuals?\b',
    r'\bwhat visuals?\b.{0,20}\bpage\b',
    r'\bhow many pages?\b',
    r'\bnumber of pages?\b',
    r'\blist (all )?(the )?pages?\b',
    r'\bwhich pages? (are|exist|does)\b',
    r'\bhow many (tables?|measures?)\b',
    r'\bnumber of (tables?|measures?)\b',
    r'\bwhat tables? (are|is) (in|on|available)\b',
    r'\bwhat measures? (are|is) (in|on|available)\b',
    r'\bwhat (does|is) this (dashboard|report|dataset) (track|contain|cover|show|about)\b',
    r'\bwhat data (is|does).{0,20}(available|contain|cover)\b',
    r'\bexplain (this |the )?(schema|dataset|data model)\b',
    r'\bwhat is this dashboard about\b',
]


def _is_meta_question(message: str) -> bool:
    msg = message.lower()
    return any(re.search(p, msg) for p in _META_PATTERNS)


def _build_schema_chunks(schema: dict) -> list:
    """Text chunks describing the dashboard's structure — the vectorization unit."""
    report_name = schema.get("report_name") or schema.get("dataset", "")
    dataset     = schema.get("dataset", "")
    workspace   = schema.get("workspace", "")
    tables      = schema.get("tables", []) or []
    measures    = schema.get("measures", []) or []
    pages       = schema.get("pages", {}) or {}

    chunks = [{
        "id": "overview",
        "text": (
            f"Dashboard/report name: {report_name or '(unnamed)'}. Dataset: {dataset}. "
            f"Workspace: {workspace}. Contains {len(tables)} tables and {len(measures)} "
            f"measures across {len(pages)} known page(s): "
            f"{', '.join(pages.keys()) if pages else 'unknown'}."
        ),
    }]

    for t in tables:
        cols = ", ".join(c["name"] for c in t.get("columns", [])[:20])
        chunks.append({
            "id": f"table:{t['name']}",
            "text": f"Table '{t['name']}': {t.get('description', '')} Columns include: {cols}.",
        })

    for pname, mnames in pages.items():
        chunks.append({
            "id": f"page:{pname}",
            "text": f"Page '{pname}' shows these measures: {', '.join(mnames) if mnames else '(none detected)'}.",
        })

    return chunks


def _embeddings_path(report_id: str) -> str:
    return os.path.join(SCHEMA_DIR, f"{report_id}.embeddings.json")


def _embed_texts(texts: list) -> list:
    resp = _get_oai().embeddings.create(model="text-embedding-3-small", input=texts)
    return [d.embedding for d in resp.data]


def _cosine(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na  = sum(x * x for x in a) ** 0.5
    nb  = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _get_schema_chunk_embeddings(schema: dict, report_id: str) -> list:
    """
    Returns [{id, text, vector}], cached to disk per report_id. Rebuilt only
    when the underlying chunk set actually changed (cheap signature check),
    so a normal chat turn doesn't re-embed the whole schema every time.
    """
    chunks = _build_schema_chunks(schema)
    signature = f"{len(chunks)}:{sum(len(c['text']) for c in chunks)}"
    path = _embeddings_path(report_id) if report_id else None

    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("signature") == signature:
                return cached.get("chunks", [])
        except Exception:
            pass

    try:
        vectors = _embed_texts([c["text"] for c in chunks])
    except Exception as e:
        print(f"  Warning: schema embedding failed: {e}")
        return []

    result = [{"id": c["id"], "text": c["text"], "vector": v} for c, v in zip(chunks, vectors)]
    if path:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"signature": signature, "chunks": result}, f)
        except Exception as e:
            print(f"  Warning: could not cache schema embeddings: {e}")
    return result


def _search_schema_chunks(query: str, schema: dict, report_id: str, top_k: int = 6) -> list:
    """Vector search over schema chunks — returns the top_k most relevant chunk texts."""
    chunk_embeds = _get_schema_chunk_embeddings(schema, report_id)
    if not chunk_embeds:
        # Fall back to the first few chunks unranked rather than nothing at all
        return [c["text"] for c in _build_schema_chunks(schema)[:top_k]]
    try:
        q_vec  = _embed_texts([query])[0]
        scored = sorted(chunk_embeds, key=lambda c: _cosine(q_vec, c["vector"]), reverse=True)
        return [c["text"] for c in scored[:top_k]]
    except Exception as e:
        print(f"  Warning: schema vector search failed: {e}")
        return [c["text"] for c in chunk_embeds[:top_k]]


def answer_meta_question(message: str, schema: dict, creds: dict,
                          page_name: Optional[str] = None,
                          visual_inventory: Optional[list] = None) -> dict:
    """
    Answers questions ABOUT the dashboard itself — no DAX involved.
    Tries cheap deterministic lookups first (title, counts, page/visual
    lists using live Power BI JS SDK context when available), then falls
    back to a schema vector search + LLM synthesis for open-ended questions.
    """
    _section("Meta Question")
    msg       = message.lower()
    creds     = creds or {}
    report_id = creds.get("report_id", "")

    def _ok(text: str) -> dict:
        return {"status": "success", "query_type": "meta", "insight": text, "message": None}

    # ── Dashboard / report title ──
    if re.search(r'\b(title|name)\b', msg) and re.search(r'\b(dashboard|report)\b', msg):
        name = schema.get("report_name") or schema.get("dataset") or "Unknown"
        return _ok(f"This dashboard's title is **{name}**.")

    # ── Visuals — count or titles, on the currently active page ──
    if re.search(r'\bvisuals?\b|\bcharts?\b|\bwidgets?\b', msg):
        inventory = list(visual_inventory or [])

        # No live inventory from the frontend (e.g. dashboard tab not open) —
        # fall back to a REST lookup for the named page, best-effort only.
        if not inventory and page_name and creds.get("report_id"):
            try:
                live_pages = get_report_pages(creds)
                match = next((p for p in live_pages if p.get("displayName") == page_name), None)
                if match:
                    raw_visuals = get_page_visuals(creds, match["name"])
                    inventory = [
                        {"title": v.get("title") or "(untitled)", "type": v.get("type", "")}
                        for v in raw_visuals
                    ]
            except Exception as e:
                print(f"  Warning: live visual lookup failed: {e}")

        page_ref = f"page **{page_name}**" if page_name else "this page"

        if not inventory:
            return {
                "status":  "no_results",
                "query_type": "meta",
                "message": "I couldn't read the visuals on this page — make sure the dashboard is open, then try again.",
                "insight": None,
            }

        if re.search(r'\bhow many\b|\bnumber of\b', msg):
            return _ok(f"There are **{len(inventory)}** visuals on {page_ref}.")

        titles = [v.get("title") or "(untitled)" for v in inventory]
        return _ok(f"The visuals on {page_ref} are:\n" + "\n".join(f"- {t}" for t in titles))

    # ── Pages — count or list ──
    if re.search(r'\bpages?\b', msg):
        names = []
        try:
            if creds.get("report_id"):
                live_pages = get_report_pages(creds)
                names = [p.get("displayName", p.get("name", "")) for p in live_pages]
        except Exception as e:
            print(f"  Warning: live page lookup failed: {e}")
        if not names:
            names = list((schema.get("pages") or {}).keys())

        if names:
            if re.search(r'\bhow many\b|\bnumber of\b', msg):
                return _ok(f"This report has **{len(names)}** page(s).")
            return _ok("This report's pages are:\n" + "\n".join(f"- {n}" for n in names))

    # ── Table / measure counts and listings ──
    if re.search(r'\btables?\b', msg):
        table_names = [t["name"] for t in schema.get("tables", [])]
        if re.search(r'\bhow many\b|\bnumber of\b', msg):
            return _ok(f"This dashboard's dataset has **{len(table_names)}** tables.")
        if re.search(r'\bwhat tables?\b|\blist\b', msg):
            return _ok("Tables in this dataset:\n" + "\n".join(f"- {n}" for n in table_names))

    if re.search(r'\bmeasures?\b', msg):
        measure_names = [m["name"] for m in schema.get("measures", [])]
        if re.search(r'\bhow many\b|\bnumber of\b', msg):
            return _ok(f"This dashboard's dataset has **{len(measure_names)}** measures.")
        if re.search(r'\bwhat measures?\b|\blist\b', msg):
            return _ok("Measures in this dataset:\n" + "\n".join(f"- {n}" for n in measure_names))

    # ── Fallback: vector search over the schema + LLM synthesis ──────────────
    context_chunks = _search_schema_chunks(message, schema, report_id, top_k=6)
    context_block  = "\n\n".join(context_chunks) if context_chunks else "(no schema context found)"

    prompt = f"""The user asked a question about the DASHBOARD ITSELF (its structure/metadata),
not about specific data values:
"{message}"

Relevant schema context (retrieved via vector search):
{context_block}

Answer using ONLY the context above. Be concise — 2 to 4 sentences. If the
context genuinely doesn't contain the answer, say so honestly rather than
guessing.

Return ONLY a JSON object: {{"answer": "<your answer>"}}"""

    result = _gpt_json(
        "You answer questions about a Power BI dashboard's structure and metadata, "
        "never inventing details that aren't present in the provided schema context.",
        prompt, max_tokens=300,
    )
    answer = result.get("answer") or result.get("summary") or "I couldn't find that information about this dashboard."
    return _ok(answer)


# ── Question classifier ────────────────────────────────────────────────────────
def _classify_question(message: str) -> str:
    """
    Returns 'descriptive' or 'specific'.
    Descriptive = summary, overview, explain, what does this page show, etc.
    Specific    = a concrete metric question with optional filter.
    """
    descriptive_patterns = [
        r'\bsummar(ize|y|ise)\b',
        r'\boverview\b',
        r'\bexplain\b',
        r'\bdescribe\b',
        r'\bwhat (does|is on|is shown|do you see)\b',
        r'\bhow (is|are) (the|this|our)\b',
        r'\btell me about\b',
        r'\bwhat.{0,20}(page|dashboard|report) show\b',
        r'\bwhat.{0,20}(kpi|metric|visual)',
        r'\btrend\b',
        r'\bbreakdown\b',
        r'\bperforming\b',
        r'\bhow (are|is) (we|things|it)\b',
        # "what about this page", "about this", "this page"
        r'\bwhat about (this|the)\b',
        r'\babout this (page|dashboard|report)\b',
        r'\bthis page\b',
        r'\bwhat can you (tell|say)\b',
        r'\bwhat.{0,10}going on\b',
        r'\bsnap(shot)?\b',
        r'\bgive me a (summary|overview|snapshot|brief)\b',
        r'\bwhat.{0,15}happening\b',
    ]
    msg_lower = message.lower()
    for pattern in descriptive_patterns:
        if re.search(pattern, msg_lower):
            return "descriptive"
    return "specific"


def _extract_data_question(message: str, schema: dict) -> str:
    """
    If the message mixes a general knowledge question with a data question
    (e.g. "what is cainsights and tell me the number of post delivery cancellation"),
    extract only the data-relevant part for the DAX pipeline.

    Uses heuristics first — no LLM call unless compound signals are detected.
    Returns the original message unchanged if it's already a pure data question.
    """
    msg_lower = message.lower()
    compound_signals = [" and tell me", " and show me", " and give me",
                        " and what is", " and also ", " also tell me"]
    if not any(s in msg_lower for s in compound_signals):
        return message

    domain_hint = (
        "This is a clinical/pharma dashboard. Data questions ask about: "
        "patient counts, apheresis, infusion, enrollment, cancellation, "
        "ATC centers, regions, KPIs, milestones, manufacturing, PSP services."
    )

    prompt = f"""{domain_hint}

USER MESSAGE: "{message}"

This message contains multiple questions. Extract ONLY the part that asks about
dashboard data metrics, counts, or KPIs. Discard general knowledge questions
(e.g. "what is X", "explain X", "define X").

If the entire message is a data question, return it unchanged.
If there is no data question, return empty string.

Return ONLY the extracted data question as plain text — no explanation."""

    try:
        resp = _get_oai().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=150,
        )
        extracted = resp.choices[0].message.content.strip().strip('"')
        if extracted and extracted.lower() != message.lower():
            return extracted
    except Exception:
        pass

    return message


# ── Active filter injection ────────────────────────────────────────────────────
def _build_filter_conditions(active_filters: list) -> list:
    """
    Convert frontend slicer state into DAX KEEPFILTERS conditions.

    Skips:
    - Filters where all values are None/null (slicer is unset)
    - Internal dashboard flag columns (Date Flag, Month Flag, COMS Date Flag, etc.)
      that are infrastructure slicers not meaningful as user query filters
    - Filters with no table or column
    """
    # Internal flag columns the dashboard uses for its own state — not user filters
    SKIP_COLUMNS = {
        "month flag", "date flag", "coms date flag", "flag",
        "month flag", "coms date flag", "week day", "week num",
    }

    conditions = []
    for f in (active_filters or []):
        table  = f.get("table", "")
        column = f.get("column", "")
        values = f.get("values", [])

        if not table or not column:
            continue

        # Skip internal flag columns
        if column.lower() in SKIP_COLUMNS:
            continue

        # Strip None/null values — None means the slicer is unset, not a real filter
        clean_values = [v for v in (values or []) if v is not None]
        if not clean_values:
            continue

        if len(clean_values) == 1:
            val = clean_values[0]
            # Numeric values don't get quoted
            if isinstance(val, (int, float)):
                conditions.append(f"KEEPFILTERS('{table}'[{column}] = {val})")
            else:
                conditions.append(f"KEEPFILTERS('{table}'[{column}] = \"{val}\")")
        else:
            parts = []
            for v in clean_values:
                parts.append(str(v) if isinstance(v, (int, float)) else f'"{v}"')
            vals_str = ", ".join(parts)
            conditions.append(f"KEEPFILTERS('{table}'[{column}] IN {{{vals_str}}})")

    return conditions


# ── Schema helpers for DAX (mirrors sql_generator._build_focused_schema) ──────

def _build_table_catalog(schema: dict) -> list:
    """
    Build a flat catalog of all tables — name, description, columns with types
    and sample values, plus column descriptions.
    Mirrors sql_generator._get_table_catalog().
    Used by Stage 1 table selector so it can read the full data model before
    touching any measure names.
    """
    catalog = []
    for t in schema.get("tables", []):
        col_summaries = []
        for c in t.get("columns", []):
            line = f"{c['name']} ({c.get('type','?')})"
            if c.get("description"):
                line += f": {c['description'][:80]}"
            if c.get("sample_values"):
                line += f" | e.g. {c['sample_values'][:3]}"
            col_summaries.append(line)
        catalog.append({
            "name":        t["name"],
            "description": t.get("description", ""),
            "columns":     col_summaries,
        })
    return catalog


def _build_focused_schema_block(selected_table_names: list, schema: dict) -> str:
    """
    Build the rich schema context block for ONLY the selected tables —
    full columns with types, descriptions, sample values, plus the
    relationships block rendered as explicit DAX FILTER/TREATAS paths.

    Mirrors sql_generator._build_focused_schema() exactly:
      - Relationships rendered FIRST, prominently
      - Then table-by-table full column detail
      - Sample values shown so the LLM knows realistic filter values
    """
    lines = []
    all_tables = {t["name"]: t for t in schema.get("tables", [])}
    relationships = schema.get("relationships", [])

    # ── Relationships block first (mirrors SQL generator) ──────────────────
    # Filter to only relationships involving selected tables
    relevant_rels = [
        r for r in relationships
        if r.get("from_table") in selected_table_names
        or r.get("to_table") in selected_table_names
    ]

    lines.append("=" * 60)
    if relevant_rels:
        lines.append("ESTABLISHED RELATIONSHIPS — MANDATORY FILTER/JOIN CONDITIONS")
        lines.append("=" * 60)
        lines.append("In DAX, these relationships define how tables can filter each other.")
        lines.append("Use TREATAS or RELATEDTABLE only along these paths — never invent joins.")
        lines.append("")
        for r in relevant_rels:
            ft = r.get("from_table", "")
            fc = r.get("from_column", "")
            tt = r.get("to_table", "")
            tc = r.get("to_column", "")
            lines.append(f"  • '{ft}'[{fc}]  →  '{tt}'[{tc}]")
            lines.append(f"    DAX cross-filter: TREATAS(VALUES('{ft}'[{fc}]), '{tt}'[{tc}])")
            lines.append(f"                   or RELATEDTABLE('{tt}') when filtering from '{ft}'")
        lines.append("")
        lines.append("RULE: If two tables are NOT linked above, measures must NOT")
        lines.append("      use TREATAS or RELATEDTABLE across them.")
    else:
        lines.append("RELATIONSHIPS: None between the selected tables.")
        lines.append("=" * 60)
        lines.append("Measures must operate on a single table only.")
    lines.append("=" * 60)
    lines.append("")

    # ── Table definitions ──────────────────────────────────────────────────
    for tname in selected_table_names:
        t = all_tables.get(tname)
        if not t:
            continue
        lines.append(f"TABLE: '{tname}'")
        if t.get("description"):
            lines.append(f"  Description: {t['description']}")
        lines.append("  Columns:")
        for c in t.get("columns", []):
            col_line = f"    - '{tname}'[{c['name']}]  ({c.get('type','?')})"
            if c.get("description"):
                col_line += f"\n        → {c['description'][:120]}"
            if c.get("sample_values"):
                col_line += f"\n        → sample values: {c['sample_values'][:5]}"
            lines.append(col_line)
        lines.append("")

    return "\n".join(lines)


# ── Stage 1 Step A: table selector (mirrors sql_generator._select_relevant_tables) ──

def _stage1_select_tables_from_schema(
    message: str, schema: dict, history: list, question_type: str
) -> list:
    """
    Step A — Given the full table catalog, select which tables are relevant
    to the user's question. This is the exact equivalent of
    sql_generator._select_relevant_tables().

    Returns a list of table name strings.
    Does NOT look at measures yet — this step is purely about the data model.
    """
    catalog = _build_table_catalog(schema)
    if not catalog:
        return []

    # Build catalog lines like the SQL generator
    lines = []
    for t in catalog:
        col_str = " | ".join(t["columns"][:12])
        lines.append(
            f"  TABLE: {t['name']}\n"
            f"    DESC: {t['description'][:120]}\n"
            f"    COLS: {col_str}"
        )

    # Render relationships so selector understands join chains
    rel_lines = []
    for r in schema.get("relationships", []):
        rel_lines.append(
            f"  '{r.get('from_table')}[{r.get('from_column')}]  "
            f"→  '{r.get('to_table')}[{r.get('to_column')}]'"
        )
    rels_section = ""
    if rel_lines:
        rels_section = (
            "\nESTABLISHED RELATIONSHIPS (use to determine which tables must be "
            "included to satisfy cross-table filters):\n"
            + "\n".join(rel_lines) + "\n"
        )

    # Enrich question with last-turn context for follow-ups
    context_question = message
    if history:
        last = history[-1]
        last_q = last.get("question", "")
        if last_q:
            context_question = (
                f"Previous question for context: {last_q}\n"
                f"Current question: {message}"
            )

    prompt = f"""You are identifying which Power BI tables are relevant to a user question.
The tables below are the raw data tables in the dataset — not measures.

AVAILABLE TABLES:
{chr(10).join(lines)}
{rels_section}
QUESTION: {context_question}

TASK:
Return every table that COULD plausibly contain columns relevant to answering this question.
Cast a wide net — it is better to include a table that turns out not to be needed than to
miss the table that holds the correct answer.

RULES:
1. Include a table if any of its columns describe the entities, events, statuses,
   dates, or metrics mentioned in the question.
2. If the question involves counting, filtering, or aggregating entities, include
   ALL tables that have an ID or key column for that entity type — there may be
   multiple tables covering the same entity at different granularities (dimension
   vs fact), and the right measure may live on any of them.
3. Include related tables linked by relationships above when they provide the
   filter context needed (e.g. a calendar table for time-period questions, a
   center/HCP table when filtering by region or site).
4. Return an empty array [] ONLY if the question has no connection whatsoever
   to the data domain of this dashboard (e.g. a general knowledge question
   about an unrelated topic).

Return ONLY a JSON array of exact table names. Example: ["TableA", "TableB"]
No explanation — just the JSON array."""

    try:
        resp = _get_oai().chat.completions.create(
            model=cfg.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=300,
        )
        raw = resp.choices[0].message.content.strip()
        match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if match:
            selected = json.loads(match.group())
            all_names = {t["name"] for t in schema.get("tables", [])}
            valid = [t for t in selected if t in all_names]
            print(f"  Stage 1A table selector: {valid}")
            return valid
    except Exception as e:
        print(f"  Stage 1A table selector failed (non-fatal): {e}")

    return []



# ── DAX pipeline (mirrors sql_generator.py exactly) ──────────────────────────
#
#  Step 1 — _dax_select_tables():     same as sql_generator._select_relevant_tables()
#  Step 2 — _build_focused_schema_block(): already exists, unchanged
#  Step 3 — _dax_generate():          same as sql_generator.generate_sql() but
#                                      outputs EVALUATE DAX, not SQL
#  Step 4 — _dax_measure_equivalence(): optional — if generated DAX is semantically
#                                      equivalent to an existing measure, swap it in
#
# Measures are no longer selected upfront. The schema drives the computation.
# Existing measures are consulted AFTER generation to see if one already
# implements the same logic — if so, we prefer it (cleaner, avoids reimplementing
# complex filter patterns like REMOVEFILTERS/ALLEXCEPT chains).


def _dax_select_tables(message: str, schema: dict, history: list) -> list:
    """
    Step 1 — Table selector for DAX.

    Unlike SQL (where questions map to column names), DAX questions often map
    to measure names and DAX expressions — not table/column names directly.
    "DP delivered", "post delivery cancellation", "high segment" don't appear
    in any table description but DO appear in measure names and DAX.

    Two-pass approach:
    Pass A — Measure scan (no LLM): keyword-match question against measure
             names, descriptions, and DAX expressions to find which tables
             the relevant measures read from. Fast, no LLM call.
    Pass B — Table catalog (LLM): show the table catalog PLUS the tables
             found in Pass A as confirmed candidates. LLM validates and adds
             any tables needed for relationships/filters.
    """
    all_measures = schema.get("measures", [])
    all_table_names = {t["name"] for t in schema.get("tables", [])}

    # ── Pass A: keyword scan over measures ───────────────────────────────
    stopwords = {'the','a','an','of','in','at','on','for','to','is','are','was',
                 'were','how','many','what','which','show','me','give','tell',
                 'number','total','all','by','and','or','from','with','per',
                 'patients','patient','please'}
    keywords = [w for w in re.sub(r'[^a-z0-9 ]', '', message.lower()).split()
                if w not in stopwords and len(w) > 2]

    def _measure_score(m: dict) -> int:
        name = m.get('name', '').lower()
        desc = m.get('description', '').lower()
        expr = m.get('expression', '').lower()
        return (sum(3 for kw in keywords if kw in name) +
                sum(2 for kw in keywords if kw in desc) +
                sum(1 for kw in keywords if kw in expr))

    # Find tables referenced by top-scoring measures
    scored = sorted(all_measures, key=_measure_score, reverse=True)
    measure_tables: set = set()
    for m in scored[:10]:
        if _measure_score(m) == 0:
            break
        expr = m.get('expression', '')
        at   = m.get('assigned_table', '')
        if at and at in all_table_names:
            measure_tables.add(at)
        for tname in re.findall(r"'([^']+)'\[", expr):
            if tname in all_table_names:
                measure_tables.add(tname)
        for tname in re.findall(r'\b([A-Z][A-Z0-9_]+)\[', expr):
            if tname in all_table_names:
                measure_tables.add(tname)

    print(f"  Stage 1A measure scan found tables: {list(measure_tables)}")

    # ── Pass B: LLM table catalog validation ─────────────────────────────
    catalog = _build_table_catalog(schema)
    lines = []
    for t in catalog:
        col_str = " | ".join(t["columns"][:12])
        confirmed = " ★ CONFIRMED via measure scan" if t["name"] in measure_tables else ""
        lines.append(
            f"  TABLE: {t['name']}{confirmed}\n"
            f"    DESC: {t['description'][:120]}\n"
            f"    COLS: {col_str}"
        )

    rel_lines = [
        f"  '{r.get('from_table')}[{r.get('from_column')}]"
        f"  →  '{r.get('to_table')}[{r.get('to_column')}]'"
        for r in schema.get("relationships", [])
    ]
    rels_section = (
        "\nRELATIONSHIPS:\n" + "\n".join(rel_lines) + "\n"
    ) if rel_lines else ""

    context_question = message
    if history:
        last_q = history[-1].get("question", "")
        if last_q:
            context_question = f"Previous question: {last_q}\nCurrent question: {message}"

    confirmed_hint = (
        f"\nTables marked ★ were identified by scanning measure DAX expressions "
        f"and are highly likely to be correct. Include them unless clearly wrong.\n"
    ) if measure_tables else ""

    prompt = f"""You are deciding which tables from a Power BI dataset could help answer a user's question.
Tables marked ★ were found by scanning existing measure DAX expressions — they are strong candidates.
{confirmed_hint}
AVAILABLE TABLES:
{chr(10).join(lines)}
{rels_section}
QUESTION: {context_question}

HOW TO READ THE SAMPLE VALUES:
The "e.g. [...]" values shown next to each column are a SMALL RANDOM SUBSET of
that column's actual contents (a handful of values out of what may be thousands
of rows) — they only demonstrate the FORMAT/CATEGORY of the data, not its full
contents. Never exclude a table because a specific name, value, or entity from
the question isn't among the samples shown. Ask "could this column's CATEGORY
of data contain the answer?" — not "do I see the exact value here?".

HOW TO MATCH QUESTION WORDS TO COLUMNS:
- Treat singular/plural forms as the same word (e.g. a question about
  "departments" matches a column named "Department", and vice versa).
- Table/column DESCRIPTIONS are paraphrases, not keyword checklists — a
  conceptual match counts even when no word is shared verbatim with the
  question.
- A question that names or asks about a specific entity ("who is X", "what
  team/department is X in", "X's manager") is a row-value lookup, not a
  keyword-matching problem — select any table whose columns describe that
  KIND of entity (e.g. Name, Person, Employee, Title, Department), regardless
  of whether X itself appears in the visible samples.

DEFAULT TOWARD INCLUDING, NOT EXCLUDING:
Including an extra table costs a slightly larger context in the next step.
Excluding a table that was actually relevant produces a visible failure for
the user ("no data found") even though the dashboard could have answered
them. When genuinely unsure whether a table is relevant, include it.

RULES:
1. Always include tables marked ★ unless they are clearly irrelevant to the question.
2. Add any other tables whose columns are needed for filters or grouping.
3. Include related tables when a relationship is needed to connect them.
4. Return an empty tables list ONLY if the question has no conceivable relation
   to what ANY table is about — e.g. general knowledge, small talk, or a
   completely different subject than anything described above. Naming a
   person, count, category, or attribute that plausibly fits this dataset's
   subject matter is NEVER "no connection" just because the exact value isn't
   visible in the samples shown.
5. If the question mixes general knowledge with data, select tables for the DATA part only.

Return ONLY valid JSON, no markdown or explanation outside the JSON:
{{"reasoning": "one short sentence on why these tables were or weren't chosen", "tables": ["TableA", "TableB"]}}"""

    try:
        resp = _get_oai().chat.completions.create(
            model=cfg.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=400,
        )
        raw = resp.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        parsed = None
        try:
            parsed = json.loads(raw)
        except Exception:
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
        if parsed is not None:
            selected = parsed.get("tables", []) if isinstance(parsed, dict) else parsed
            valid    = [t for t in selected if t in all_table_names]
            # Always include measure-scan tables if LLM dropped them
            for t in measure_tables:
                if t not in valid:
                    valid.append(t)
            reasoning = parsed.get("reasoning", "") if isinstance(parsed, dict) else ""
            print(f"  Stage 1A table selector: {valid} — reasoning: {reasoning[:150]}")
            return valid
    except Exception as e:
        print(f"  Stage 1A table selector failed (non-fatal): {e}")

    # Fallback: return measure-scan tables directly
    result = list(measure_tables)
    print(f"  Stage 1A fallback to measure scan: {result}")
    return result


def _find_relevant_measure_hints(user_question: str, schema: dict, selected_tables: list) -> str:
    """
    Fast keyword scan to find existing measures whose DAX is likely relevant
    to the question. Returns a formatted block of measure name + full DAX
    to inject as reference patterns into the _dax_generate system prompt.

    This replaces _dax_measure_equivalence as a post-generation check.
    Instead of checking after generation, we inject good examples before,
    so the generator can directly use or adapt the right measure DAX.
    """
    all_measures = schema.get("measures", [])
    if not all_measures:
        return ""

    # Extract meaningful keywords from question
    stopwords = {'the','a','an','of','in','at','on','for','to','is','are','was','were',
                 'how','many','what','which','show','me','give','tell','number','total',
                 'all','by','and','or','from','with','per','count','patients','patient'}
    keywords = [w for w in re.sub(r'[^a-z0-9 ]', '', user_question.lower()).split()
                if w not in stopwords and len(w) > 3]

    if not keywords:
        return ""

    def _score(m: dict) -> int:
        name = m.get('name', '').lower()
        expr = m.get('expression', '').lower()
        desc = m.get('description', '').lower()
        at   = m.get('assigned_table', '')
        s = 0
        s += sum(3 for kw in keywords if kw in name)
        s += sum(2 for kw in keywords if kw in desc)
        s += sum(1 for kw in keywords if kw in expr)
        # Boost measures on selected tables
        for tname in selected_tables:
            if at == tname or f"'{tname}'" in m.get('expression',''):
                s += 4
                break
        return s

    scored = sorted(all_measures, key=_score, reverse=True)
    # Take top 5 with score > 0
    hints = [m for m in scored[:5] if _score(m) > 0]
    if not hints:
        return ""

    lines = ["REFERENCE MEASURES — existing measures that may be relevant.",
             "Study their DAX logic as patterns for the computation needed.",
             "If one exactly matches what you need, use [MeasureName] in the EVALUATE.",
             "If adapting, keep the same aggregation function and filter columns.",
             ""]
    for m in hints:
        deps = m.get('dependent_measures', [])
        lines.append(f"[{m['name']}]")
        lines.append(f"  Description: {m.get('description','')[:100]}")
        lines.append(f"  DAX: {m.get('expression','')}")
        if deps:
            lines.append(f"  Calls: {deps}")
        lines.append("")
    return "\n".join(lines)


def _dax_generate(
    user_question: str,
    focused_schema_block: str,
    schema: dict,
    active_filters: list = None,
    history: list = None,
    page_name: Optional[str] = None,
    selected_tables: list = None,
) -> str:
    """
    Step 3 — Generate EVALUATE DAX directly from schema.
    Mirrors sql_generator.generate_sql() exactly:
    - System prompt = focused schema as absolute ground truth
    - Relevant existing measures injected as reference patterns
    - User prompt = question (+ history for follow-ups)
    - Retry loop with targeted error injection (same pattern as SQL generator)
    """
    # Active slicer filters
    filter_conditions = _build_filter_conditions(active_filters or [])
    filter_block = ""
    if filter_conditions:
        filter_block = (
            "\nACTIVE SLICER FILTERS — include ALL of these inside CALCULATE(...):\n"
            + "\n".join(filter_conditions) + "\n"
        )

    page_context = f"User is viewing page: '{page_name}'\n" if page_name else ""

    history_block = ""
    if history:
        lines = ["CONVERSATION HISTORY (use for follow-up context only):"]
        for entry in history[-4:]:
            q   = entry.get("question", "")
            dax = entry.get("dax_query") or entry.get("sql_query", "")
            s   = entry.get("summary") or entry.get("insight", "")
            if q:   lines.append(f"  Q: {q}")
            if dax: lines.append(f"  DAX: {dax[:150]}")
            if s:   lines.append(f"  Answer: {s[:120]}")
        history_block = "\n".join(lines) + "\n\n"

    # Find relevant existing measures to inject as reference patterns
    measure_hints = _find_relevant_measure_hints(
        user_question, schema, selected_tables or []
    )
    hints_block = f"\n{measure_hints}\n" if measure_hints else ""

    emphasis_blocks = []
    if _needs_hierarchy_guidance(user_question):
        emphasis_blocks.append(
            "THIS QUESTION LOOKS LIKE A HIERARCHY / CHAIN-OF-COMMAND TRAVERSAL.\n"
            "Read RULE 10 below carefully BEFORE writing any PATH()/PATHCONTAINS "
            "call — a naive PATH() call on real-world hierarchy data very commonly "
            "fails on this class of question."
        )
    if _tables_are_disconnected(selected_tables, schema):
        emphasis_blocks.append(_MULTI_TABLE_SEARCH_GUIDANCE)

    hierarchy_emphasis = ""
    if emphasis_blocks:
        hierarchy_emphasis = (
            f"\n{'='*70}\n" + f"\n{'='*70}\n".join(emphasis_blocks) + f"\n{'='*70}\n"
        )

    system_prompt = f"""You are a Power BI DAX expert. Write a correct EVALUATE DAX query that answers the user's question using ONLY the schema provided.

{"="*70}
SCHEMA CONTEXT — tables, columns, sample values, relationships
{"="*70}
{focused_schema_block}
{"="*70}
END OF SCHEMA CONTEXT
{"="*70}
{hierarchy_emphasis}{hints_block}{page_context}{filter_block}
RULES — follow exactly:

1. USE ONLY EXACT TABLE AND COLUMN NAMES from the SCHEMA above.
   Copy character-for-character including capitalisation.
   Never invent, abbreviate, or guess names. Syntax: 'TableName'[ColumnName]

2. RELATIONSHIPS — the schema shows the ONLY valid cross-filter paths.
   Use TREATAS(VALUES('FromTable'[FromCol]), 'ToTable'[ToCol]) for cross-filters.
   NEVER invent a TREATAS path not shown in relationships.

3. AGGREGATION — match to what the user is asking:
   - "how many / count / number of [entity]" → DISTINCTCOUNT('Table'[ID_column])
   - "total / sum" → SUM('Table'[amount_column])
   - "reach / % / rate / proportion" → DIVIDE(numerator, denominator)
   - "average" → AVERAGE or DIVIDE
   - "distribution / breakdown" → SUMMARIZECOLUMNS with a dimension column

4. FILTERS — write explicit CALCULATE(..., filter_condition) for any filter needed.
   Use exact column values from sample values in schema.
   For milestone/stage filters not in samples, use the REFERENCE MEASURES above
   to discover the correct filter values (e.g. CURRENT_MILESTONE = "Authorized").

5. DATE COLUMN FILTERS — when the question asks "patients who have [done X]"
   where X corresponds to a date column (e.g. apheresed → APH_1_FINISH_DATE,
   infused → DRUG_PRODUCT_INFUSION_1_DATE, manufactured → MANUFACTURING_FINISH_DATE):
   Filter with NOT(ISBLANK('Table'[DateColumn])) to select patients who completed that stage.
   Example: "apheresed patients" → CALCULATE(DISTINCTCOUNT(...), NOT(ISBLANK('VW_RPT_DIM_PATIENT'[APH_1_FINISH_DATE])))

6. PREFER EXISTING MEASURES — if a REFERENCE MEASURE above already computes exactly
   what is needed, use [MeasureName] in your EVALUATE instead of rewriting the DAX.
   Example: if [Apheresed] exists and the question asks for apheresed patients → use [Apheresed].

7. GROUP BY — only add SUMMARIZECOLUMNS dimension when user explicitly asks for breakdown.
   For single totals → ROW(...).

8. TIME FILTERS — only add calendar filter when user specifies a period.
   No time qualifier → no TREATAS calendar filter.

9. PATTERNS:
   Single value using existing measure (PREFERRED when measure exists):
   EVALUATE ROW("Result", [MeasureName])

   Single value from schema (when no matching measure exists):
   EVALUATE ROW("Result", CALCULATE(DISTINCTCOUNT('Table'[ID]),
       NOT(ISBLANK('Table'[DateColumn]))))

   Breakdown:
   EVALUATE SUMMARIZECOLUMNS('Table'[Region], "Count", [MeasureName])

   With active slicer filters:
   EVALUATE ROW("Result", CALCULATE([MeasureName],
       KEEPFILTERS('Table'[Col] = "Val"), ...))

10. {_HIERARCHY_FIX_GUIDANCE}

11. Return raw DAX only — no markdown, no backticks, no explanation."""

    user_prompt = f"{history_block}Question: {user_question}"

    last_error = None
    for attempt in range(3):
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ]
        if last_error and attempt > 0:
            error_lower = last_error.lower()
            if "syntax" in error_lower or "parse" in error_lower:
                hint = "→ DAX syntax error. Check parentheses, commas, and EVALUATE keyword."
            elif "cannot find name" in error_lower or "not found" in error_lower:
                hint = "→ Table or column name not found. Use ONLY names from the SCHEMA CONTEXT."
            elif "treatas" in error_lower or "relationship" in error_lower:
                hint = "→ Invalid TREATAS. Use ONLY the relationship paths shown in the schema."
            else:
                hint = "→ Fix the DAX based on the error. Use ONLY names from SCHEMA CONTEXT."
            messages.append({
                "role": "user",
                "content": (
                    f"Previous DAX failed:\n  {last_error}\n{hint}\n\n"
                    "Rewrite using ONLY exact table/column names from SCHEMA CONTEXT above."
                )
            })
        try:
            resp = _get_oai().chat.completions.create(
                model=cfg.llm_model,
                messages=messages,
                temperature=0.1,
                max_tokens=1400,
            )
            return _clean_dax(resp.choices[0].message.content.strip())
        except Exception as e:
            last_error = str(e)

    raise ValueError(f"DAX generation failed after 3 attempts: {last_error}")


def _dax_measure_equivalence(
    generated_dax: str,
    schema: dict,
    active_filters: list = None,
) -> Optional[str]:
    """
    Step 4 — Optional measure equivalence check.
    Mirrors nothing in the SQL generator (SQL has no equivalent concept).

    After generating raw DAX from schema, check if any existing measure
    implements semantically the same computation. If yes, rewrite the
    EVALUATE query to use [MeasureName] instead of the raw expression.

    This is valuable because:
    - Existing measures may handle REMOVEFILTERS/ALLEXCEPT edge cases correctly
    - Power BI computes defined measures faster than raw expressions
    - Composite measures (returning formatted strings) can't be replicated in raw DAX

    Returns a rewritten EVALUATE using [MeasureName] if a match is found,
    or None if the raw DAX should be used as-is.
    """
    all_measures = schema.get("measures", [])
    if not all_measures:
        return None

    # Build compact measure summary — name + truncated DAX only
    # We don't need descriptions here; only the DAX expression matters for equivalence
    measure_lines = []
    for m in all_measures:
        expr = m.get("expression", "")[:200]
        measure_lines.append(f"[{m['name']}]: {expr}")

    filter_block = ""
    if active_filters:
        clean = [f for f in active_filters if f.get("values") and any(v is not None for v in f["values"])]
        if clean:
            filter_block = "Active slicer filters that should also be applied: " + ", ".join(
                f"{f['table']}.{f['column']}={f['values']}" for f in clean
            ) + "\n"

    prompt = f"""You have generated this DAX query from table/column schema:
{generated_dax}

{filter_block}
EXISTING MEASURES:
{chr(10).join(measure_lines[:80])}

TASK: Does any existing measure implement SEMANTICALLY THE SAME computation as the
generated DAX above?

Semantic equivalence means:
- Same aggregation function (DISTINCTCOUNT/SUM/DIVIDE/etc.) on the same column
- Same or equivalent filter conditions (values may differ by case/format but mean the same thing)
- A measure using REMOVEFILTERS/ALLEXCEPT that achieves the same result is equivalent

If YES: return a rewritten EVALUATE query that uses [MeasureName] instead of the raw
expression. Include active slicer filters if any.

If NO: return null.

Return JSON:
{{"equivalent_measure": "Measure Name" | null, "rewritten_dax": "EVALUATE ROW(...)" | null}}"""

    try:
        resp = _get_oai().chat.completions.create(
            model=cfg.llm_model,
            messages=[
                {"role": "system", "content": "You are a DAX expert. Return valid JSON only."},
                {"role": "user",   "content": prompt},
            ],
            temperature=0,
            max_tokens=400,
        )
        raw = resp.choices[0].message.content.strip().replace("```json","").replace("```","").strip()
        result = json.loads(raw)
        if result.get("equivalent_measure") and result.get("rewritten_dax"):
            print(f"  Measure equivalence: [{result['equivalent_measure']}] — using measure instead of raw DAX")
            return _clean_dax(result["rewritten_dax"])
    except Exception as e:
        print(f"  Measure equivalence check failed (non-fatal): {e}")
    return None


# ── Stage 1: orchestrator ──────────────────────────────────────────────────────
def _stage1_select_tables(message: str, schema: dict, history: list,
                           page_name: Optional[str] = None,
                           question_type: str = "specific") -> dict:
    """
    Mirrors sql_generator.generate_sql() flow exactly:

    Step 1 — Table selector (_dax_select_tables):
      Same as sql_generator._select_relevant_tables().
      Returns [] for out-of-scope questions.

    Step 2 — Focused schema builder (_build_focused_schema_block):
      Same as sql_generator._build_focused_schema().
      Rich block: relationships FIRST, then full columns + sample values.

    Returns a dict with selected_tables, focused_schema_block, and
    selected_measures=[] (measures are no longer selected upfront —
    the DAX is generated from schema in Stage 2, then optionally
    matched to existing measures for equivalence).
    """
    selected_tables = _dax_select_tables(message, schema, history)

    if not selected_tables:
        print("  Stage 1A: no tables selected — trying measure-hint fallback")
        # Some answers live entirely in measures. Build schema from tables that
        # score highest on keyword overlap with the question so hints are relevant.
        stopwords = {'the','a','an','of','in','at','on','for','to','is','are','tell',
                     'me','how','many','what','which','show','give','number','total',
                     'all','by','and','or','from','with','per','patients','patient'}
        keywords = [w for w in re.sub(r'[^a-z0-9 ]', '', message.lower()).split()
                    if w not in stopwords and len(w) > 3]

        # Name/column matches are structural signals (the question is actually
        # about that entity) — description matches are prose and can collide
        # incidentally with words that mean something completely different in
        # another domain (e.g. a pharma dataset's "Phase" tables vs a question
        # about the software product's development "phases"). Weight name/
        # column matches much higher so a single weak prose hit can't carry
        # a table across the bar on its own.
        def _tbl_score(t):
            n = t["name"].lower()
            d = t.get("description","").lower()
            col_text = " ".join(c["name"].lower() for c in t.get("columns",[]))
            return (sum(3 for kw in keywords if kw in n) +
                    sum(2 for kw in keywords if kw in col_text) +
                    sum(1 for kw in keywords if kw in d))

        all_tables = schema.get("tables", [])
        scored_tables = sorted(all_tables, key=_tbl_score, reverse=True)

        # Require a real match before trusting the fallback at all — previously
        # this always took the top 8 tables regardless of score, so even a
        # dataset where NOTHING matched (or only matched by incidental prose
        # overlap) would still get handed to the DAX generator, overriding
        # Stage 1A's own correct "out of scope" judgment and producing
        # confidently wrong answers instead of "no results".
        MIN_FALLBACK_SCORE = 3
        top_score = _tbl_score(scored_tables[0]) if scored_tables else 0
        if top_score < MIN_FALLBACK_SCORE:
            print(f"  Stage 1A: no table cleared the relevance bar (top score={top_score}) — treating as out of scope")
            return {
                "selected_tables":    [],
                "selected_measures":  [],
                "filter_columns":     [],
                "dependent_measures": [],
                "reasoning":          "No relevant tables or measures found for this question.",
                "_focused_schema_block": None,
            }

        fallback_tables = [t["name"] for t in scored_tables if _tbl_score(t) >= MIN_FALLBACK_SCORE][:8]
        focused_schema_block = _build_focused_schema_block(fallback_tables, schema)
        print(f"  Stage 1A: measure-hint fallback using tables: {fallback_tables[:4]}...")
        return {
            "selected_tables":    [],
            "selected_measures":  [],
            "filter_columns":     [],
            "dependent_measures": [],
            "reasoning":          "No tables selected — using measure hints.",
            "_focused_schema_block": focused_schema_block,
        }

    focused_schema_block = _build_focused_schema_block(selected_tables, schema)
    print(f"  Stage 1B: focused schema built for {selected_tables}")

    return {
        "selected_tables":    selected_tables,
        "selected_measures":  [],        # not used — DAX generated from schema
        "filter_columns":     [],
        "dependent_measures": [],
        "reasoning":          f"Tables selected: {selected_tables}",
        "_focused_schema_block": focused_schema_block,
    }


# ── Stage 2: generate DAX from schema + optional measure equivalence ──────────
def _force_multi_table_coverage(
    user_question: str, current_dax: str, missing_tables: list,
    focused_schema_block: str,
) -> str:
    """
    Deterministic correction step, called only when code-level inspection
    (not the model's own judgment) has confirmed the generated DAX ignores
    one or more selected tables that are unrelated to the ones it did use.
    This is a direct instruction to fix a known, specific problem — not
    another "please remember the rule" prompt — so it's far more reliable
    than hoping the first pass follows advisory guidance.
    """
    prompt = f"""This DAX query answers the user's question using only SOME of the
relevant tables. It is missing coverage of: {', '.join(missing_tables)}.

User question: "{user_question}"

Current DAX:
{current_dax}

{"="*70}
FULL SCHEMA (all selected tables, including the missing ones)
{"="*70}
{focused_schema_block}
{"="*70}

TASK: Rewrite the DAX so it ALSO searches/filters {', '.join(missing_tables)}
using the SAME logic already applied to the other table(s) (same kind of
filter condition, same kind of columns pulled), then combine all tables'
results with UNION (via SELECTCOLUMNS into a matching shape first if the
column names differ) so the final answer never misses a match that lives in
a different, unrelated table than the one currently covered.

Return raw DAX only — no markdown, no backticks, no explanation."""
    try:
        raw = _get_oai().chat.completions.create(
            model=cfg.llm_model,
            messages=[
                {"role": "system", "content": "You are a DAX expert. Return only valid DAX."},
                {"role": "user",   "content": prompt},
            ],
            temperature=0,
            max_tokens=1400,
        ).choices[0].message.content.strip()
        return _clean_dax(raw)
    except Exception as e:
        print(f"  _force_multi_table_coverage failed (non-fatal, keeping original DAX): {e}")
        return current_dax


def _stage2_generate_dax(user_question: str, selected_tables: list,
                          selected_measures: list, filter_columns: list,
                          schema: dict, history: list = None,
                          active_filters: list = None,
                          focused_schema_block: str = None) -> str:
    """
    Mirrors sql_generator.generate_sql() Steps 3 + 4:

    Step 3 — Generate EVALUATE DAX directly from focused schema.
      Schema is the ground truth. No measure names involved at this stage.
      Retry loop with targeted error injection (same as SQL generator).

    Step 4 — Measure equivalence check (_dax_measure_equivalence).
      If an existing measure implements the same computation, rewrite
      the EVALUATE to use [MeasureName] (cleaner + handles edge cases).
      Falls back to raw DAX if no equivalent measure found.
    """
    # If focused_schema_block not provided, build from selected tables
    if not focused_schema_block:
        if not selected_tables and selected_measures:
            # Legacy path: measures were provided, derive tables from them
            all_schema_measures = {m["name"]: m for m in schema.get("measures", [])}
            all_table_names     = {t["name"] for t in schema.get("tables", [])}
            tables = set()
            for mname in selected_measures:
                m = all_schema_measures.get(mname, {})
                at = m.get("assigned_table", "")
                if at and at in all_table_names:
                    tables.add(at)
                for match in re.findall(r"'([^']+)'\[", m.get("expression", "")):
                    if match in all_table_names:
                        tables.add(match)
            selected_tables = list(tables)
        focused_schema_block = _build_focused_schema_block(selected_tables, schema)

    # Step 3: Generate DAX from schema, with relevant measures injected as hints
    raw_dax = _dax_generate(
        user_question, focused_schema_block, schema,
        active_filters, history,
        page_name=None,
        selected_tables=selected_tables,
    )
    print(f"  Stage 2 raw DAX:\n{raw_dax}")

    # ── Deterministic enforcement, not just a prompt request ──────────────
    # The multi-table-search instruction inside _dax_generate's prompt is
    # advisory — the model follows it MOST of the time but not always (same
    # question, reworded, can get a different outcome at temperature>0).
    # A silently-wrong single-table guess doesn't error, so nothing else
    # would ever catch it. Actually check the output instead of trusting it:
    # if multiple selected tables are disconnected in the schema, verify the
    # generated DAX references ALL of them; if it's missing one, force a
    # single corrective rewrite that explicitly must add it.
    if _tables_are_disconnected(selected_tables, schema):
        missing = [t for t in selected_tables if f"'{t}'" not in raw_dax]
        if missing:
            print(f"  Stage 2: DAX only covers {[t for t in selected_tables if t not in missing]} "
                  f"— forcing coverage of unrelated table(s) {missing} too")
            raw_dax = _force_multi_table_coverage(
                user_question, raw_dax, missing, focused_schema_block
            )
            print(f"  Stage 2 corrected DAX:\n{raw_dax}")

    return raw_dax



# ── Format insight ─────────────────────────────────────────────────────────────
def _looks_like_dax(text: str) -> bool:
    """
    Stage 2 is supposed to always return an EVALUATE/DEFINE DAX query. When the
    question is genuinely unrelated to this dashboard's schema, the model
    sometimes replies with a plain-English refusal instead (e.g. "I'm sorry,
    but I cannot determine..."). That text is NOT DAX and must never be sent
    to execute_dax — doing so wastes retries on self-correcting garbage and
    can return unrelated rows, which then get formatted into a misleading
    "success" insight. Treat anything that doesn't start like real DAX as
    "no relevant answer from this dashboard".
    """
    if not text or not text.strip():
        return False
    upper = text.strip().upper()
    return upper.startswith("EVALUATE") or upper.startswith("DEFINE")


def _format_insight(user_question: str, dax_query: str, rows: list,
                    history: list = None, mode: str = "specific") -> dict:
    history_block = ""
    if history:
        lines = ["Prior context:"]
        for entry in history[-3:]:
            lines.append(f"Q: {entry.get('question','')}")
            s = entry.get("summary") or entry.get("insight","")
            if s:
                lines.append(f"A: {s[:150]}")
        history_block = "\n".join(lines) + "\n\n"

    if mode == "descriptive":
        format_instruction = (
            "By default, write a 3-5 sentence executive summary covering all metrics. "
            "Highlight what's performing well, what needs attention, any notable trends. "
            "Be specific — use the actual numbers."
        )
    else:
        format_instruction = (
            "By default, write a clear, concise 1-3 sentence answer. "
            "Be specific — mention the actual numbers."
        )

    ROW_CAP = 150
    rows_for_prompt = rows[:ROW_CAP]
    truncation_note = (
        f"\nNOTE: only the first {ROW_CAP} of {len(rows)} total rows are shown below "
        f"— say so explicitly in your answer if the user asked for a complete list.\n"
        if len(rows) > ROW_CAP else ""
    )

    prompt = f"""{history_block}User asked: "{user_question}"
DAX used: {dax_query}
{truncation_note}Result: {json.dumps(rows_for_prompt, indent=2, default=str)}

{format_instruction}

The user's own question is the ONLY source of truth for how they want the answer
formatted — read it yourself (e.g. "in points", "briefly", "elaborate" all mean
something plain and obvious, don't guess from keyword lists).

{_INSIGHT_FORMAT_SCHEMA_BLOCK}"""
    result = _gpt_json(
        "You are a helpful data analyst. Answer based only on the data provided. Never invent numbers.",
        prompt, max_tokens=1400,
    )
    result.setdefault("answered", True)
    result.setdefault("format", "prose")
    return result


# ── Multi-DAX for descriptive questions ───────────────────────────────────────
def _run_multi_dax(user_question: str, selected_measures: list, schema: dict,
                   creds: dict, active_filters: list = None) -> dict:
    """Run one DAX query per measure in parallel. Returns {measure_name: rows}."""
    results = {}

    # Build a lookup: measure_name -> tables it references
    # Uses assigned_table + scans expression for table references
    all_schema_measures = {m["name"]: m for m in schema.get("measures", [])}
    all_table_names     = {t["name"] for t in schema.get("tables", [])}

    def _tables_for_measure(measure_name: str) -> list:
        m = all_schema_measures.get(measure_name, {})
        tables = set()

        # assigned_table
        at = m.get("assigned_table", "")
        if at and at in all_table_names:
            tables.add(at)

        # scan expression for 'TableName'[col] patterns
        expr = m.get("expression", "")
        for match in re.findall(r"'([^']+)'\[", expr):
            if match in all_table_names:
                tables.add(match)
        # also unquoted TableName[col]
        for match in re.findall(r"\b([A-Z][A-Z0-9_]+)\[", expr):
            if match in all_table_names:
                tables.add(match)

        # fallback: include all tables referenced in relationships for those tables
        if not tables:
            # last resort — return empty, Stage 2 will use measure-only pattern
            pass

        return list(tables)

    def _run_one(measure_name):
        try:
            tables = _tables_for_measure(measure_name)
            print(f"  Multi-DAX [{measure_name}]: resolved tables = {tables}")
            dax = _stage2_generate_dax(
                user_question,
                selected_tables=tables,
                selected_measures=[measure_name],
                filter_columns=[],
                schema=schema,
                active_filters=active_filters,
            )
            # Same guard as the single-DAX path: if Stage 2 refused instead of
            # returning real DAX (question unrelated to this measure), don't
            # execute the refusal text as a query — just skip this measure.
            if not _looks_like_dax(dax):
                print(f"  Multi-DAX [{measure_name}]: Stage 2 did not return valid DAX — skipping")
                return measure_name, None
            result = execute_dax(creds, dax, user_question=user_question, schema=schema)
            return measure_name, result.get("rows", [])
        except Exception as e:
            print(f"  Multi-DAX: {measure_name} failed: {e}")
            return measure_name, None

    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(_run_one, m): m for m in selected_measures}
        for fut in as_completed(futures):
            mname, rows = fut.result()
            results[mname] = rows

    return results


def _format_multi_insight(user_question: str, multi_results: dict,
                           page_name: str = None, active_filters: list = None,
                           history: list = None) -> dict:
    history_block = ""
    if history:
        lines = ["Prior context:"]
        for entry in history[-3:]:
            lines.append(f"Q: {entry.get('question','')}")
            s = entry.get("summary") or entry.get("insight","")
            if s:
                lines.append(f"A: {s[:150]}")
        history_block = "\n".join(lines) + "\n\n"

    filter_context = ""
    if active_filters:
        filter_context = "Active filters: " + ", ".join([
            f"{f['column']}={f['values']}" for f in active_filters
        ]) + "\n"

    page_context = f"Page: {page_name}\n" if page_name else ""

    metrics_block = json.dumps(
        {k: v for k, v in multi_results.items() if v is not None},
        indent=2, default=str
    )

    prompt = f"""{history_block}{page_context}{filter_context}User asked: "{user_question}"

KPI data retrieved from the dashboard:
{metrics_block}

IMPORTANT: each measure above was queried in isolation and may have returned a
real numeric value even when that measure has nothing to do with what the user
actually asked (e.g. the user asked about a person's resume, and a measure like
"Total Enrollments" still executed successfully — that number does NOT answer
the question just because it's non-null). Judge relevance against the user's
actual question, not just whether numbers came back.

By default, write a 3-5 sentence executive summary covering the metrics that are
genuinely relevant to the question. Skip metrics that returned null/empty AND
skip metrics that are simply unrelated to what was asked.

The user's own question is the ONLY source of truth for how they want the answer
formatted — read it yourself (e.g. "in points", "briefly", "elaborate" all mean
something plain and obvious, don't guess from keyword lists).

{_INSIGHT_FORMAT_SCHEMA_BLOCK}"""
    result = _gpt_json(
        "You are a data analyst summarizing a dashboard page. Be concise, specific, and honest about relevance — never treat an unrelated metric as an answer.",
        prompt, max_tokens=1400,
    )
    result.setdefault("answered", True)
    result.setdefault("format", "prose")
    return result


# ── Page summary using exported visual data (no DAX needed) ──────────────────
def run_page_summary(message: str, page_name: Optional[str],
                     page_visual_data: list, active_filters: list,
                     conversation_history: list = None) -> dict:
    """
    Answer descriptive questions using exported visual data from the frontend.
    No DAX needed — data is already rendered with correct filter context.
    Structures metrics prominently to create a quality summary.
    """
    _section("Page Summary")
    print(f"  Page: {page_name or 'unknown'}")
    print(f"  Visual data received: {len(page_visual_data or [])} visuals")
    print(f"  Active filters: {len(active_filters or [])}")

    history = conversation_history or []

    history_block = ""
    if history:
        lines = ["Prior context:"]
        for entry in history[-3:]:
            lines.append(f"Q: {entry.get('question','')}")
            s = entry.get("summary") or entry.get("insight","")
            if s:
                lines.append(f"A: {s[:150]}")
        history_block = "\n".join(lines) + "\n\n"

    filter_context = ""
    if active_filters:
        filter_context = "Active filters on page: " + ", ".join([
            f"{f.get('column','?')} = {f.get('values','?')}"
            for f in active_filters
        ]) + "\n"

    page_context = f"Page: '{page_name}'\n" if page_name else ""

    # Extract metrics and structure visuals for better summary
    visuals_block = ""
    metrics_summary = []

    for v in (page_visual_data or []):
        title  = v.get("title") or "(untitled)"
        vtype  = v.get("type", "visual")
        data   = v.get("data", "")

        # For KPI/Card type visuals, treat the entire data as the metric value
        if vtype.lower() in ["kpi", "card", "gauge"]:
            if data:
                metrics_summary.append(f"• {title}: {data.strip()}")

        # For other visuals, show first few rows
        lines  = data.strip().split("\n") if data else []
        if len(lines) > 26:
            lines  = lines[:26]
            lines.append("... (truncated)")
        data_str = "\n".join(lines)
        visuals_block += f"\n[{vtype}] {title}:\n{data_str}\n"

    if not visuals_block.strip():
        visuals_block = "(No visual data available)"

    # Build a structured metrics section if we found any KPI-style metrics
    metrics_block = ""
    if metrics_summary:
        metrics_block = "Key Metrics on Page:\n" + "\n".join(metrics_summary) + "\n\n"

    prompt = f"""{history_block}{page_context}{filter_context}{metrics_block}User asked: "{message}"

Visual data on the page:
{visuals_block}

Write a 3-5 sentence executive summary answering the question above.
Highlight key metrics, what's performing well, what may need attention, and any notable trends.
Use the actual numbers and metric titles from the data.

If the visual data on this page has nothing to do with what the user actually
asked (e.g. they asked about a person or topic that isn't a metric on this
page), don't force an answer out of unrelated numbers.

The user's own question is the ONLY source of truth for how they want the answer
formatted — read it yourself (e.g. "in points", "briefly", "elaborate" all mean
something plain and obvious, don't guess from keyword lists).

{_INSIGHT_FORMAT_SCHEMA_BLOCK}"""

    insight_result = _gpt_json(
        "You are a data analyst summarizing a dashboard. Be specific and honest about relevance — never treat unrelated metrics as an answer.",
        prompt,
        max_tokens=1400,
    )
    insight_result.setdefault("answered", True)
    insight_result.setdefault("format", "prose")

    if not insight_result.get("answered", True):
        return {
            "status":     "no_results",
            "query_type": "page_summary",
            "insight":    None,
            "message":    None,
        }

    return {
        "status":     "success",
        "query_type": "page_summary",
        "insight":    _render_insight_summary(insight_result),
        "message":    None,
    }


# ── Public chat ────────────────────────────────────────────────────────────────
def run_chat(creds: dict, dashboard_id: int, message: str,
             conversation_history: list = None,
             page_name: Optional[str] = None,
             active_filters: Optional[list] = None,
             page_visual_data: Optional[list] = None,
             page_visual_inventory: Optional[list] = None) -> dict:
    """
    Enhanced agentic chat:
      - Meta questions (title, page/visual counts, schema structure) → answer_meta_question, no DAX
      - Classifies remaining questions as descriptive or specific
      - Descriptive with visual data  → run_page_summary (no DAX)
      - Descriptive without visual data → multi-DAX + narrative
      - Specific                       → existing two-stage DAX pipeline
      - Page context scopes measure selection
      - Active filters injected into DAX
    """
    _section("Agentic Chat")
    print(f"  User: {message}")
    print(f"  Page: {page_name or 'unknown'}")
    print(f"  Filters: {active_filters}")

    schema  = get_schema(creds, dashboard_id)
    history = conversation_history or []

    # ── Meta questions about the dashboard/report itself — never need DAX ──
    if _is_meta_question(message):
        print("  Question type: meta")
        return answer_meta_question(
            message, schema, creds,
            page_name=page_name,
            visual_inventory=page_visual_inventory,
        )

    # Classify
    q_type = _classify_question(message)
    print(f"  Question type: {q_type}")

    # ── Descriptive with exported visual data → fastest path ──
    if q_type == "descriptive" and page_visual_data:
        return run_page_summary(
            message, page_name, page_visual_data,
            active_filters or [], history
        )

    # ── Stage 1 ──
    print("\n  [Stage 1] Selecting measures...")
    try:
        stage1 = _stage1_select_tables(
            message, schema, history,
            page_name=page_name,
            question_type=q_type,
        )
        # Extract internal schema block before logging — it's large and only needed for Stage 2
        focused_schema_block = stage1.pop("_focused_schema_block", None)
        print(f"  Stage 1 tables={stage1.get('selected_tables')} measures={stage1.get('selected_measures')} reason={stage1.get('reasoning','')[:100]}")
    except Exception as e:
        return {
            "status": "error", "query_type": "unsupported",
            "message": f"Stage 1 failed: {e}",
            "filters": [], "dax_query": None, "needs_dax": False, "insight": None,
        }

    selected_measures = stage1.get("selected_measures", [])
    selected_tables   = stage1.get("selected_tables", [])

    # ── No tables AND no schema block — truly out of scope ───────────────
    if not selected_tables and not focused_schema_block:
        print("  Stage 1 found no relevant tables — no results from this dashboard")
        return {
            "status":     "no_results",
            "query_type": "data_query",
            "message":    None,
            "filters":    active_filters or [],
            "dax_query":  None,
            "needs_dax":  False,
            "insight":    None,
            "rows":       [],
        }

    # ── Descriptive without visual data → multi-DAX ──
    if q_type == "descriptive":
        print("\n  [Multi-DAX] Running parallel DAX for descriptive question...")
        multi_results = _run_multi_dax(
            message, selected_measures, schema, creds, active_filters
        )

        # If every measure was rejected (not valid DAX) or returned nothing,
        # there's no real data to summarize — don't hand an empty dict to the
        # LLM and let it write a plausible-sounding "insight" out of nothing.
        if not any(v for v in multi_results.values()):
            print("  Multi-DAX: no measure returned data — no results from this dashboard")
            return {
                "status":     "no_results",
                "query_type": "descriptive",
                "message":    None,
                "filters":    active_filters or [],
                "dax_query":  None,
                "needs_dax":  False,
                "insight":    None,
                "rows":       [],
            }

        insight_result = _format_multi_insight(
            message, multi_results, page_name, active_filters, history
        )

        # A measure can execute successfully and return real numbers while
        # being completely irrelevant to what the user actually asked (each
        # measure is queried in isolation, unaware of the others). Trust the
        # model's own relevance judgment here, not just "did any query
        # return non-null data".
        if not insight_result.get("answered", True):
            print("  Multi-DAX: retrieved metrics aren't relevant to the question — no results from this dashboard")
            return {
                "status":     "no_results",
                "query_type": "descriptive",
                "message":    None,
                "filters":    active_filters or [],
                "dax_query":  None,
                "needs_dax":  False,
                "insight":    None,
                "rows":       [],
            }

        insight = _render_insight_summary(insight_result)
        metrics_rows = []
        for measure_name, values in multi_results.items():
            if values:
                metrics_rows.append({
                    "measure": measure_name,
                    "values": values if isinstance(values, list) else [values]
                })

        return {
            "status":     "success",
            "query_type": "descriptive",
            "message":    None,
            "filters":    active_filters or [],
            "dax_query":  None,
            "needs_dax":  False,
            "insight":    insight,
            "rows":       metrics_rows,
        }

    # ── Specific → single DAX ──
    print("\n  [Stage 2] Generating DAX...")
    try:
        dax_query = _stage2_generate_dax(
            message,
            stage1.get("selected_tables", []),
            selected_measures,
            stage1.get("filter_columns", []),
            schema,
            history,
            active_filters,
            focused_schema_block=focused_schema_block,
        )
        print(f"  Generated DAX:\n{dax_query}")
    except Exception as e:
        return {
            "status": "error", "query_type": "unsupported",
            "message": f"Stage 2 failed: {e}",
            "filters": [], "dax_query": None, "needs_dax": False, "insight": None,
        }

    # ── Stage 2 refused instead of returning DAX — the question doesn't
    # actually match this dashboard's data. Don't execute the refusal text
    # as a query (it would just fail, self-correct into an unrelated query,
    # and return misleading rows). Report cleanly as no results instead.
    if not _looks_like_dax(dax_query):
        print("  Stage 2 did not return valid DAX — no relevant answer from this dashboard")
        return {
            "status":     "no_results",
            "query_type": "data_query",
            "message":    None,
            "filters":    active_filters or [],
            "dax_query":  None,
            "needs_dax":  False,
            "insight":    None,
            "rows":       [],
        }

    try:
        result = execute_dax(creds, dax_query, user_question=message, schema=schema)
        rows   = result["rows"]
    except HTTPException as e:
        return {
            "status": "error", "query_type": "data_query",
            "message": e.detail, "filters": [],
            "dax_query": dax_query, "needs_dax": False, "insight": None,
        }

    # ── No rows returned — report it cleanly, don't format a fake insight ─
    if not rows:
        print("  DAX executed but returned no rows — no results for this question")
        return {
            "status":     "no_results",
            "query_type": "data_query",
            "message":    None,
            "filters":    active_filters or [],
            "dax_query":  dax_query,
            "needs_dax":  False,
            "insight":    None,
            "rows":       [],
        }

    insight_result = _format_insight(message, dax_query, rows, history, mode="specific")

    if not insight_result.get("answered", True):
        print("  Specific DAX: retrieved data isn't relevant to the question — no results from this dashboard")
        return {
            "status":     "no_results",
            "query_type": "data_query",
            "message":    None,
            "filters":    active_filters or [],
            "dax_query":  dax_query,
            "needs_dax":  False,
            "insight":    None,
            "rows":       [],
        }

    insight = _render_insight_summary(insight_result)

    return {
        "status":     "success",
        "query_type": "data_query",
        "message":    None,
        "filters":    active_filters or [],
        "dax_query":  dax_query,
        "needs_dax":  False,
        "insight":    insight,
        "rows":       rows,
    }