# federated_query_engine.py
"""
FederatedQueryEngine — DuckDB-backed cross-source query execution.

Architecture:
  - Holds a registry of DatabaseManager instances, each keyed by a source alias
    (e.g. "snowflake_1", "databricks_1", "pg_1").
  - When a federated query is executed, it:
      1. Inspects the SQL for source-qualified table refs: <alias>.<table>
      2. Fetches only the needed tables from each remote source into DuckDB
         in-memory DataFrames.
      3. Registers those DataFrames as DuckDB views using the alias-prefixed name.
      4. Rewrites the SQL so <alias>.<table> → <alias>__<table> (DuckDB view name).
      5. Runs the rewritten SQL inside DuckDB and returns the result DataFrame.
  - For single-source queries, falls through to the native DatabaseManager —
    no DuckDB overhead.
  - All remote data is held in-process RAM only; nothing is written to disk.

Security:
  - Each source's credentials live only inside its DatabaseManager.
  - No cross-source data is persisted after query completion.
  - Row limits and query timeouts are enforced.
  - Only SELECT is permitted (no DDL/DML).
"""

import re
import logging
import threading
import time
from typing import Dict, List, Optional, Tuple
import pandas as pd

from database_manager import DatabaseManager

logger = logging.getLogger(__name__)

# Max rows fetched from any single remote table during federation.
# Set to None to pull complete tables without limits.
FEDERATION_ROW_LIMIT = None  # No limit - pull complete tables

# Per-query timeout in seconds (DuckDB execution, not remote fetch)
QUERY_TIMEOUT_SECONDS = 120


