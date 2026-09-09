# sql_generator.py
from openai import OpenAI
from database_manager import DatabaseManager
import logging
import json
import re
from typing import TYPE_CHECKING, Dict, Any, List, Optional, Union

if TYPE_CHECKING:
    from federated_query_engine import FederatedQueryEngine

logger = logging.getLogger(__name__)


class SQLGenerator:
    def __init__(
        self,
        api_key: str,
        db_manager: "Union[FederatedQueryEngine, DatabaseManager]"
    ):
        self.client = OpenAI(api_key=api_key)
        self.db_manager: "Union[FederatedQueryEngine, DatabaseManager]" = db_manager
        self._last_result_df = None
        self.model = "gpt-4o"  # GPT-4o: optimized for SQL generation

        # Normalise source access — FederatedQueryEngine exposes ._sources directly;
        # a bare DatabaseManager is wrapped into a single-entry dict for uniform access.
        from federated_query_engine import FederatedQueryEngine  # runtime check
        if isinstance(db_manager, FederatedQueryEngine):
            self._sources: Dict[str, DatabaseManager] = db_manager._sources
        else:
            # db_manager is a bare DatabaseManager — wrap it for uniform iteration
            bare_mgr: DatabaseManager = db_manager  # type: ignore[assignment]
            alias: str = bare_mgr._schema_alias or bare_mgr.db_type
            self._sources = {alias: bare_mgr}

    def _is_federated(self) -> bool:
        return len(self._sources) > 1

    # ── Global relationships — read from shared schema_info ───────────────

    def _get_global_relationships(self) -> List[str]:
        """
        Return the full list of established relationships stored in the schema
        (global YAML list, already fully-namespaced as alias.table.col = alias.table.col).

        Reads from the first connected manager's schema_info["relationships"] which,
        after the datasources fix, always contains the complete global list.
        Falls back to aggregating from all managers if only a subset is loaded.
        """
        seen: set = set()
        result: List[str] = []
        for alias, mgr in self._sources.items():
            schema_info = getattr(mgr, "schema_info", None) or {}
            for rel in schema_info.get("relationships", []):
                if rel not in seen:
                    seen.add(rel)
                    result.append(rel)
        return result


    def _get_table_catalog(self) -> List[Dict]:
        sources = self._sources
        catalog = []

        def _entries_for(alias: Optional[str], db_type: str, mgr: "DatabaseManager"):
            schema_tables = (mgr.schema_info or {}).get("tables", {})
            active = set(mgr.selected_tables if mgr.selected_tables is not None else list(schema_tables.keys()))
            for tname, tinfo in schema_tables.items():
                if tname not in active:
                    continue
                qualified = f"{alias}.{tname}" if alias else tname
                desc, count_col, inline_cols, entity_cols, filter_hints = "", "", [], [], []
                col_summaries = []

                if isinstance(tinfo, dict):
                    desc         = tinfo.get("description", "")
                    count_col    = tinfo.get("count_column", "")
                    inline_cols  = tinfo.get("inline_value_columns", [])
                    entity_cols  = tinfo.get("entity_name_columns", [])
                    filter_hints = tinfo.get("filter_hints", [])
                    for col, col_info in tinfo.get("columns", {}).items():
                        col_desc = col_info.get("description", "") if isinstance(col_info, dict) else ""
                        col_summaries.append(f"{col}: {col_desc}" if col_desc else col)

                catalog.append({
                    "name":                 qualified,
                    "source_alias":         alias or "",
                    "db_type":              db_type,
                    "description":          desc,
                    "columns":              col_summaries,
                    "count_column":         count_col,
                    "inline_value_columns": inline_cols,
                    "entity_name_columns":  entity_cols,
                    "filter_hints":         filter_hints,
                })

        for alias, mgr in sources.items():
            _entries_for(alias, mgr.db_type, mgr)

        return catalog

    def _all_table_names(self) -> List[str]:
        return [e["name"] for e in self._get_table_catalog()]

    # ── Schema introspection helpers ──────────────────────────────────────

    def _get_columns_for_table(self, qualified_name: str) -> List[str]:
        """
        Return the exact column names for a qualified table from schema_info.
        Used to inject real column names into retry error messages.
        """
        sources = self._sources
        if sources:
            parts = qualified_name.split(".", 1)
            if len(parts) == 2:
                alias, tname = parts
                mgr = sources.get(alias)
                if mgr:
                    tinfo = (mgr.schema_info or {}).get("tables", {}).get(tname, {})
                    return list(tinfo.get("columns", {}).keys()) if isinstance(tinfo, dict) else []
        return []

    def _schema_columns_block(self, selected_tables: List[str]) -> str:
        """
        Compact column listing for ALL selected tables — injected into retry messages
        so the LLM has the exact column names available when fixing errors.
        """
        lines = ["EXACT COLUMN NAMES (use only these — no other column names exist):"]
        for tname in selected_tables:
            cols = self._get_columns_for_table(tname)
            if cols:
                lines.append(f"  {tname}: {', '.join(cols)}")
        return "\n".join(lines)

    # ── Check for stale schema entries ────────────────────────────────────

    def _trigger_schema_refresh_if_stale(self, selected_tables: List[str]):
        """
        If any selected table is missing sample_rows (old schema format),
        trigger a background re-generation for those tables so future queries
        benefit from filter_hints and richer descriptions.
        Does not block the current query.
        """
        import threading

        sources = self._sources
        stale: List[tuple] = []  # List of (alias, DatabaseManager, table_name)

        def _check(alias: Optional[str], mgr: "DatabaseManager"):
            schema_tables = (mgr.schema_info or {}).get("tables", {})
            for qt in selected_tables:
                tname = qt.split(".", 1)[1] if alias and qt.startswith(f"{alias}.") else qt
                tinfo = schema_tables.get(tname, {})
                # Check if table needs enrichment (old format or missing enriched flag)
                if isinstance(tinfo, dict) and not tinfo.get("enriched", False):
                    stale.append((alias, mgr, tname))

        for alias, mgr in sources.items():
            _check(alias, mgr)

        if stale:
            logger.info(
                f"Stale schema detected for {[t for _,_,t in stale]} — "
                "triggering background refresh (won't affect this query)"
            )
            def _refresh():
                try:
                    for alias, mgr, tname in stale:
                        typed_mgr: DatabaseManager = mgr
                        api_key: Optional[str] = getattr(typed_mgr, '_openai_api_key', None)
                        if not api_key:
                            continue
                        # Re-run set_selected_tables forcing re-description of stale tables
                        schema_tables: Dict[str, Any] = (typed_mgr.schema_info or {}).get("tables", {})
                        # Temporarily remove stale entry so it gets re-described
                        removed = schema_tables.pop(tname, None)
                        try:
                            current_selected: List[str] = typed_mgr.selected_tables or list(schema_tables.keys()) + [tname]
                            typed_mgr.set_selected_tables(current_selected, api_key)
                            logger.info(f"Background schema refresh complete for {tname}")
                        except Exception as e:
                            logger.warning(f"Background refresh failed for {tname}: {e}")
                            if removed is not None:
                                schema_tables[tname] = removed  # restore on failure
                except Exception as e:
                    logger.warning(f"Background schema refresh thread failed: {e}")

            threading.Thread(target=_refresh, daemon=True).start()

    # ── Step 1: Table selector ────────────────────────────────────────────

    def _select_relevant_tables(self, question: str, conversation_history: list = None) -> List[str]:
        all_names = self._all_table_names()
        if not all_names:
            return []

        # Build a context-enriched question for the table selector.
        # If the current question is a follow-up (uses pronouns / references prior results),
        # prepend the last turn's question so the selector understands what "that" / "same" refers to.
        context_question = question
        if conversation_history:
            last = conversation_history[-1]
            last_q = last.get("question", "")
            last_sql = last.get("sql_query", "")
            if last_q:
                context_question = (
                    f"Previous question for context: {last_q}"
                    + (f" (SQL used: {last_sql})" if last_sql else "")
                    + f"\nCurrent question: {question}"
                )

        catalog = self._get_table_catalog()
        lines = []
        for e in catalog:
            col_str = ", ".join(e["columns"][:14])
            extras = []
            if e["count_column"]:
                extras.append(f"count_column={e['count_column']}")
            if e["inline_value_columns"]:
                extras.append(f"inline_values=[{', '.join(e['inline_value_columns'])}]")
            if e["entity_name_columns"]:
                extras.append(f"entity_names=[{', '.join(e['entity_name_columns'])}]")
            if e["filter_hints"]:
                extras.append(f"filter_hints={e['filter_hints']}")
            extra_str = f"\n    META: {' | '.join(extras)}" if extras else ""
            lines.append(
                f"  TABLE: {e['name']}\n"
                f"    SOURCE: {e['source_alias']} (db_type={e['db_type']})\n"
                f"    DESC: {e['description']}\n"
                f"    COLS: [{col_str}]{extra_str}"
            )

        # Render established relationships so table selector can follow join chains.
        # Read from global schema_info — format is already fully namespaced.
        rels = self._get_global_relationships()
        fk_section = ""
        if rels:
            rel_lines = [f"  - {rel}" for rel in rels]
            fk_section = (
                "\nESTABLISHED JOIN PATHS (use these to determine which tables must be "
                "included to satisfy join requirements):\n" + "\n".join(rel_lines) + "\n"
            )

        prompt = f"""You are selecting the minimum set of database tables needed to answer this question.

AVAILABLE TABLES:
{chr(10).join(lines)}
{fk_section}
QUESTION: {context_question}

RULES:
1. Return only tables whose columns will appear in SELECT, WHERE, JOIN, or GROUP BY.
2. If the needed column already exists in the primary table (denormalized), do NOT add a lookup table for it.
3. Only join tables if they have a defined FK relationship in the schema and you need a column from them.
4. For "how many [entity]" questions, pick the table that contains that entity's ID column.
5. If the question has MULTIPLE PARTS, select tables for the parts you CAN answer from available tables.
   Do NOT return [] just because you cannot answer every part of the question.
   Return tables for the parts you can answer, even if other parts have no matching data.
   Example: "patients in high segment AND dp delivered" — if dim_patient has SEGMENT, return it
   even if "dp delivered" has no matching column in any table.
6. Return [] ONLY if NONE of the question's parts can be answered by any available table.
7. If the current question is a follow-up referencing a prior question, use the prior question context to identify the correct tables.

Return ONLY a JSON array of exact table names shown above. Example: ["alias.table1", "alias.table2"]
No explanation, no commentary — just the JSON array."""

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=250,
            )
            raw = resp.choices[0].message.content.strip()
            match = re.search(r'\[.*?\]', raw, re.DOTALL)
            if match:
                selected = json.loads(match.group())
                if isinstance(selected, list):
                    if len(selected) == 0:
                        logger.info("Table selector chose 0 tables (out of domain question).")
                        return []
                    valid = [t for t in selected if t in all_names]
                    if valid:
                        logger.info(f"Table selector chose {len(valid)}/{len(all_names)} tables: {valid}")
                        return valid
            logger.warning(f"Table selector bad response, using all: {raw[:200]}")
        except Exception as e:
            logger.warning(f"Table selection failed (non-fatal): {e}")

        logger.info(f"Fallback: using all {len(all_names)} tables")
        return all_names

    # ── Step 2: Focused schema context ────────────────────────────────────

    def _build_focused_schema(self, selected_tables: List[str]) -> str:
        """Build a clean, focused schema context for SQL generation."""
        is_fed = self._is_federated()
        sources = self._sources
        lines = []

        # ── Global established relationships ──────────────────────────────
        # These are the ONLY permitted JOIN conditions. The LLM must not invent
        # any other join paths. Format: alias.table.col = alias.table.col
        all_relationships = self._get_global_relationships()

        def _render(tname: str, tinfo: dict, qualified: str, db_type: str):
            """Render table schema in a clean format."""
            if not isinstance(tinfo, dict):
                return

            # Table header and description
            lines.append(f"Table: {qualified}")
            desc = tinfo.get("description", "")
            if desc:
                lines.append(f"  Description: {desc}")

            # Columns
            lines.append("  Columns:")
            for col, col_info in tinfo.get("columns", {}).items():
                if isinstance(col_info, dict):
                    dtype = col_info.get("type", "TEXT")
                    desc_c = col_info.get("description", "")
                    line = f"    - {col} ({dtype})"
                    if desc_c:
                        line += f": {desc_c}"
                    lines.append(line)
                else:
                    lines.append(f"    - {col} ({col_info})")

            # Sample data
            sample_rows = tinfo.get("sample_rows", [])
            if sample_rows:
                try:
                    headers = list(sample_rows[0].keys())
                    lines.append(f"  Sample data ({len(sample_rows)} rows):")
                    lines.append(f"    {' | '.join(headers)}")
                    for row in sample_rows[:3]:
                        vals = [str(row.get(h, ""))[:25] for h in headers]
                        lines.append(f"    {' | '.join(vals)}")
                except Exception:
                    pass

            lines.append("")

        # ── Relationships block — rendered FIRST, before table definitions ──
        if all_relationships:
            lines.append("=" * 60)
            lines.append("ESTABLISHED RELATIONSHIPS — MANDATORY JOIN CONDITIONS")
            lines.append("=" * 60)
            lines.append("The following JOIN paths are the ONLY valid ways to join")
            lines.append("tables. You MUST use these exact columns. You MUST NOT")
            lines.append("invent any other join conditions.")
            lines.append("")
            for rel in all_relationships:
                # rel format: alias.table.col = alias.table.col
                # Render as explicit SQL ON condition so the LLM can copy it directly
                if " = " in rel:
                    left, right = rel.split(" = ", 1)
                    # Parse alias.table.col → table alias and column
                    def _parse_side(side: str):
                        parts = side.strip().split(".")
                        if len(parts) == 3:   # alias.table.col
                            return parts[0], parts[1], parts[2]
                        elif len(parts) == 2:  # table.col
                            return "", parts[0], parts[1]
                        return "", "", side.strip()

                    la, lt, lc = _parse_side(left)
                    ra, rt, rc = _parse_side(right)

                    left_full  = f"{la}.{lt}" if la else lt
                    right_full = f"{ra}.{rt}" if ra else rt

                    lines.append(f"  • {rel}")
                    lines.append(f"    → SQL: JOIN {right_full} ON {left_full}.{lc} = {right_full}.{rc}")
                    lines.append(f"       OR : JOIN {left_full}  ON {right_full}.{rc} = {left_full}.{lc}")
                else:
                    lines.append(f"  • {rel}")
            lines.append("")
            lines.append("RULE: If no relationship is listed above between two tables,")
            lines.append("      you MUST NOT join them. Use only the columns from a")
            lines.append("      single table if no relationship exists.")
            lines.append("=" * 60)
            lines.append("")
        else:
            lines.append("=" * 60)
            lines.append("RELATIONSHIPS: None defined.")
            lines.append("Do NOT join any tables — use only a single table per query.")
            lines.append("=" * 60)
            lines.append("")

        # ── Table definitions ─────────────────────────────────────────────
        if is_fed and sources:
            lines += ["=== FEDERATED DATABASE ===",
                      "ALL table references MUST be prefixed: <source_alias>.<table>", ""]
            for alias, mgr in sources.items():
                schema_tables = (mgr.schema_info or {}).get("tables", {})
                alias_tables = [t.split(".", 1)[1] for t in selected_tables
                                if t.startswith(f"{alias}.")]
                if not alias_tables:
                    continue
                lines.append(f"-- Source: {alias} (db_type={mgr.db_type}) --")
                for tname in alias_tables:
                    _render(tname, schema_tables.get(tname, {}), f"{alias}.{tname}", mgr.db_type)
        else:
            # Single source
            alias, mgr = next(iter(self._sources.items()))
            schema_tables = (mgr.schema_info or {}).get("tables", {})
            for tname in selected_tables:
                base = tname.split(".", 1)[1] if "." in tname else tname
                tinfo = schema_tables.get(base) or schema_tables.get(tname) or {}
                if tinfo:
                    _render(base, tinfo, tname, mgr.db_type)

        return "\n".join(lines) + self._pbix_context_block(selected_tables)

    def _pbix_context_block(self, selected_tables: List[str]) -> str:
        """
        Appends business-measure and relationship context for tables that
        came from a pbix upload (see pbix_service.py). Plain SQL schemas have
        no concept of a DAX measure's filter/aggregation conventions (e.g.
        "a valid claim has CLAIM_TRANSACTION_TYPE_FLG = 1") — without this,
        the LLM only sees raw columns and has no way to know that
        convention exists, which is exactly the bug class this fixes.

        Safe no-op for any source that isn't a pbix upload: the fingerprint
        lookup just returns None and nothing gets appended.
        """
        try:
            import pbix_service
        except ImportError:
            return ""  # pbix feature not installed in this deployment — fine

        # Everything below is enrichment-only (extra business-measure/
        # relationship context for the LLM). It must NEVER be able to take
        # down SQL generation for sqlite/databricks/any other source, so the
        # whole block is wrapped defensively — any unexpected failure here
        # (missing attribute, bad index file, load error, etc.) degrades to
        # "no extra context" instead of propagating and failing the query.
        try:
            # Map each selected table back to its source alias/DatabaseManager,
            # mirroring the same alias-parsing _build_focused_schema uses above.
            is_fed = self._is_federated()
            relevant_base_tables: set = set()
            fingerprints_seen: set = set()

            for tname in selected_tables:
                if is_fed and "." in tname:
                    alias, base = tname.split(".", 1)
                    mgr = self._sources.get(alias)
                else:
                    alias, mgr = next(iter(self._sources.items()))
                    base = tname.split(".", 1)[1] if "." in tname else tname

                if mgr is None or not getattr(mgr, "db_path", None):
                    continue

                fp = pbix_service.get_fingerprint_for_sqlite_path(mgr.db_path)
                if fp:
                    fingerprints_seen.add(fp)
                    relevant_base_tables.add(base.replace(" ", "_"))
                    relevant_base_tables.add(base)  # also keep original (pre-sanitize) form

            if not fingerprints_seen:
                return ""

            blocks = []
            for fp in fingerprints_seen:
                schema = pbix_service.load_enriched_schema(fp)
                if not schema:
                    continue

                # Only surface measures whose DAX expression actually references
                # one of the currently selected tables — same idea as the
                # keyword-matching used elsewhere, but based on real table refs
                # instead of question keywords, so it's precise rather than fuzzy.
                relevant_measures = [
                    m for m in schema.get("measures", [])
                    if any(
                        f"{t}[" in m.get("expression", "") or f"'{t}'[" in m.get("expression", "")
                        for t in relevant_base_tables
                    )
                ]

                if relevant_measures:
                    blocks.append(
                        "\n" + "=" * 60 +
                        "\nBUSINESS MEASURES (from the uploaded Power BI file's original "
                        "model)\n" + "=" * 60 +
                        "\nThese are the ORIGINAL, business-approved calculation "
                        "definitions for this data.\nWhen the question maps to one of "
                        "these concepts, your SQL's filters and\naggregation MUST match "
                        "the same logic shown here — these are not\nsuggestions, they "
                        "are the house convention for what counts as valid data\n"
                        "(e.g. which rows represent a real, successful record vs. a "
                        "reversal/adjustment).\n"
                    )
                    for m in relevant_measures:
                        blocks.append(f"  • {m['name']}")
                        if m.get("description"):
                            blocks.append(f"    Meaning: {m['description']}")
                        blocks.append(f"    Original DAX: {m['expression']}")
                        blocks.append("")

                relationships = schema.get("relationships", [])
                relevant_rels = [
                    r for r in relationships
                    if r.get("from_table") in relevant_base_tables
                    or r.get("to_table") in relevant_base_tables
                ]
                if relevant_rels:
                    blocks.append(
                        "PBIX RELATIONSHIPS (from the original model, for additional "
                        "JOIN guidance):"
                    )
                    for r in relevant_rels:
                        active = "" if r.get("is_active", True) else " (inactive by default in the original model)"
                        blocks.append(
                            f"  • {r['from_table']}.{r['from_column']} -> "
                            f"{r['to_table']}.{r['to_column']}{active}"
                        )
                    blocks.append("")

            return "\n".join(blocks)
        except Exception as e:
            # Enrichment is strictly optional. Never let a bug here (missing
            # attribute, bad schema shape, index corruption, etc.) break SQL
            # generation for the sql agent or the dashboard agent.
            logger.warning(f"sql_generator: pbix context block skipped due to error: {e}")
            return ""

    # ── Step 0: Schema meta-questions ──────────────────────────────────────
    # Keyword phrases that indicate the question is asking about the SHAPE of
    # the schema itself ("how many columns", "what is this column about"),
    # not asking for data. These never fit the table-selector's contract
    # ("pick tables whose columns appear in SELECT/WHERE/JOIN/GROUP BY"), so
    # they used to fall straight through to 0 selected tables and the
    # generic "out of scope" message. Checked with a cheap keyword match
    # first so normal data questions never pay for this at all.
    _SCHEMA_META_KEYWORDS = (
        "how many columns", "how many tables", "how many fields",
        "what columns", "which columns", "what fields", "which fields",
        "what tables", "which tables", "list the columns", "list the tables",
        "list of columns", "list of tables", "column names", "table names",
        "describe the table", "describe this table", "describe the schema",
        "describe this database", "describe this data", "describe the data",
        "what is this column", "what does this column", "what is the column",
        "what's this column", "what's the column", "meaning of the column",
        "what data is available", "what data do we have",
        "what's in this database", "what is in this database",
        "what's in this table", "what is in this table",
        "structure of the data", "structure of the database",
        "structure of this table", "schema of", "what kind of data",
        "tell me about this table", "tell me about this database",
        "tell me about this column", "what does this table contain",
        "what does this database contain",
    )

    def _looks_like_schema_meta_question(self, question: str) -> bool:
        q = (question or "").lower()
        return any(kw in q for kw in self._SCHEMA_META_KEYWORDS)

    def _answer_schema_meta_question(self, question: str) -> Optional[str]:
        """
        Answers a question ABOUT the schema's structure (column/table counts,
        what a column means, what a table contains) directly from the
        already-generated table/column descriptions in schema_info — no SQL
        is written or executed. Returns None (never raises) on any failure,
        so the caller can fall through to the normal SQL pipeline exactly as
        before if this can't produce an answer.
        """
        try:
            catalog = self._get_table_catalog()
            if not catalog:
                return None

            lines = []
            for e in catalog:
                lines.append(f"TABLE: {e['name']} (source={e['source_alias'] or 'default'}, db_type={e['db_type']})")
                if e["description"]:
                    lines.append(f"  Description: {e['description']}")
                lines.append(f"  Column count: {len(e['columns'])}")
                lines.append("  Columns:")
                for c in e["columns"]:
                    lines.append(f"    - {c}")

            schema_text = "\n".join(lines)

            prompt = f"""You are answering a question about the STRUCTURE of a database schema — not a question that requires running a query against the data. Use ONLY the schema information below; never invent tables, columns, or facts not shown here.

SCHEMA:
{schema_text}

QUESTION: {question}

Answer directly and concisely using only the information above. If asked for a count, give the exact number. If asked what a column or table means, quote/paraphrase its description above."""

            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=500,
            )
            answer = resp.choices[0].message.content.strip()
            return answer or None
        except Exception as e:
            logger.warning(f"Schema meta-question answer failed (non-fatal, falling back to SQL pipeline): {e}")
            return None

    # ── Step 3: SQL generation ─────────────────────────────────────────────

    def generate_sql(self, question: str, previous_question: str = None,
                     previous_sql: str = None, max_retries: int = 3, persona: str = None,
                     conversation_history: list = None) -> Dict[str, Any]:
        is_fed = self._is_federated()

        # Schema meta-questions are answered directly from schema_info and
        # never touch table selection or SQL generation. If detection or
        # answering fails for any reason, this falls straight through to the
        # existing pipeline below, completely unchanged.
        if self._looks_like_schema_meta_question(question):
            meta_answer = self._answer_schema_meta_question(question)
            if meta_answer:
                logger.info("Answered as schema meta-question (no SQL executed).")
                return {
                    "success": True,
                    "sql_query": None,
                    "meta_answer": meta_answer,
                    "is_federated": is_fed,
                }

        selected_tables = self._select_relevant_tables(question, conversation_history or [])
        logger.info(f"Selected {len(selected_tables)} tables: {selected_tables}")

        # REMOVED: Background schema refresh during queries
        # Schema generation should ONLY happen during data source initialization
        # NOT during query execution to avoid:
        # 1. Rate limit exhaustion
        # 2. Query latency
        # 3. Schema inconsistency during queries
        # Schema is generated once when tables are first loaded via ensure_schema_generated()

        # Build focused schema first so it can be passed into the scope check.
        # This gives the scope checker the full column list for every selected
        # table — not a truncated 8-column summary — making the verdict reliable
        # for any table size or data domain.
        focused_schema = self._build_focused_schema(selected_tables)

        # OPTIMIZATION: Removed redundant scope check (was making separate API call)
        # Now relying on natural failure path:
        # - If question is out of scope, table selection will return empty list
        # - We check for empty table list below and return appropriate error
        # This saves 1 API call per query with no loss of functionality
        
        # Early exit if no tables selected (indicates out-of-scope question)
        if not selected_tables:
            logger.info(f"No tables selected - question likely out of scope: {question[:80]}")
            return {
                "success": False,
                "sql_query": None,
                "error": (
                    "Your question doesn't appear to match any of the currently active tables. "
                    "The available tables contain data about sales, prescriptions, and medical records. "
                    "Please rephrase your question to match the available data, "
                    "or enable additional tables in the Table Filters panel."
                ),
                "out_of_scope": True,
                "is_federated": is_fed,
            }

        # Pre-compute exact column block for retry messages
        schema_columns_block = self._schema_columns_block(selected_tables)

        fed_rules = (
            f"\nFEDERATED MODE: Every table MUST be prefixed with its source alias — <alias>.<table>.\n"
            f"Available sources: {', '.join(self._sources.keys())}\n"
        ) if is_fed else ""

        system_prompt = f"""You are an expert pharma commercial data analyst. Your job is to write a correct SQL query that answers the user's question using ONLY the schema provided below.

{'='*70}
SCHEMA CONTEXT
{'='*70}
{focused_schema}
{'='*70}
END OF SCHEMA CONTEXT
{'='*70}

RULES — follow these precisely:

1. USE ONLY EXACT COLUMN AND TABLE NAMES from the SCHEMA CONTEXT above. Never invent or guess names.
   - CRITICAL: Copy column names EXACTLY as they appear in the schema, character by character
   - If a column name has underscores, numbers, or special capitalization, preserve it EXACTLY
   - Example: If schema shows "DRUG_PRODUCT_INFUSION_1_DATE", you MUST use that exact name
   - NEVER abbreviate, simplify, or "interpret" column names (e.g., don't use "INFUSION_DATE" when schema says "DRUG_PRODUCT_INFUSION_1_DATE")
   - If a column does not exist in the listed tables, do not use it
   - Check column descriptions carefully — they often explain the format and values

2. JOINS — STRICT RULE — you may ONLY join tables using the EXACT relationships
   listed in the "ESTABLISHED RELATIONSHIPS" block in the SCHEMA CONTEXT above:
   - Those relationships define the ONLY valid JOIN ON conditions — nothing else.
   - Copy the exact column names from the → SQL line shown for each relationship.
   - If two tables do NOT appear together in that block, you MUST NOT join them.
   - If the user's question seems to require joining unlisted tables, answer using
     only the single best table that already contains the needed columns.
   - NEVER guess, infer, or invent a join condition that is not explicitly listed.
   - Example: if schema shows "JOIN hospital_center ON dim_patient.SRC_ORG_ID = hospital_center.ORG_ID_COPS_CENTER_ID"
     then you MUST write exactly that — NOT "ON p.SRC_ORG_ID = h.ORG_ID" or any variation.

3. AGGREGATION — match the aggregation to what the user is asking:
   - "how many [entities]" → COUNT(DISTINCT <entity_id_column>)
   - "total amount/revenue/sales" → SUM(amount_column)
   - "average amount/value" → AVG(amount_column)
   - "average number of X per Y" → COUNT(DISTINCT x_id) * 1.0 / COUNT(DISTINCT y_id)
   - Never use AVG() on an ID or count column.

4. GROUP BY — ONLY add GROUP BY when the user EXPLICITLY asks for a breakdown:
   - User must say words like "by region", "per product", "for each rep", "grouped by", "breakdown by"
   - For "total", "overall", "how many", "what is the average" WITHOUT breakdown words → NO GROUP BY
   - NEVER assume the user wants data grouped by region/territory/product unless they specifically request it
   - Example: "What is total sales?" → No GROUP BY (just SUM)
   - Example: "What is total sales by region?" → GROUP BY region
   - Every non-aggregated SELECT column must appear in GROUP BY.

5. ORDER BY — ONLY add ORDER BY when the user EXPLICITLY asks for sorting or ranking:
   - User must say words like "top", "highest", "lowest", "best", "worst", "sorted by", "ordered by", "ranking"
   - For general questions without ranking words → NO ORDER BY
   - Example: "How many patients?" → No ORDER BY
   - Example: "Which regions have the highest sales?" → ORDER BY with DESC
   - NEVER add ORDER BY just to make results "look nice"

6. WHERE — only filter on values the user explicitly mentioned. Never add assumed filters.
   - Use LIKE '%value%' for partial text matches, not =.
   - For date ranges: check the sample data in the schema to determine the stored format before writing date comparisons.

7. DATES — Use date filters ONLY when the user explicitly asks for a time period:
   - "this quarter", "this month", "this year" → Add appropriate date filter
   - "last 30 days", "since January" → Add appropriate date filter
   - NO date filter needed for general questions like "highest", "total", "average"
   - If date column type is TEXT/VARCHAR, use the conversion in the column description
   - SQLite TEXT dates (DD-MM-YYYY format) → DATE(substr(col,7,4)||'-'||substr(col,4,2)||'-'||substr(col,1,2))
   - When date filter IS needed, always add: WHERE col IS NOT NULL AND col != ''

8. TOP-N and RANKING — use ORDER BY ... LIMIT N. Use ROW_NUMBER() OVER (PARTITION BY ...) only for "top N per group".

9. COMPARISONS — use GROUP BY the dimension column, never UNION. This keeps rows labeled.

10. SIMPLICITY — Prefer simple queries:
   - Start with the minimum columns needed to answer the question
   - Avoid complex date parsing unless explicitly required by the question
   - Don't add filters that weren't requested
   - If unsure about a filter, leave it out

11. COLUMN NAMING PREFERENCES — When user mentions generic terms, prefer generic columns:
   - "center" or "treatment center" → Use ORG_NAME or CENTER_NAME columns, NOT APHERESIS_ORG_NAME or INFUSION_ORG_NAME
   - "org" or "organization" → Use ORG_NAME or generic organization columns
   - "physician", "doctor", "provider" → Look for HCP, CONTACT_FULL_NAME, PHYSICIAN_NAME, or similar columns
   - "rep", "sales rep", "representative" → Look for REP_NAME, USER_NAME, SALES_REP, or similar columns
   - Only use specific columns like APHERESIS_ORG_NAME or INFUSION_ORG_NAME when user EXPLICITLY mentions "apheresis center" or "infusion center"
   - Look for columns with simpler/generic names first before using prefixed variants
   - IMPORTANT: Read column DESCRIPTIONS in the schema — they explain what each column contains and help identify the right column

12. INFUSION DATA — When user asks about "infusion", "infusion volume", or "infusion date":
   - DEFAULT to INFUSION_1 or DRUG_PRODUCT_INFUSION_1_DATE columns ONLY
   - Do NOT include INFUSION_2, DRUG_PRODUCT_INFUSION_2_DATE, or other numbered infusions
   - Only include infusion 2, 3, etc. if user EXPLICITLY mentions "infusion 2", "second infusion", "all infusions", or "total infusions"
   - For counting infusions: COUNT rows where DRUG_PRODUCT_INFUSION_1_DATE IS NOT NULL (not summing multiple infusion columns)

13. TABLE SELECTION — When deciding which tables to use:
   - Read the column DESCRIPTIONS carefully — they explain what data each column contains
   - Match user concepts to column descriptions, not just column names
   - Example: If user asks about "physicians", look for columns described as "healthcare provider", "HCP", "doctor", or "contact name"
   - Example: If user asks about "calls" or "interactions", look for tables/columns described as "call activity", "interaction", or "visit"

14. OUTPUT — return raw SQL only. No markdown, no explanation. Give aggregated columns readable aliases.
   Never use SELECT *. Only SELECT columns that answer the question.
   Only SELECT statements — no INSERT, UPDATE, DELETE, DROP.
   If the question has multiple parts but only some can be answered from available tables,
   write SQL for the parts you CAN answer — do not refuse the entire query.
{('15. FEDERATED: prefix every table with its source alias as shown in the schema.') if is_fed else ''}

COLUMN SELECTION PREFERENCE (by user role):
{'Executive role: Prefer aggregate totals and KPI-level columns (revenue, market share, counts). Focus on high-level metrics.' if persona == 'executive' else ''}
{'Sales Manager role: Prefer territory/region breakdown columns and performance vs target columns. Include comparative metrics.' if persona == 'sales_manager' else ''}
{'Field Rep role: Prefer account-level identifier columns and per-HCP/account metrics. Include specific account details.' if persona == 'field_rep' else ''}
{'Analyst role: Prefer granular columns and include count/distribution columns where available. Include detailed breakdown columns.' if persona == 'analyst' else ''}

COLUMN NAME VALIDATION:
Before you write the SQL, re-read the SCHEMA CONTEXT above and verify that EVERY column and table name you plan to use appears EXACTLY as shown. If you cannot find an exact match, DO NOT guess or create variations — report that the data is not available."""

        # Build conversation history block for context
        history_block = ""
        if conversation_history:
            recent = conversation_history[-6:]  # last 6 turns max
            lines = ["CONVERSATION HISTORY (most recent first — use for follow-up context only):"]
            for i, entry in enumerate(reversed(recent), 1):
                q = entry.get("question", "")
                sql = entry.get("sql_query") or entry.get("sql", "")
                summary = entry.get("summary", "")
                src = entry.get("sourceFilter") or entry.get("source_filter", "")
                lines.append(f"\n[Turn -{i}] Source: {src}")
                lines.append(f"  Q: {q}")
                if sql:
                    lines.append(f"  SQL: {sql}")
                if summary:
                    lines.append(f"  Answer: {summary[:200]}")
            history_block = "\n".join(lines) + "\n\n"

        user_prompt = f"{history_block}Current Question: {question}"

        last_error = None
        for attempt in range(max_retries):
            try:
                messages = [{"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}]

                if last_error and attempt > 0:
                    # Inject EXACT column names + targeted fix guidance
                    error_lower = last_error.lower()
                    if "no such column" in error_lower or "column" in error_lower:
                        error_hint = "→ You used a column name that doesn't exist. Use ONLY the exact names below."
                    elif "no such table" in error_lower or "table" in error_lower:
                        error_hint = "→ You referenced a table that doesn't exist or used the wrong source alias."
                    elif "syntax error" in error_lower or "parse error" in error_lower:
                        error_hint = "→ SQL syntax error. Check parentheses, commas, and keyword spelling."
                    elif "binder error" in error_lower or "does not have a column" in error_lower:
                        error_hint = "→ Column not found in that table. Check the exact column list below."
                    elif "ambiguous" in error_lower:
                        error_hint = "→ Column name is ambiguous. Qualify it with the table alias: alias.column_name"
                    else:
                        error_hint = "→ Fix the SQL based on the error and the exact schema below."

                    messages.append({
                        "role": "user",
                        "content": (
                            f"Previous SQL failed:\n  {last_error}\n"
                            f"{error_hint}\n\n"
                            f"{schema_columns_block}\n\n"
                            "Rewrite the complete SQL using ONLY the exact column names listed above.\n"
                            "Do not keep any column name from the failed query unless it appears in the list above."
                        )
                    })

                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=600,
                )

                sql_query = resp.choices[0].message.content.strip()
                if "```" in sql_query:
                    sql_query = "\n".join(
                        l for l in sql_query.split("\n")
                        if not l.strip().startswith("```")
                    ).strip()

                self._last_result_df = self.db_manager.execute_query(sql_query)
                logger.info(f"SQL attempt {attempt + 1} OK: {sql_query[:80]}...")
                return {
                    "success": True,
                    "sql_query": sql_query,
                    "row_count": len(self._last_result_df),
                    "error": None,
                    "is_federated": is_fed,
                    "tables_used": selected_tables,
                }

            except Exception as e:
                last_error = str(e)
                logger.warning(f"SQL attempt {attempt + 1} failed: {e}")

        return {
            "success": False,
            "sql_query": None,
            "error": f"Failed after {max_retries} attempts. Last error: {last_error}",
            "is_federated": is_fed,
        }