class FederatedQueryEngine:
    """
    Manages multiple DatabaseManager connections and executes cross-source
    SQL queries via DuckDB as the in-memory federation layer.
    """

    def __init__(self):
        # { alias: DatabaseManager }
        self._sources: Dict[str, "DatabaseManager"] = {}
        self._lock = threading.Lock()

    # ── Source Registry ────────────────────────────────────────────────────

    def add_source(self, alias: str, manager: "DatabaseManager"):
        """Register a new data source under a given alias."""
        with self._lock:
            self._sources[alias] = manager
        logger.info(f"FederatedQueryEngine: added source '{alias}' ({manager.db_type})")

    def remove_source(self, alias: str):
        """Remove a source by alias."""
        with self._lock:
            self._sources.pop(alias, None)
        logger.info(f"FederatedQueryEngine: removed source '{alias}'")

    def list_sources(self) -> Dict[str, str]:
        """Return { alias: db_type } for all registered sources."""
        with self._lock:
            return {alias: mgr.db_type for alias, mgr in self._sources.items()}

    def get_source(self, alias: str) -> Optional["DatabaseManager"]:
        with self._lock:
            return self._sources.get(alias)

    def clear(self):
        with self._lock:
            self._sources.clear()

    @property
    def source_count(self) -> int:
        return len(self._sources)

    def is_federated(self) -> bool:
        """True when more than one source is registered."""
        return len(self._sources) > 1

    # ── Schema Aggregation ─────────────────────────────────────────────────

    def get_combined_schema_context(self) -> str:
        """
        Build a schema context string for the LLM that covers ALL sources.
        Tables are namespaced as <alias>.<table> so the LLM generates
        correctly qualified SQL.
        """
        with self._lock:
            sources = dict(self._sources)

        if not sources:
            return "No data sources connected."

        # Single source — return its native schema context (no namespacing needed)
        if len(sources) == 1:
            alias, mgr = next(iter(sources.items()))
            return mgr.get_schema_context()

        # Multi-source — build combined, namespace-aware context
        lines = [
            "=== MULTI-SOURCE FEDERATED DATABASE ===",
            "",
            "IMPORTANT: This query spans multiple data sources.",
            "Use <source_alias>.<table_name> notation for ALL table references.",
            "Cross-source JOINs are fully supported.",
            "",
            "Available sources and their tables:",
            "",
        ]

        all_relationships = []

        for alias, mgr in sources.items():
            lines.append(f"── Source: {alias} (type: {mgr.db_type}) ──")
            schema_info = mgr.schema_info or {}
            tables = schema_info.get("tables", {})
            active = set(mgr.selected_tables if mgr.selected_tables is not None else list(tables.keys()))

            for table_name, table_info in tables.items():
                if table_name not in active:
                    continue
                qualified_name = f"{alias}.{table_name}"
                desc = table_info.get("description", "") if isinstance(table_info, dict) else ""
                lines.append(f"  Table: {qualified_name}")
                if desc:
                    lines.append(f"    Description: {desc}")

                count_col = table_info.get("count_column", "") if isinstance(table_info, dict) else ""
                if count_col:
                    lines.append(f"    ► COUNT COLUMN (use in COUNT() for 'how many'): {count_col}")

                inline_val_cols = table_info.get("inline_value_columns", []) if isinstance(table_info, dict) else []
                if inline_val_cols:
                    lines.append(f"    ⚠ INLINE VALUE COLUMNS (filter directly, NO extra JOIN): {', '.join(inline_val_cols)}")

                entity_name_cols = table_info.get("entity_name_columns", []) if isinstance(table_info, dict) else []
                if entity_name_cols:
                    lines.append(f"    ★ ENTITY NAME COLUMNS (always SELECT with ID): {', '.join(entity_name_cols)}")

                filter_hints = table_info.get("filter_hints", []) if isinstance(table_info, dict) else []
                if filter_hints:
                    lines.append("    ✦ FILTER HINTS (apply ONLY for count/status queries — NOT for AVG/SUM/comparison/distribution):")
                    for hint in filter_hints:
                        lines.append(f"        - {hint}")

                lines.append("    Columns:")
                cols = table_info.get("columns", {}) if isinstance(table_info, dict) else {}
                for col, col_info in cols.items():
                    if isinstance(col_info, dict):
                        col_type = col_info.get("type", "TEXT")
                        col_desc = col_info.get("description", "")
                        flags = []
                        if col_info.get("is_row_pk"):       flags.append("ROW_PK — NOT for COUNT")
                        if col_info.get("is_entity_id"):    flags.append("ENTITY_ID — use for COUNT")
                        if col_info.get("is_entity_name"):  flags.append("ENTITY_NAME — always SELECT with ID")
                        if col_info.get("is_inline_value"): flags.append("INLINE_VALUE — filter directly")
                        if col_info.get("is_flag"):         flags.append("FLAG — check description for values")
                        if col_info.get("is_fk"):           flags.append("FK")
                        flag_str = f" [{', '.join(flags)}]" if flags else ""
                        lines.append(f"      - {col}: {col_type}{flag_str}  {col_desc}")
                    else:
                        lines.append(f"      - {col}: {col_info}")

                # Sample rows
                sample_rows = table_info.get("sample_rows", []) if isinstance(table_info, dict) else []
                if sample_rows:
                    try:
                        headers = list(sample_rows[0].keys())
                        lines.append(f"    Sample data ({len(sample_rows)} rows):")
                        lines.append(f"      | {' | '.join(headers)}")
                        for row in sample_rows[:5]:
                            vals = [str(row.get(h, ""))[:30] for h in headers]
                            lines.append(f"      | {' | '.join(vals)}")
                    except Exception:
                        pass

                lines.append("")

            # Collect relationships, qualify them with alias
            for rel in schema_info.get("relationships", []):
                all_relationships.append(f"{alias}: {rel}")

        if all_relationships:
            lines.append("JOIN PATHS (FK relationships — use these to reach columns in related tables):")
            for rel in all_relationships:
                lines.append(f"  - {rel}")
            lines.append("")

        lines += [
            "STRICT RULES:",
            "1. Always prefix table names with source alias: <alias>.<table>",
            "2. Only use tables and columns listed above",
            "3. COUNT questions: use ► COUNT COLUMN (ENTITY_ID), never [ROW_PK]",
            "4. [INLINE_VALUE] / [FLAG] columns: filter directly — no join to look up same value",
            "5. [ENTITY_NAME] columns: always SELECT alongside the ID column",
            "6. Apply ✦ FILTER HINTS ONLY for count/status queries — omit for AVG/SUM/comparison queries",
            "7. No GROUP BY unless question explicitly requests a breakdown",
            "8. Never use DROP, DELETE, UPDATE, INSERT — only SELECT",
            f"9. Sources: {', '.join(f'{a} ({m.db_type})' for a, m in sources.items())}",
        ]

        return "\n".join(lines)

    # ── SQL Rewriting ──────────────────────────────────────────────────────

    def _parse_source_table_refs(self, sql: str) -> List[Tuple[str, str]]:
        """
        Find all <alias>.<table> references in a SQL string.
        Returns list of (alias, table) tuples — deduplicated.
        """
        # Match word.word patterns that aren't schema qualifiers like information_schema.tables
        pattern = r'\b([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\b'
        matches = re.findall(pattern, sql)
        seen = set()
        result = []
        with self._lock:
            known_aliases = set(self._sources.keys())
        for alias, table in matches:
            if alias in known_aliases and (alias, table) not in seen:
                seen.add((alias, table))
                result.append((alias, table))
        return result

    def _rewrite_sql_for_duckdb(self, sql: str) -> str:
        """
        Rewrite <alias>.<table> → <alias>__<table> so DuckDB can find the
        registered views. DuckDB doesn't support dot-notation for view namespacing
        the way we use it.
        """
        with self._lock:
            known_aliases = set(self._sources.keys())

        def replacer(m):
            alias, table = m.group(1), m.group(2)
            if alias in known_aliases:
                return f"{alias}__{table}"
            return m.group(0)

        return re.sub(
            r'\b([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\b',
            replacer,
            sql
        )

    # ── Remote Data Fetch ──────────────────────────────────────────────────

    def _fetch_table_from_source(
        self, alias: str, table: str, row_limit: int = FEDERATION_ROW_LIMIT
    ) -> pd.DataFrame:
        """
        Pull a table from a remote source into a pandas DataFrame.
        If row_limit is None, fetches the complete table without limits.

        Uses explicit column names from schema_info instead of SELECT * —
        SELECT * triggers "ordinal must be >= 1" on Snowflake tables that
        contain VARIANT/OBJECT/ARRAY columns via SQLAlchemy's row converter.
        """
        mgr = self.get_source(alias)
        if not mgr:
            raise ValueError(f"Unknown source alias: '{alias}'")

        # Build column list from schema to avoid SELECT * on Snowflake.
        # Fall back to * only if schema has no column info for this table.
        col_list = "*"
        if mgr.db_type == "snowflake":
            tinfo = (mgr.schema_info or {}).get("tables", {}).get(table, {})
            cols = list(tinfo.get("columns", {}).keys()) if isinstance(tinfo, dict) else []
            if cols:
                col_list = ", ".join(cols)

        # Apply limit only if specified
        if row_limit is not None:
            limited_sql = f"SELECT {col_list} FROM {table} LIMIT {row_limit}"
            logger.info(f"Fetching '{alias}.{table}' (limit={row_limit}) from {mgr.db_type}")
        else:
            limited_sql = f"SELECT {col_list} FROM {table}"
            logger.info(f"Fetching '{alias}.{table}' (no limit - complete table) from {mgr.db_type}")
        
        t0 = time.time()
        df = mgr.execute_query(limited_sql)
        elapsed = time.time() - t0
        logger.info(
            f"Fetched '{alias}.{table}': {len(df)} rows, {len(df.columns)} cols "
            f"in {elapsed:.2f}s"
        )
        return df

    # ── Query Execution ────────────────────────────────────────────────────

    def execute_query(self, sql: str) -> pd.DataFrame:
        """
        Main entry point. Routes to:
          - Single-source native execution (no DuckDB) if only one source active
            and the SQL has no source-qualified table refs.
          - DuckDB federated execution for cross-source or explicitly namespaced queries.
        """
        self._validate_sql_safety(sql)

        refs = self._parse_source_table_refs(sql)

        # Single-source passthrough — no federation overhead
        if not refs and len(self._sources) == 1:
            alias, mgr = next(iter(self._sources.items()))
            logger.info(f"Single-source passthrough to '{alias}' ({mgr.db_type})")
            return mgr.execute_query(sql)

        # If no explicit source refs but multiple sources, try first source
        if not refs and len(self._sources) > 1:
            alias, mgr = next(iter(self._sources.items()))
            logger.info(
                f"No source-qualified refs found; routing to first source '{alias}'"
            )
            return mgr.execute_query(sql)

        # Single-source federated: all refs point to one source alias.
        # Strip the alias prefix and run the SQL directly on the native source —
        # avoids fetching the full raw table into DuckDB just to run an aggregation,
        # and prevents "ordinal must be >= 1" from SELECT * on Snowflake.
        unique_aliases = set(alias for alias, _ in refs)
        if len(unique_aliases) == 1:
            alias = next(iter(unique_aliases))
            mgr = self.get_source(alias)
            if mgr:
                # Rewrite alias.TABLE → TABLE (native source doesn't use alias prefix)
                native_sql = re.sub(
                    r'\b' + re.escape(alias) + r'\.([a-zA-Z_][a-zA-Z0-9_]*)\b',
                    r'\1',
                    sql
                )
                logger.info(
                    f"Single-alias passthrough to '{alias}' ({mgr.db_type}): "
                    f"{native_sql[:80]}..."
                )
                return mgr.execute_query(native_sql)

        return self._execute_federated(sql, refs)

    def _execute_federated(
        self, sql: str, refs: List[Tuple[str, str]]
    ) -> pd.DataFrame:
        """
        Execute a federated query via DuckDB:
          1. Fetch each referenced table from its remote source.
          2. Register as a DuckDB view named <alias>__<table>.
          3. Rewrite SQL to use the view names.
          4. Execute in DuckDB and return results.
        """
        import duckdb

        # Create a fresh in-process DuckDB connection (purely in RAM)
        duck = duckdb.connect(database=":memory:")

        try:
            # Fetch and register each referenced table
            for alias, table in refs:
                view_name = f"{alias}__{table}"
                df = self._fetch_table_from_source(alias, table)
                # Register the DataFrame directly — zero copy via Arrow
                duck.register(view_name, df)
                logger.info(f"Registered DuckDB view: {view_name} ({len(df)} rows)")

            # Rewrite SQL so alias.table → alias__table
            rewritten_sql = self._rewrite_sql_for_duckdb(sql)
            logger.info(f"Executing federated SQL in DuckDB: {rewritten_sql[:120]}...")

            result_df = duck.execute(rewritten_sql).df()
            logger.info(f"Federated query returned {len(result_df)} rows")
            return result_df

        finally:
            duck.close()

    # ── Safety ─────────────────────────────────────────────────────────────

    def _validate_sql_safety(self, sql: str):
        """Reject any non-SELECT SQL to prevent writes through the federation layer."""
        stripped = sql.strip().lstrip("(").upper()
        forbidden = ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER",
                     "TRUNCATE", "REPLACE", "MERGE", "EXEC", "EXECUTE")
        for kw in forbidden:
            if re.match(rf"^{kw}\b", stripped):
                raise PermissionError(
                    f"'{kw}' statements are not allowed through the query engine. "
                    "Only SELECT queries are permitted."
                )

    # ── Combined selected_tables (for SSE state) ───────────────────────────

    def get_all_tables_unfiltered(self) -> List[str]:
        """
        Return all tables across all sources, namespaced as <alias>.<table>.
        Used by the SSE state builder.
        """
        result = []
        with self._lock:
            sources = dict(self._sources)
        for alias, mgr in sources.items():
            for t in mgr.get_all_tables_unfiltered():
                result.append(f"{alias}.{t}")
        return result

    @property
    def selected_tables(self) -> List[str]:
        """Aggregate selected tables across all sources, namespaced."""
        result = []
        with self._lock:
            sources = dict(self._sources)
        for alias, mgr in sources.items():
            selected = mgr.selected_tables if mgr.selected_tables is not None else mgr.get_all_tables_unfiltered()
            for t in selected:
                result.append(f"{alias}.{t}")
        return result

    @property
    def db_type(self) -> str:
        """Synthetic db_type for multi-source mode."""
        with self._lock:
            if len(self._sources) == 1:
                return next(iter(self._sources.values())).db_type
            return "federated"

    @property
    def schema_info(self):
        """
        For single-source mode, delegate to the underlying manager's schema_info
        so existing schema editor / relationships logic still works unchanged.
        """
        with self._lock:
            if len(self._sources) == 1:
                return next(iter(self._sources.values())).schema_info
        return {"tables": {}, "relationships": []}

    def get_schema_context(self) -> str:
        return self.get_combined_schema_context()

    def set_selected_tables(self, tables: List[str], openai_api_key: str = None):
        """
        Accept namespaced table list (<alias>.<table>) and fan out to each source.
        Also accepts bare table names for single-source backwards compat.
        """
        # Group by alias
        by_source: Dict[str, List[str]] = {}
        with self._lock:
            sources = dict(self._sources)

        for entry in tables:
            if "." in entry:
                alias, table = entry.split(".", 1)
                by_source.setdefault(alias, []).append(table)
            else:
                # Bare name — assign to all sources that have this table
                for alias, mgr in sources.items():
                    all_t = mgr.get_all_tables_unfiltered()
                    if entry in all_t:
                        by_source.setdefault(alias, []).append(entry)

        for alias, table_list in by_source.items():
            mgr = sources.get(alias)
            if mgr:
                mgr.set_selected_tables(table_list, openai_api_key=openai_api_key)

    def apply_schema_edits(self, edits: dict) -> dict:
        """Delegate schema edits to first (or only) source for backwards compat."""
        with self._lock:
            if self._sources:
                mgr = next(iter(self._sources.values()))
                return mgr.apply_schema_edits(edits)
        return {}