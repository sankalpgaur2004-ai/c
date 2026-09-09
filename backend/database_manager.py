# database_manager.py
import os
import re
import sqlite3
import logging
import time
import threading
import yaml
import pandas as pd
from openai import OpenAI
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

class DatabaseManager:

    # Single shared schema file used by ALL sources — one file forever.
    GLOBAL_SCHEMA_FILE = "db_schema.yaml"

    # Module-level lock — declared here so all instances share the same object
    # without relying on lazy hasattr tricks that confuse type checkers.
    _schema_file_lock: threading.Lock = threading.Lock()

    def __init__(self, db_path: str = None, db_type: str = "sqlite",
                 connection_config: Dict = None, schema_alias: str = None):
        self.db_type = db_type
        self.db_path = db_path
        self.connection_config = connection_config or {}
        self.selected_tables: Optional[List[str]] = None
        # schema_alias kept for backwards compat but no longer used for file naming
        self._schema_alias = schema_alias
        self.schema_info = self._load_schema()

        # Table cache — avoids hitting DB on every poll
        self._table_cache: Optional[Dict] = None
        self._table_cache_ttl = 60  # seconds

        logger.info(f"DatabaseManager initialized: db_type={db_type}")

    # ── Cache ──────────────────────────────────────────────────────────────

    def _is_cache_valid(self) -> bool:
        """Check if table cache is still fresh."""
        if not self._table_cache:
            return False
        age = time.time() - self._table_cache.get("cached_at", 0)
        return age < self._table_cache_ttl

    def invalidate_table_cache(self):
        """Force next call to re-query the DB."""
        self._table_cache = None

    # ── Schema YAML ────────────────────────────────────────────────────────

    def _get_schema_file_path(self) -> str:
        """Always return the single shared schema file — one file for all sources."""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base_dir, "data", self.GLOBAL_SCHEMA_FILE)

    @classmethod
    def _global_schema_lock(cls) -> threading.Lock:
        """Return the class-level lock shared across all DatabaseManager instances."""
        return cls._schema_file_lock

    def _table_key(self, table_name: str) -> str:
        """
        Namespace key used in the global YAML.
        When a schema_alias is set (always the case for CSV/user uploads, format: uid[:8]_alias),
        we use "<schema_alias>.<table_name>" to isolate each user's source entries.
        Falls back to "<db_type>.<table_name>" for legacy/unauthenticated sources.
        e.g. "abc12345_sp_patient.sp_patient_status"  (CSV, user-scoped)
             "databricks.dw_veeva_dbo_call"            (legacy, no alias)
        """
        prefix = self._schema_alias if self._schema_alias else self.db_type
        return f"{prefix}.{table_name}"

    def _read_global_yaml(self) -> Dict[str, Any]:
        """Read the full global schema YAML. Returns empty structure if missing."""
        schema_file = self._get_schema_file_path()
        try:
            if os.path.exists(schema_file):
                with open(schema_file, "r") as f:
                    data = yaml.safe_load(f) or {}
                return data
        except Exception as e:
            logger.error(f"Failed to read global schema YAML: {e}")
        return {}

    def _write_global_yaml(self, data: Dict[str, Any]):
        """Overwrite the global schema YAML atomically under the shared lock."""
        schema_file = self._get_schema_file_path()
        os.makedirs(os.path.dirname(schema_file), exist_ok=True)
        tmp = schema_file + ".tmp"
        with open(tmp, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=True)
        os.replace(tmp, schema_file)  # atomic on all OS

    def _load_schema(self) -> Dict[str, Any]:
        """
        Load this source's tables from the global schema YAML.
        Keys are namespaced as "<schema_alias>.<table>" (user-scoped) or
        "<db_type>.<table>" (legacy). Only entries matching THIS instance are returned.
        """
        try:
            global_data = self._read_global_yaml()
            tables = {}
            # Use schema_alias prefix when available (isolates per user+source),
            # fall back to db_type prefix for legacy entries
            prefix = f"{self._schema_alias}." if self._schema_alias else f"{self.db_type}."
            # Also check legacy db_type prefix so existing data migrates gracefully
            legacy_prefix = f"{self.db_type}."
            
            # Load tables for this source.
            # Skip entries that were written with no LLM content (empty description +
            # all column descriptions empty) — they are ghost entries from a previous
            # run that was interrupted or skipped.  Treating them as absent causes
            # set_selected_tables to re-run the LLM for them, which is the correct
            # behaviour (same as when the table is genuinely brand-new).
            for key, table_schema in global_data.items():
                matched_prefix = None
                if self._schema_alias and key.startswith(prefix):
                    matched_prefix = prefix
                elif not self._schema_alias and key.startswith(legacy_prefix):
                    matched_prefix = legacy_prefix
                if matched_prefix is None or not isinstance(table_schema, dict):
                    continue
                table_name = key[len(matched_prefix):]
                has_table_desc = bool(table_schema.get("description", "").strip())
                has_col_desc = any(
                    bool((v or {}).get("description", "").strip())
                    for v in (table_schema.get("columns") or {}).values()
                )
                if not has_table_desc and not has_col_desc:
                    logger.info(
                        f"_load_schema: skipping '{table_name}' — no LLM descriptions yet, "
                        f"will be regenerated on next set_selected_tables call"
                    )
                    continue
                tables[table_name] = table_schema
            
            # Load ALL relationships from the YAML — relationships are global state
            # shared across all connected sources and are not filtered per-source.
            # The old prefix-filter was incorrectly dropping relationships whose
            # left-hand table had no alias prefix (e.g. stored as bare table.col).
            all_relationships = global_data.get("relationships") or []
            
            if tables:
                logger.info(
                    f"Loaded {len(tables)} existing {self.db_type} tables, "
                    f"{len(all_relationships)} relationships from global schema"
                )
            else:
                logger.info(f"No existing schema for db_type={self.db_type} in global YAML")
            
            return {"tables": tables, "relationships": all_relationships}
        except Exception as e:
            logger.error(f"Failed to load schema: {e}")
            return {"tables": {}, "relationships": []}

    def _save_schema_to_yaml(self, schema: Dict[str, Any]):
        """
        Merge this source's tables into the single global YAML under the shared lock.
        Tables from other sources (different db_type prefix) are always preserved.
        Uses an atomic write (write-to-tmp then rename) to prevent corruption.
        """
        try:
            tables = schema.get("tables") or {}
            relationships = schema.get("relationships") or []
            
            with self._global_schema_lock():
                global_data = self._read_global_yaml()
                
                # Write every table under its namespaced key
                for table_name, table_schema in tables.items():
                    key = self._table_key(table_name)
                    global_data[key] = table_schema
                
                # Save relationships at top level as a global shared list.
                # Relationships are NOT scoped per-source — they are shared across
                # all connected sources and must be preserved exactly as-is.
                # Only update when a non-empty list is explicitly provided;
                # an empty list from a table-only save should NOT wipe existing rels.
                if relationships:
                    global_data["relationships"] = relationships
                elif "relationships" not in global_data:
                    global_data["relationships"] = []
                # else: keep existing relationships untouched
                
                self._write_global_yaml(global_data)
            logger.info(
                f"Global schema updated — wrote {len(tables)} {self.db_type} tables, "
                f"{len(relationships)} relationships (total keys in file: {len(global_data)})"
            )
        except Exception as e:
            logger.error(f"Failed to save schema to global YAML: {e}")

    # ── Connection ─────────────────────────────────────────────────────────

    def get_connection(self):
        """
        Return a live database connection.
        MySQL/PostgreSQL/Snowflake use cached engines with connection pooling.
        Databricks caches the connection object itself.
        SQLite opens a new connection each time (cheap, no pooling needed).
        CSV files are stored as SQLite databases internally.
        """
        if self.db_type in ("sqlite", "csv"):
            if not self.db_path or not os.path.exists(self.db_path):
                raise FileNotFoundError(f"SQLite file not found: {self.db_path}")
            return sqlite3.connect(self.db_path)

        elif self.db_type == "postgresql":
            # Cache engine — pool_size=5 keeps 5 connections alive
            if not hasattr(self, '_pg_engine'):
                from sqlalchemy import create_engine
                cfg = self.connection_config
                url = (
                    f"postgresql+psycopg2://{cfg['user']}:{cfg['password']}"
                    f"@{cfg['host']}:{cfg.get('port', 5432)}/{cfg['database']}"
                )
                self._pg_engine = create_engine(
                    url,
                    pool_size=10,         # Increased from 5 to 10
                    max_overflow=5,       # Increased from 2 to 5
                    pool_pre_ping=True,   # test connection before use
                    pool_timeout=60,      # Added 60 second timeout
                    pool_recycle=300      # recycle every 5 min
                )
            return self._pg_engine.connect()

        elif self.db_type == "mysql":
            # Cache engine with connection pool
            if not hasattr(self, '_mysql_engine'):
                from sqlalchemy import create_engine
                cfg = self.connection_config
                url = (
                    f"mysql+pymysql://{cfg['user']}:{cfg['password']}"
                    f"@{cfg['host']}:{cfg.get('port', 3306)}/{cfg['database']}"
                )
                self._mysql_engine = create_engine(
                    url,
                    pool_size=10,         # Increased from 5 to 10
                    max_overflow=5,       # Increased from 2 to 5
                    pool_pre_ping=True,
                    pool_timeout=60,      # Added 60 second timeout
                    pool_recycle=300
                )
            return self._mysql_engine.connect()

        elif self.db_type == "databricks":
            # Cache the connection — Databricks cold start is expensive (~3-5s)
            # Reuse the same connection for all queries
            if not hasattr(self, '_databricks_conn') or self._databricks_conn is None:
                from databricks import sql as databricks_sql
                cfg = self.connection_config
                self._databricks_conn = databricks_sql.connect(
                    server_hostname=cfg["server_hostname"],
                    http_path=cfg["http_path"],
                    access_token=cfg["access_token"]
                )
                logger.info("Databricks connection created and cached")
            return self._databricks_conn

        elif self.db_type == "snowflake":
            # Cache Snowflake engine — cold start is ~5-8s
            if not hasattr(self, '_snowflake_engine'):
                from sqlalchemy import create_engine
                from urllib.parse import quote_plus
                # Import snowflake-sqlalchemy to register the dialect
                # (dynamic import avoids editor unresolved-import diagnostics)
                __import__("snowflake.sqlalchemy")
                cfg = self.connection_config
                # URL-encode user and password — special chars (@, #, /, etc.)
                # in raw credentials break SQLAlchemy's URL parser and cause
                # misleading "incorrect username or password" errors from Snowflake.
                encoded_user = quote_plus(str(cfg['user']))
                encoded_password = quote_plus(str(cfg['password']))
                url = (
                    f"snowflake://{encoded_user}:{encoded_password}"
                    f"@{cfg['account']}/{cfg['database']}/{cfg.get('schema', 'PUBLIC')}"
                    f"?warehouse={cfg['warehouse']}"
                )
                self._snowflake_engine = create_engine(
                    url,
                    pool_size=10,           # Increased from 3 to 10
                    max_overflow=5,         # Increased from 1 to 5
                    pool_pre_ping=True,
                    pool_timeout=60,        # Increased from 30 to 60 seconds
                    pool_recycle=300        # Recycle connections every 5 minutes
                )
                logger.info("Snowflake engine created and cached")
            return self._snowflake_engine.connect()

        else:
            raise ValueError(f"Unsupported db_type: {self.db_type}")

    def _close_connection(self, conn):
        """
        Close connection.
        For pooled engines (SQLAlchemy), .close() returns to pool — not truly closed.
        For Databricks we never close — reuse cached connection.
        """
        if self.db_type in ("postgresql", "mysql", "snowflake"):
            try:
                conn.close()
            except:
                pass
        # Databricks: intentionally skip — keep connection alive

    # ── Query Execution ────────────────────────────────────────────────────

    def execute_query(self, query: str) -> pd.DataFrame:
        """
        Execute SQL and return DataFrame.
        Databricks and Snowflake use the cursor API to avoid SQLAlchemy's
        'ordinal must be >= 1' row conversion bug that occurs with SELECT *.
        All other sources use pd.read_sql_query.
        """
        try:
            logger.info(f"Executing on {self.db_type}: {query[:100]}...")

            if self.db_type == "databricks":
                conn = self.get_connection()  # returns cached connection
                cursor = conn.cursor()
                cursor.execute(query)
                rows = cursor.fetchall()
                cols = [desc[0] for desc in cursor.description]
                cursor.close()
                # Don't close conn — it's cached
                return pd.DataFrame(rows, columns=cols)

            elif self.db_type == "snowflake":
                # Use engine.raw_connection() to get a plain DBAPI connection,
                # bypassing SQLAlchemy's row conversion which raises
                # "ordinal must be >= 1" on VARIANT/OBJECT/ARRAY columns.
                # raw_connection() is the stable API in both SQLAlchemy 1.4 and 2.x.
                raw_conn = self._snowflake_engine.raw_connection()
                try:
                    cursor = raw_conn.cursor()
                    try:
                        cursor.execute(query)
                        rows = cursor.fetchall()
                        cols = [desc[0] for desc in cursor.description]
                    finally:
                        cursor.close()
                finally:
                    raw_conn.close()
                return pd.DataFrame(rows, columns=cols)

            else:
                conn = self.get_connection()
                df = pd.read_sql_query(query, conn)
                self._close_connection(conn)
                return df

        except Exception as e:
            logger.error(f"Query execution failed: {e}")
            raise

    # ── Table Discovery ────────────────────────────────────────────────────

    def _fetch_live_tables(self) -> List[str]:
        """
        Actually query the database for table names.
        Only called on cache miss — not on every poll.
        """
        try:
            conn = self.get_connection()

            if self.db_type in ("sqlite", "csv"):
                df = pd.read_sql_query(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'", conn
                )
                return df['name'].tolist()

            elif self.db_type == "mysql":
                df = pd.read_sql_query("SHOW TABLES", conn)
                self._close_connection(conn)
                return df.iloc[:, 0].tolist()

            elif self.db_type == "postgresql":
                df = pd.read_sql_query(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_type='BASE TABLE'", conn
                )
                self._close_connection(conn)
                return df['table_name'].tolist()

            elif self.db_type == "databricks":
                cursor = conn.cursor()
                cfg = self.connection_config
                schema = cfg.get("schema", "default")
                catalog = cfg.get("catalog", None)
                query = (
                    f"SHOW TABLES IN {catalog}.{schema}"
                    if catalog else
                    f"SHOW TABLES IN {schema}"
                )
                cursor.execute(query)
                rows = cursor.fetchall()
                cursor.close()
                # SHOW TABLES → (database, tableName, isTemporary)
                return [row[1] if len(row) >= 2 else row[0] for row in rows]

            elif self.db_type == "snowflake":
                df = pd.read_sql_query(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = CURRENT_SCHEMA() "
                    "AND table_type = 'BASE TABLE'",
                    conn
                )
                self._close_connection(conn)
                df.columns = [c.lower() for c in df.columns]
                return df['table_name'].tolist()

            return []

        except Exception as e:
            logger.error(f"_fetch_live_tables failed: {e}")
            raise

    def get_available_tables(self) -> List[str]:
        """
        Get tables with caching.
        First call hits the DB, subsequent calls return cache until TTL expires.
        Reduces DB roundtrips from every 4s poll to every 60s.
        """
        # Return cache if still fresh
        if self._is_cache_valid():
            cached = self._table_cache["tables"]
            if self.selected_tables is not None:
                return [t for t in cached if t in self.selected_tables]
            return cached

        # Cache miss — query live DB
        try:
            logger.info(f"Cache miss — fetching live tables for {self.db_type}")
            tables = self._fetch_live_tables()

            # Store in cache
            self._table_cache = {
                "tables": tables,
                "cached_at": time.time()
            }

            if self.selected_tables is not None:
                return [t for t in tables if t in self.selected_tables]
            return tables

        except Exception as e:
            logger.error(f"get_available_tables failed: {e}")
            # Return stale cache rather than empty on error
            if self._table_cache:
                logger.info("Returning stale cache due to error")
                return self._table_cache["tables"]
            return []

    def get_all_tables_unfiltered(self) -> List[str]:
        """
        Return ALL tables ignoring selection filter.
        Uses cache — never hits DB if cache is warm.
        """
        if self._is_cache_valid():
            return self._table_cache["tables"]

        # No cache — fetch and store
        try:
            tables = self._fetch_live_tables()
            self._table_cache = {"tables": tables, "cached_at": time.time()}
            return tables
        except Exception as e:
            logger.error(f"get_all_tables_unfiltered failed: {e}")
            return []

    # ── Schema Context ─────────────────────────────────────────────────────

    def _get_columns_for_table(self, table_name: str, conn) -> Dict[str, str]:
        """Fetch column names and types for a single table."""
        try:
            if self.db_type in ("sqlite", "csv"):
                df = pd.read_sql_query(f"PRAGMA table_info({table_name})", conn)
                return {row['name']: row['type'] for _, row in df.iterrows()}

            elif self.db_type == "mysql":
                df = pd.read_sql_query(f"DESCRIBE {table_name}", conn)
                return {row['Field']: row['Type'] for _, row in df.iterrows()}

            elif self.db_type == "postgresql":
                df = pd.read_sql_query(f"""
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_name = '{table_name}'
                    AND table_schema = 'public'
                """, conn)
                return {row['column_name']: row['data_type'] for _, row in df.iterrows()}

            elif self.db_type == "databricks":
                cursor = conn.cursor()
                cursor.execute(f"DESCRIBE TABLE {table_name}")
                rows = cursor.fetchall()
                cursor.close()
                return {
                    row[0]: row[1] for row in rows
                    if row[0] and not row[0].startswith('#')
                }

            elif self.db_type == "snowflake":
                df = pd.read_sql_query(f"""
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_name = UPPER('{table_name}')
                    AND table_schema = CURRENT_SCHEMA()
                """, conn)
                df.columns = [c.lower() for c in df.columns]
                return {row['column_name']: row['data_type'] for _, row in df.iterrows()}

        except Exception as e:
            logger.error(f"Could not get columns for {table_name}: {e}")
            return {}

    def _get_sample_for_table(self, table_name: str, conn) -> str:
        """Fetch up to 10 sample rows for LLM context."""
        try:
            if self.db_type in ("databricks", "snowflake"):
                # Use DBAPI2 cursor — pd.read_sql_query doesn't support Databricks,
                # and Snowflake's SQLAlchemy layer raises "ordinal must be >= 1" with SELECT *.
                # For Snowflake: use engine.raw_connection() — stable in SQLAlchemy 1.4 & 2.x.
                if self.db_type == "snowflake":
                    raw_conn = self._snowflake_engine.raw_connection()
                    try:
                        cursor = raw_conn.cursor()
                        try:
                            cursor.execute(f"SELECT * FROM {table_name} LIMIT 10")
                            rows = cursor.fetchall()
                            cols = [desc[0] for desc in cursor.description]
                        finally:
                            cursor.close()
                    finally:
                        raw_conn.close()
                else:
                    cursor = conn.cursor()
                    cursor.execute(f"SELECT * FROM {table_name} LIMIT 10")
                    rows = cursor.fetchall()
                    cols = [desc[0] for desc in cursor.description]
                    cursor.close()
                df = pd.DataFrame(rows, columns=cols)
            else:
                df = pd.read_sql_query(f"SELECT * FROM {table_name} LIMIT 10", conn)
            return df.to_string(index=False)
        except:
            return ""
        
    def _detect_datetime_format_from_samples(self, sample_values: List[str]) -> Optional[Dict[str, str]]:
        """
        Detect if VARCHAR/TEXT column contains date/timestamp data and determine the format.
        
        Returns:
            Dict with 'detected_type' (DATE/TIMESTAMP), 'sample_format' (e.g., '2024-01-15 14:30:00'),
            and 'conversion_format' (format string for TO_TIMESTAMP) if datetime detected,
            None otherwise.
        """
        if not sample_values:
            return None
        
        # Common date/timestamp patterns and their conversion formats
        # Format: (regex_pattern, detected_type, conversion_format_postgresql, sample_format_description)
        datetime_patterns = [
            # ISO formats
            (r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z?$', 'TIMESTAMP', 'YYYY-MM-DD"T"HH24:MI:SS.US', 'ISO with microseconds'),
            (r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?$', 'TIMESTAMP', 'YYYY-MM-DD"T"HH24:MI:SS', 'ISO timestamp'),
            (r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+$', 'TIMESTAMP', 'YYYY-MM-DD HH24:MI:SS.US', 'datetime with microseconds'),
            (r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$', 'TIMESTAMP', 'YYYY-MM-DD HH24:MI:SS', 'datetime'),
            (r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$', 'TIMESTAMP', 'YYYY-MM-DD HH24:MI', 'datetime no seconds'),
            (r'^\d{4}-\d{2}-\d{2}$', 'DATE', 'YYYY-MM-DD', 'ISO date'),
            
            # US formats (MM/DD/YYYY)
            (r'^\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2} [AP]M$', 'TIMESTAMP', 'MM/DD/YYYY HH12:MI:SS AM', 'US datetime 12hr'),
            (r'^\d{1,2}/\d{1,2}/\d{4} \d{2}:\d{2}:\d{2}$', 'TIMESTAMP', 'MM/DD/YYYY HH24:MI:SS', 'US datetime 24hr'),
            (r'^\d{1,2}/\d{1,2}/\d{4} \d{2}:\d{2}$', 'TIMESTAMP', 'MM/DD/YYYY HH24:MI', 'US datetime no seconds'),
            (r'^\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2} [AP]M$', 'TIMESTAMP', 'MM/DD/YYYY HH12:MI AM', 'US datetime 12hr no sec'),
            (r'^\d{1,2}/\d{1,2}/\d{4}$', 'DATE', 'MM/DD/YYYY', 'US date'),
            
            # European formats (DD/MM/YYYY or DD-MM-YYYY)
            (r'^\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}$', 'TIMESTAMP', 'DD/MM/YYYY HH24:MI:SS', 'EU datetime'),
            (r'^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$', 'TIMESTAMP', 'DD/MM/YYYY HH24:MI', 'EU datetime no seconds'),
            (r'^\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2}$', 'TIMESTAMP', 'DD-MM-YYYY HH24:MI:SS', 'EU datetime dash'),
            (r'^\d{2}-\d{2}-\d{4} \d{2}:\d{2}$', 'TIMESTAMP', 'DD-MM-YYYY HH24:MI', 'EU datetime dash no seconds'),
            (r'^\d{2}/\d{2}/\d{4}$', 'DATE', 'DD/MM/YYYY', 'EU date'),
            (r'^\d{2}-\d{2}-\d{4}$', 'DATE', 'DD-MM-YYYY', 'EU date dash'),
            
            # Compact formats (YYYYMMDD)
            (r'^\d{8}$', 'DATE', 'YYYYMMDD', 'compact date'),
            (r'^\d{14}$', 'TIMESTAMP', 'YYYYMMDDHH24MISS', 'compact datetime'),
            
            # Text month formats
            (r'^\d{1,2}-[A-Za-z]{3}-\d{4}$', 'DATE', 'DD-MON-YYYY', 'Oracle date'),
            (r'^\d{1,2}-[A-Za-z]{3}-\d{2}$', 'DATE', 'DD-MON-YY', 'Oracle date short year'),
            (r'^[A-Za-z]{3} \d{1,2}, \d{4}$', 'DATE', 'MON DD, YYYY', 'text month date'),
            (r'^[A-Za-z]+ \d{1,2}, \d{4}$', 'DATE', 'MONTH DD, YYYY', 'full month date'),
            
            # Time only
            (r'^\d{2}:\d{2}:\d{2}$', 'TIME', 'HH24:MI:SS', 'time 24hr'),
            (r'^\d{1,2}:\d{2}:\d{2} [AP]M$', 'TIME', 'HH12:MI:SS AM', 'time 12hr'),
            (r'^\d{1,2}:\d{2} [AP]M$', 'TIME', 'HH12:MI AM', 'time 12hr no sec'),
        ]
        
        # Test each sample value against patterns
        valid_samples = [str(v).strip() for v in sample_values if v and str(v).strip() and str(v).lower() not in ('none', 'null', 'nan', '')]
        if not valid_samples:
            return None
        
        # Need at least 50% of non-null samples to match a pattern
        min_matches = max(1, len(valid_samples) // 2)
        
        for pattern, detected_type, conversion_format, format_desc in datetime_patterns:
            matches = sum(1 for v in valid_samples if re.match(pattern, v, re.IGNORECASE))
            if matches >= min_matches:
                # Found a match - get a sample value that matches
                sample_value = next((v for v in valid_samples if re.match(pattern, v, re.IGNORECASE)), valid_samples[0])
                return {
                    'detected_type': detected_type,
                    'conversion_format': conversion_format,
                    'format_description': format_desc,
                    'sample_value': sample_value
                }
        
        return None

    def _infer_type_from_name(self, col_name: str, raw_type: str) -> str:
        """
        Improve on the raw DB type by inferring semantic type from the column name.
        The raw_type is kept if no name-based rule matches.
        """
        name = col_name.lower()
        # Date / time signals
        if any(name.endswith(s) for s in ("_date", "_at", "_time", "_timestamp", "_dt")):
            return "DATE" if "time" not in name and "timestamp" not in name else "TIMESTAMP"
        if name in ("date", "time", "timestamp", "created", "updated", "modified"):
            return "TIMESTAMP"
        # Boolean signals
        if name.startswith(("is_", "has_", "can_", "flag_")) or name.endswith(("_flag", "_bool", "_yn")):
            return "BOOLEAN"
        # ID / key signals — keep as raw type but normalise label
        if name.endswith("_id") or name == "id":
            return raw_type or "INTEGER"
        # Numeric signals
        if any(s in name for s in ("amount", "price", "cost", "revenue", "sales", "count",
                                    "qty", "quantity", "rate", "score", "age", "duration",
                                    "total", "sum", "avg", "percent", "pct", "num_")):
            return "NUMERIC"
        # Text / category signals
        if any(s in name for s in ("name", "desc", "description", "note", "comment",
                                    "address", "email", "phone", "url", "code", "type",
                                    "category", "status", "region", "country", "city",
                                    "brand", "product", "outcome", "reason")):
            return "TEXT"
        # Fall back to whatever the DB reported
        return raw_type or "TEXT"

    def _generate_table_metadata(
        self,
        table_name: str,
        columns: Dict[str, str],        # {col_name: raw_db_type from database}
        openai_api_key: str,
        sample_rows: list = None,
    ) -> Dict[str, Any]:
        """
        Generate clean schema metadata for a table.
        
        LLM generates ONLY:
          - Table description (detailed, explains what the table represents)
          - Column descriptions (detailed, explains purpose and usage)
          - Relationships (FK references to other tables)
        
        Data types come DIRECTLY from the database, not LLM.
        
        Returns:
            {
                "description": "...",
                "relationships": ["table.col -> other_table.col", ...],
                "columns": {
                    "col_name": {
                        "type": "VARCHAR",  # From DB
                        "description": "..."  # From LLM
                    }
                }
            }
        """
        import json
        
        sample_rows = sample_rows or []
        
        # Build fallback with DB types only
        fallback = {
            "description": f"Table {table_name}",
            "columns": {
                col: {"type": col_type, "description": ""}
                for col, col_type in columns.items()
            }
        }
        
        if not openai_api_key or not columns:
            logger.warning(f"[{table_name}] Skipping LLM call - API key present: {bool(openai_api_key)}, columns count: {len(columns)}")
            return fallback
        
        logger.info(f"[{table_name}] Calling OpenAI API for schema generation ({len(columns)} columns)")
        
        # Format columns for prompt - show actual DB types
        col_list = "\n".join([f"  - {col} ({db_type})" for col, db_type in columns.items()])
        
        # Format sample data
        sample_block = ""
        if sample_rows:
            try:
                headers = list(sample_rows[0].keys())
                rows_text = []
                for row in sample_rows[:5]:
                    vals = [str(row.get(h, ""))[:35] for h in headers]
                    rows_text.append("  " + " | ".join(vals))
                sample_block = f"\nSample data:\n  {' | '.join(headers)}\n" + "\n".join(rows_text)
            except Exception:
                sample_block = ""
        
        # Detect database type for date conversion instructions
        db_type = getattr(self, 'db_type', 'sqlite').lower()
        
        # Database-specific date conversion examples
        date_conversion_examples = {
            'sqlite': {
                'iso': "Use DATE(col) or JULIANDAY(col) for date operations",
                'dd-mm-yyyy': "Convert: DATE(substr(col,7,4)||'-'||substr(col,4,2)||'-'||substr(col,1,2))",
                'dd-mm-yyyy hh:mm': "Convert: DATETIME(substr(col,7,4)||'-'||substr(col,4,2)||'-'||substr(col,1,2)||' '||substr(col,12,5)||':00')",
                'julianday': "For date differences: JULIANDAY(DATE(col1)) - JULIANDAY(DATE(col2))"
            },
            'postgres': {
                'iso': "Use col::DATE or col::TIMESTAMP for date operations",
                'dd-mm-yyyy': "Convert: TO_DATE(col, 'DD-MM-YYYY')",
                'dd-mm-yyyy hh:mm': "Convert: TO_TIMESTAMP(col, 'DD-MM-YYYY HH24:MI')"
            },
            'databricks': {
                'iso': "Use TO_DATE(col) or TO_TIMESTAMP(col) for date operations",
                'dd-mm-yyyy': "Convert: TO_DATE(col, 'dd-MM-yyyy')",
                'dd-mm-yyyy hh:mm': "Convert: TO_TIMESTAMP(col, 'dd-MM-yyyy HH:mm')"
            },
            'snowflake': {
                'iso': "Use TO_DATE(col) or TO_TIMESTAMP(col) for date operations",
                'dd-mm-yyyy': "Convert: TO_DATE(col, 'DD-MM-YYYY')",
                'dd-mm-yyyy hh:mm': "Convert: TO_TIMESTAMP(col, 'DD-MM-YYYY HH24:MI')"
            },
            'mysql': {
                'iso': "Use DATE(col) or STR_TO_DATE(col, '%Y-%m-%d') for date operations",
                'dd-mm-yyyy': "Convert: STR_TO_DATE(col, '%d-%m-%Y')",
                'dd-mm-yyyy hh:mm': "Convert: STR_TO_DATE(col, '%d-%m-%Y %H:%i')"
            }
        }
        
        db_conversions = date_conversion_examples.get(db_type, date_conversion_examples['sqlite'])
        date_instructions = f"""
DATABASE: {db_type.upper()}
Date/Timestamp Conversion Rules:
  - ISO format (YYYY-MM-DD): {db_conversions['iso']}
  - DD-MM-YYYY format: {db_conversions['dd-mm-yyyy']}
  - DD-MM-YYYY HH:MM format: {db_conversions.get('dd-mm-yyyy hh:mm', db_conversions['dd-mm-yyyy'])}
  {'- JULIANDAY for differences: ' + db_conversions['julianday'] if 'julianday' in db_conversions else ''}
"""

        prompt = f"""You are a PHARMACEUTICAL COMMERCIAL ANALYTICS EXPERT analyzing database tables for SQL query generation.

Table: {table_name}
Columns (actual database types):
{col_list}
{sample_block}
{date_instructions}

Generate a JSON response with exactly TWO fields:

1. "description": A 3-4 sentence description written for a pharma commercial analyst:
   - What business entity or process this table represents (e.g., patient journey, prescriber activity, claims, call activity, market access, hub services, sales force deployment)
   - What granularity each row represents (one row per patient? per claim? per call? per territory?)
   - Primary analytics use cases this table supports (enrollment funnel, time-to-treatment, field force effectiveness, market share, adherence tracking, etc.)
   - Which columns are most critical for filtering, grouping, and aggregation — use the ACTUAL column names from the list above

2. "columns": For EVERY column listed above, write a single descriptive string that MUST include:
   a) What this column represents in pharma commercial terms (patient ID, therapy status, call date, prescriber NPI, territory, brand, etc.)
   b) How to use it in SQL: is it a filter (WHERE), a grouping dimension (GROUP BY), a count key (COUNT DISTINCT), a join key, or an aggregation measure (SUM/AVG)?
   c) **ACTUAL DATA FORMAT** observed in sample data:
      - For TEXT/VARCHAR date columns: Specify EXACT format (DD-MM-YYYY, DD-MM-YYYY HH:MM, YYYY-MM-DD, etc.)
      - For numeric columns: Specify if it's a flag (0/1), ID, amount, count, percentage
      - For text columns: Specify if it's a category, code, name, or free text
   d) **DATABASE-SPECIFIC CONVERSION** (CRITICAL):
      - If date stored as TEXT, include the EXACT conversion for {db_type.upper()}: {db_conversions.get('dd-mm-yyyy', '')}
      - If timestamp with time component, use the appropriate timestamp conversion
      - For JULIANDAY operations in SQLite on TEXT dates, MUST convert to DATE first
   e) **2-3 SAMPLE VALUES** from the actual data (MANDATORY) - include these so SQL generator knows exact values to filter on

   IMPORTANT: Even if column appears to be a date by name (e.g., enrollment_date, infusion_date), if the database type is TEXT/VARCHAR, you MUST specify the format and conversion syntax.

   Write each column description as a single plain text string — no sub-fields, no JSON nesting, just a comprehensive sentence covering all points above.

Return ONLY valid JSON. No markdown, no code fences, no commentary.
Format: {{"description": "...", "columns": {{"col_name": "description string", ...}}}}"""

        def _call_llm(cols_subset: Dict[str, str], include_table_desc: bool) -> Dict[str, Any]:
            """Call LLM for a subset of columns. Returns parsed JSON or raises."""
            col_list_subset = "\n".join([f"  - {col} ({col_db_type})" for col, col_db_type in cols_subset.items()])
            # db_type and db_conversions are already defined in outer scope
            
            subset_prompt = f"""You are a PHARMACEUTICAL COMMERCIAL ANALYTICS EXPERT analyzing database tables for SQL query generation.

Table: {table_name}
Database: {db_type.upper()}
Columns (actual database types):
{col_list_subset}
{sample_block}

Generate a JSON response with{"" if not include_table_desc else " TWO fields:"}\
{"" if include_table_desc else " ONE field:"}

{"1. " if include_table_desc else ""}\
{"" if not include_table_desc else '''"description": A 3-4 sentence description written for a pharma commercial analyst covering what this table represents, row granularity, analytics use cases, and critical columns for filtering/grouping.

2. '''}
"columns": For EVERY column listed above, write a single descriptive string that MUST include: (a) pharma commercial meaning, (b) SQL usage (WHERE/GROUP BY/COUNT DISTINCT/JOIN/SUM/AVG), (c) EXACT data format from samples (for TEXT dates: DD-MM-YYYY HH:MM, YYYY-MM-DD, etc.), (d) database-specific conversion if TEXT date (e.g., {db_conversions.get('dd-mm-yyyy', 'appropriate conversion')}), (e) 2-3 actual sample values.

Return ONLY valid JSON. No markdown, no code fences.
Format: {{{{"description": "...", "columns": {{"col_name": "description", ...}}}}}}"""

            client = OpenAI(api_key=openai_api_key)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a database analyst. Return only valid JSON."},
                    {"role": "user", "content": subset_prompt}
                ],
                temperature=0.1,
                max_tokens=4000,
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = "\n".join(l for l in raw.split("\n") if not l.strip().startswith("```")).strip()
            return json.loads(raw)

        try:
            # Try full table in one shot first
            parsed = _call_llm(columns, include_table_desc=True)
            table_desc = parsed.get("description", fallback["description"])
            llm_col_descs = parsed.get("columns", {})

            # If any columns are missing descriptions, it may have been truncated — fill gaps
            missing = {c: t for c, t in columns.items() if not llm_col_descs.get(c)}
            if missing:
                logger.info(f"  → {table_name}: {len(missing)} columns missing descriptions — retrying in chunks")
                CHUNK = 15
                missing_items = list(missing.items())
                for i in range(0, len(missing_items), CHUNK):
                    chunk = dict(missing_items[i:i + CHUNK])
                    try:
                        chunk_parsed = _call_llm(chunk, include_table_desc=False)
                        llm_col_descs.update(chunk_parsed.get("columns", {}))
                    except Exception as ce:
                        logger.warning(f"  → {table_name}: chunk {i//CHUNK+1} failed: {ce}")

            result = {
                "description": table_desc,
                "columns": {
                    col: {"type": col_type, "description": llm_col_descs.get(col, "")}
                    for col, col_type in columns.items()
                }
            }
            logger.info(f"Generated metadata for {table_name}: {len(columns)} columns")
            return result

        except Exception as e:
            logger.warning(f"Metadata generation failed for {table_name} (full): {e} — retrying in chunks")
            # Fallback: chunk the columns so large tables never fail due to token limits
            result = {"description": fallback["description"], "columns": dict(fallback["columns"])}
            try:
                # Get table description from first chunk
                col_items = list(columns.items())
                CHUNK = 15
                first_chunk = dict(col_items[:CHUNK])
                first_parsed = _call_llm(first_chunk, include_table_desc=True)
                result["description"] = first_parsed.get("description", fallback["description"])
                for col, desc in first_parsed.get("columns", {}).items():
                    result["columns"][col] = {"type": columns[col], "description": desc}
                # Remaining chunks
                for i in range(CHUNK, len(col_items), CHUNK):
                    chunk = dict(col_items[i:i + CHUNK])
                    try:
                        cp = _call_llm(chunk, include_table_desc=False)
                        for col, desc in cp.get("columns", {}).items():
                            result["columns"][col] = {"type": columns[col], "description": desc}
                    except Exception as ce2:
                        logger.warning(f"  → {table_name}: chunk {i//CHUNK+1} failed: {ce2}")
                logger.info(f"Generated metadata for {table_name} via chunking: {len(columns)} columns")
            except Exception as e2:
                logger.warning(f"Metadata generation fully failed for {table_name}: {e2}")
            return result

    # Keep old function name as alias for backward compatibility
    def _generate_column_descriptions(
        self,
        table_name: str,
        table_description: str,
        columns: Dict[str, str],
        openai_api_key: str,
        sample_rows: list = None,
    ) -> Dict[str, Dict[str, str]]:
        """
        DEPRECATED: Use _generate_table_metadata instead.
        This wrapper converts new format to old format for backward compatibility.
        """
        new_result = self._generate_table_metadata(table_name, columns, openai_api_key, sample_rows)
        
        # Convert to old format for backward compatibility
        old_format = {"__table_description__": new_result.get("description", "")}
        for col, col_info in new_result.get("columns", {}).items():
            old_format[col] = {
                "type": col_info.get("type", ""),
                "description": col_info.get("description", ""),
            }
        # No relationships from LLM - relationships are user-defined only
        return old_format

    def ensure_schema_generated(self, available_tables: List[str], openai_api_key: str) -> bool:
        """
        Called during login/onboarding BEFORE the user reaches the Edit Schema step.
        Generates schema descriptions for any tables that do not yet have one.
        Returns True if any new tables were described, False if everything was already present.

        This guarantees that when the user opens Edit Schema, all columns and tables
        already have rich pharma-commercial descriptions they can review and refine.
        Relationships are intentionally left empty — the user defines those in Edit Schema.
        """
        existing_tables = self.schema_info.get("tables", {})
        new_tables = [t for t in available_tables if t not in existing_tables]

        if not new_tables:
            logger.info(
                f"ensure_schema_generated: all {len(available_tables)} tables already described — skipping LLM"
            )
            return False

        logger.info(
            f"ensure_schema_generated: generating schema for {len(new_tables)} new tables before Edit Schema"
        )
        self.set_selected_tables(available_tables, openai_api_key)
        return True

    def set_selected_tables(self, selected_tables: List[str], openai_api_key: str = None):
        """
        Update the active table selection and extend the schema for any new tables.

        Key invariant: schema_info["tables"] is ADDITIVE — tables are never removed
        from it, regardless of what is checked/unchecked in the filter.
        selected_tables is purely a runtime visibility filter; it does not drive
        what is persisted to the YAML.

        - Existing table descriptions are always preserved as-is.
        - The LLM is called ONLY for tables not yet present in schema_info.
        - Unchecking a table in the filter keeps its schema entry intact for next time.
        """
        logger.info(f"Updating selected tables: {selected_tables}")
        self.selected_tables = selected_tables

        if openai_api_key:
            self._openai_api_key = openai_api_key

        try:
            existing_tables = self.schema_info.get("tables", {})
            existing_relationships = self.schema_info.get("relationships", [])

            # Start from the FULL existing schema — never drop any table entries
            preserved_schema: Dict[str, Any] = {
                "tables": dict(existing_tables),   # shallow copy — all tables kept
                "relationships": existing_relationships,
                "metadata": {
                    "db_type": self.db_type,
                    "selected_tables": selected_tables,
                    "generated_at": str(pd.Timestamp.now())
                }
            }

            # Helper to check if a table has empty/incomplete descriptions
            def _needs_regeneration(table_name: str) -> bool:
                table_info = existing_tables.get(table_name, {})
                # Check if table description is empty
                if not table_info.get("description") or table_info.get("description") == f"Table {table_name}":
                    return True
                # Check if ANY column has empty description
                columns = table_info.get("columns", {})
                for col_info in columns.values():
                    if isinstance(col_info, dict) and not col_info.get("description"):
                        return True
                return False

            # Process tables that have never been described OR have empty descriptions
            tables_to_fetch = [
                t for t in selected_tables 
                if t not in existing_tables or _needs_regeneration(t)
            ]

            if existing_tables:
                reused = [t for t in selected_tables if t in existing_tables and not _needs_regeneration(t)]
                needs_regen = [t for t in selected_tables if t in existing_tables and _needs_regeneration(t)]
                logger.info(
                    f"Schema preserved for {len(reused)} fully described tables, "
                    f"{len(needs_regen)} need regeneration (empty descriptions)"
                )

            # ── Fetch + LLM-describe only brand-new tables ─────────────────
            if tables_to_fetch:
                logger.info(f"Fetching schema for new tables: {tables_to_fetch}")
                results: Dict[str, Dict] = {}

                def fetch_table(table_name: str):
                    try:
                        conn = self.get_connection()
                        columns = self._get_columns_for_table(table_name, conn)

                        # Fetch a small sample (up to 5 rows) for LLM description context only.
                        # Sample values help the LLM write accurate column descriptions
                        # (flag values, date formats, category names, etc.).
                        # Sample rows are NOT stored in the schema YAML.
                        sample_rows = []
                        try:
                            if self.db_type == "databricks":
                                cursor = conn.cursor()
                                cursor.execute(f"SELECT * FROM {table_name} LIMIT 5")
                                rows = cursor.fetchall()
                                cols = [d[0] for d in cursor.description]
                                cursor.close()
                                sample_df = pd.DataFrame(rows, columns=cols)
                            elif self.db_type == "snowflake":
                                raw_conn = self._snowflake_engine.raw_connection()
                                try:
                                    cursor = raw_conn.cursor()
                                    try:
                                        cursor.execute(f"SELECT * FROM {table_name} LIMIT 5")
                                        rows = cursor.fetchall()
                                        cols = [d[0] for d in cursor.description]
                                    finally:
                                        cursor.close()
                                finally:
                                    raw_conn.close()
                                sample_df = pd.DataFrame(rows, columns=cols)
                            else:
                                sample_df = pd.read_sql_query(
                                    f"SELECT * FROM {table_name} LIMIT 5", conn
                                )
                            sample_rows = sample_df.astype(str).to_dict(orient="records")
                        except Exception as se:
                            logger.debug(f"Sample fetch skipped for {table_name}: {se}")

                        if self.db_type not in ("databricks", "snowflake"):
                            self._close_connection(conn)

                        results[table_name] = {"columns": columns, "sample_rows": sample_rows}
                        logger.info(f"  → {table_name}: {len(columns)} columns")
                    except Exception as e:
                        logger.error(f"fetch_table failed for {table_name}: {e}")
                        results[table_name] = {"columns": {}, "sample_rows": []}

                # Start threads based on db_type: sequential for slow cloud DBs, parallel for fast local DBs
                if self.db_type in ("databricks", "snowflake"):
                    # Snowflake/Databricks: Sequential execution to avoid pool exhaustion
                    logger.info(f"Using SEQUENTIAL schema fetch for {self.db_type} (avoids connection pool timeouts)")
                    for table_name in tables_to_fetch:
                        fetch_table(table_name)
                else:
                    # PostgreSQL/MySQL/SQLite/CSV: Parallel execution is safe
                    threads = [
                        threading.Thread(target=fetch_table, args=(t,))
                        for t in tables_to_fetch
                    ]
                    for t in threads:
                        t.start()
                    for t in threads:
                        t.join(timeout=60)  # Increased from 15 to 60 seconds

                _api_key = openai_api_key or getattr(self, '_openai_api_key', None)

                for table_name in tables_to_fetch:
                    r = results.get(table_name, {"columns": {}, "sample_rows": []})
                    fallback_desc = f"Table '{table_name}' from {self.db_type}"

                    enriched_columns = self._generate_column_descriptions(
                        table_name=table_name,
                        table_description=fallback_desc,
                        columns=r["columns"],
                        sample_rows=r.get("sample_rows", []),
                        openai_api_key=_api_key or ""
                    )

                    # Pop LLM-generated table-level metadata
                    table_description = enriched_columns.pop("__table_description__", fallback_desc)
                    # These keys are no longer generated by the LLM — popped defensively
                    enriched_columns.pop("__relationships__", None)
                    count_col         = enriched_columns.pop("__count_column__", "")
                    inline_val_cols   = enriched_columns.pop("__inline_value_columns__", [])
                    entity_name_cols  = enriched_columns.pop("__entity_name_columns__", [])
                    filter_hints      = enriched_columns.pop("__filter_hints__", [])
                    # Relationships are NEVER auto-generated — they are user-defined only via Edit Schema

                    preserved_schema["tables"][table_name] = {
                        "description":          table_description,
                        "columns":              enriched_columns,
                        # relationships are user-defined only — NOT stored per-table from LLM
                        "count_column":         count_col,
                        "inline_value_columns": inline_val_cols,
                        "entity_name_columns":  entity_name_cols,
                        "filter_hints":         filter_hints,
                        # sample_rows are intentionally NOT stored — used only for LLM description
                        # enriched flag prevents redundant metadata regeneration
                        "enriched":             True,
                    }
            else:
                logger.info("No new tables to describe — LLM not called")

            # ── Preserve user-defined relationships only ────────
            # Do NOT auto-detect or auto-generate relationships
            # Relationships are managed by the user via the Edit Schema UI
            # Preserve existing relationships from schema (if any)
            if "relationships" not in preserved_schema:
                preserved_schema["relationships"] = []
            
            logger.info(f"Schema relationships preserved: {len(preserved_schema['relationships'])} (user-defined only)")

            # Update in-memory schema IMMEDIATELY
            self.schema_info = preserved_schema

            # Write YAML in background — full schema including deselected tables
            def write_yaml():
                try:
                    self._save_schema_to_yaml(preserved_schema)
                except Exception as e:
                    logger.error(f"Background YAML write failed: {e}")

            threading.Thread(target=write_yaml, daemon=True).start()
            logger.info(
                f"Schema updated — total stored: {len(preserved_schema['tables'])}, "
                f"newly described: {len(tables_to_fetch)}, "
                f"active: {len(selected_tables)}"
            )

        except Exception as e:
            logger.error(f"set_selected_tables failed: {e}", exc_info=True)
            raise

    def get_schema_context(self) -> str:
        """
        Build LLM prompt context — active tables only.
        Surfaces per-column role flags, filter_hints, and sample rows
        so the SQL generator applies correct WHERE conditions proactively.
        """
        context = f"Database Type: {self.db_type.upper()}\n\n"

        all_tables = self.schema_info.get("tables", {})
        active_names = self.selected_tables if self.selected_tables is not None else list(all_tables.keys())
        active_tables = {n: all_tables[n] for n in active_names if n in all_tables}

        if not active_tables:
            return "No tables selected. Please connect a data source and select tables first."

        for tname, tinfo in active_tables.items():
            context += f"Table: {tname}\n"
            context += f"  Description: {tinfo.get('description', '')}\n"

            count_col = tinfo.get("count_column", "")
            if count_col:
                context += f"  ► COUNT COLUMN (use in COUNT() for 'how many'): {count_col}\n"

            inline_cols = tinfo.get("inline_value_columns", [])
            if inline_cols:
                context += f"  ⚠ INLINE VALUE COLUMNS (filter directly, NO extra JOIN): {', '.join(inline_cols)}\n"

            entity_cols = tinfo.get("entity_name_columns", [])
            if entity_cols:
                context += f"  ★ ENTITY NAME COLUMNS (always SELECT with ID): {', '.join(entity_cols)}\n"

            filter_hints = tinfo.get("filter_hints", [])
            if filter_hints:
                context += "  ✦ FILTER HINTS (apply ONLY for count/status queries — NOT for AVG/SUM/comparison/distribution):\n"
                for hint in filter_hints:
                    context += f"      - {hint}\n"

            context += "  Columns:\n"
            for col, col_info in tinfo.get("columns", {}).items():
                if isinstance(col_info, dict):
                    dtype  = col_info.get("type", "TEXT")
                    desc   = col_info.get("description", "")
                    flags  = []
                    if col_info.get("is_row_pk"):       flags.append("ROW_PK — NOT for COUNT")
                    if col_info.get("is_entity_id"):    flags.append("ENTITY_ID — use for COUNT")
                    if col_info.get("is_entity_name"):  flags.append("ENTITY_NAME — always SELECT with ID")
                    if col_info.get("is_inline_value"): flags.append("INLINE_VALUE — filter directly")
                    if col_info.get("is_flag"):         flags.append("FLAG — see description for values")
                    if col_info.get("is_fk"):           flags.append("FK")
                    flag_str = f" [{', '.join(flags)}]" if flags else ""
                    line = f"    - {col} ({dtype}){flag_str}"
                    if desc:
                        line += f": {desc}"
                    context += line + "\n"
                else:
                    context += f"    - {col} ({col_info})\n"

            context += "\n"

        rels = self.schema_info.get("relationships", [])
        if rels:
            context += "JOIN PATHS (FK relationships — use these to reach columns in related tables):\n"
            for r in rels:
                context += f"  - {r}\n"
            context += "\n"

        context += (
            "STRICT RULES:\n"
            "1. Use ONLY tables and columns listed above — no invented column names\n"
            "2. COUNT questions: use ► COUNT COLUMN (ENTITY_ID), never [ROW_PK]\n"
            "3. [INLINE_VALUE] / [FLAG] columns: filter directly — no join needed\n"
            "4. [ENTITY_NAME] columns: always SELECT alongside the ID column\n"
            "5. Apply ✦ FILTER HINTS ONLY for count/status queries — omit for AVG/SUM/comparison queries\n"
            "6. No GROUP BY unless question explicitly requests a breakdown\n"
            f"7. Available tables: {', '.join(active_tables.keys())}\n"
        )
        return context

    # ── Sample Data ────────────────────────────────────────────────────────

    def get_sample_data(self, limit: int = 5) -> Dict[str, Any]:
        """Fetch sample rows from each selected table."""
        try:
            sample_data = {}
            conn = self.get_connection()
            tables = (
                self.selected_tables
                if self.selected_tables
                else self.get_all_tables_unfiltered()
            )
            for table_name in tables:
                try:
                    if self.db_type in ("databricks", "snowflake"):
                        # Use raw DBAPI cursor — pd.read_sql_query hits "ordinal must be >= 1"
                        # on Snowflake tables with VARIANT/OBJECT/ARRAY columns via SELECT *.
                        # For Snowflake: use engine.raw_connection() — stable in SQLAlchemy 1.4 & 2.x.
                        if self.db_type == "snowflake":
                            raw_conn = self._snowflake_engine.raw_connection()
                            try:
                                cursor = raw_conn.cursor()
                                try:
                                    cursor.execute(f"SELECT * FROM {table_name} LIMIT {limit}")
                                    rows = cursor.fetchall()
                                    cols = [desc[0] for desc in cursor.description]
                                finally:
                                    cursor.close()
                            finally:
                                raw_conn.close()
                        else:
                            cursor = conn.cursor()
                            cursor.execute(f"SELECT * FROM {table_name} LIMIT {limit}")
                            rows = cursor.fetchall()
                            cols = [desc[0] for desc in cursor.description]
                            cursor.close()
                        df = pd.DataFrame(rows, columns=cols)
                    else:
                        df = pd.read_sql_query(
                            f"SELECT * FROM {table_name} LIMIT {limit}", conn
                        )
                    sample_data[table_name] = df.to_dict(orient='records')
                except Exception as e:
                    logger.warning(f"Could not fetch sample from {table_name}: {e}")
                    sample_data[table_name] = []
            self._close_connection(conn)
            return sample_data
        except Exception as e:
            logger.error(f"get_sample_data failed: {e}")
            return {}

    def reload_schema(self) -> Dict[str, Any]:
        """Reload this source's tables from the global schema YAML."""
        self.schema_info = self._load_schema()
        self.invalidate_table_cache()
        logger.info(
            f"Schema reloaded from global YAML. "
            f"{self.db_type} tables: {list(self.schema_info.get('tables', {}).keys())}"
        )
        return self.schema_info

    def apply_schema_edits(self, edits: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply manual schema edits in-place — NO LLM involved.

        Expected `edits` structure (all keys optional — only changed fields needed):
        {
            "tables": {
                "<table_name>": {
                    "description": "...",          # optional — only if changed
                    "columns": {
                        "<col_name>": {
                            "description": "..."   # optional — only if changed
                        }
                    }
                }
            },
            "relationships": ["...", "..."]        # optional — full replacement list
        }

        Only the fields present in `edits` are touched; everything else is preserved.
        Saves to YAML in a background thread after applying.
        Returns the updated schema_info.
        """
        if not edits:
            logger.info("apply_schema_edits called with empty edits — no-op")
            return self.schema_info

        schema = self.schema_info  # mutate in-place
        changed = False

        # ── Table-level edits ──────────────────────────────────────────────
        for table_name, table_edits in edits.get("tables", {}).items():
            if table_name not in schema.get("tables", {}):
                logger.warning(f"apply_schema_edits: unknown table '{table_name}' — skipped")
                continue

            table_schema = schema["tables"][table_name]

            # Table description override.
            # Guard: never overwrite a non-empty LLM-generated description with an
            # empty string from the frontend. An empty payload value means the user
            # hasn't typed anything yet (e.g. Edit Schema opened before the background
            # LLM call finished) — it does NOT mean 'erase this field'.
            if "description" in table_edits:
                new_desc = table_edits["description"]
                existing_desc = table_schema.get("description", "")
                if new_desc == "" and existing_desc:
                    pass  # keep existing — blank payload is a no-op
                elif existing_desc != new_desc:
                    table_schema["description"] = new_desc
                    changed = True
                    logger.info(f"Updated description for table '{table_name}'")

            # Column description overrides
            for col_name, col_edits in table_edits.get("columns", {}).items():
                if col_name not in table_schema.get("columns", {}):
                    logger.warning(
                        f"apply_schema_edits: unknown column '{col_name}' "
                        f"in table '{table_name}' — skipped"
                    )
                    continue

                col_schema = table_schema["columns"][col_name]

                # Upgrade legacy format (plain string) to dict in-place
                if not isinstance(col_schema, dict):
                    table_schema["columns"][col_name] = {"type": col_schema, "description": ""}
                    col_schema = table_schema["columns"][col_name]
                    changed = True

                for field in ("description", "type", "is_primary_key", "is_foreign_key"):
                    if field in col_edits:
                        incoming = col_edits[field]
                        existing = col_schema.get(field)
                        # Same guard for column descriptions: don't blank out existing content
                        if field == "description" and incoming == "" and existing:
                            continue
                        if existing != incoming:
                            col_schema[field] = incoming
                            changed = True
                            logger.info(f"Updated {field} for column '{table_name}.{col_name}'")

        # ── Relationship edits (full replacement if provided) ──────────────
        if "relationships" in edits:
            new_rels = edits["relationships"]
            if schema.get("relationships") != new_rels:
                schema["relationships"] = new_rels
                changed = True
                logger.info(f"Updated relationships: {len(new_rels)} entries")

        if not changed:
            logger.info("apply_schema_edits: no actual changes detected — skipping save")
            return self.schema_info

        # Persist changes in background — deepcopy AFTER all mutations are applied
        import copy
        snapshot = copy.deepcopy(self.schema_info)

        def write_yaml():
            try:
                self._save_schema_to_yaml(snapshot)
                logger.info(
                    f"apply_schema_edits: YAML updated — "                    f"tables: {list(snapshot.get('tables', {}).keys())}, "                    f"relationships: {snapshot.get('relationships', [])}"
                )
            except Exception as e:
                logger.error(f"apply_schema_edits background YAML write failed: {e}")

        threading.Thread(target=write_yaml, daemon=True).start()
        logger.info(
            f"apply_schema_edits: {sum(1 for t in schema.get('tables',{}) if t in edits.get('tables',{}))} table(s) edited, "            f"relationships updated: {'relationships' in edits}"
        )
        return self.schema_info

    # ── Enhanced Schema with Business Metadata ─────────────────────────────

    def enhance_column_metadata(
        self,
        table_name: str,
        column_name: str,
        business_description: Optional[str] = None,
        example_values: Optional[List[str]] = None,
        business_rules: Optional[List[str]] = None,
        common_queries: Optional[List[str]] = None
    ) -> bool:
        """
        Enhance a column with rich business metadata
        
        Args:
            table_name: Name of the table
            column_name: Name of the column
            business_description: User-friendly business explanation
            example_values: Sample values to clarify usage
            business_rules: Business rules, constraints, or calculations
            common_queries: Common query patterns this column appears in
        
        Returns:
            True if successful, False otherwise
        """
        if table_name not in self.schema_info.get("tables", {}):
            logger.warning(f"Table '{table_name}' not found in schema")
            return False
        
        table_schema = self.schema_info["tables"][table_name]
        
        if column_name not in table_schema.get("columns", {}):
            logger.warning(f"Column '{column_name}' not found in table '{table_name}'")
            return False
        
        col_schema = table_schema["columns"][column_name]
        
        # Upgrade legacy format if needed
        if not isinstance(col_schema, dict):
            col_schema = {"type": col_schema, "description": ""}
            table_schema["columns"][column_name] = col_schema
        
        # Add business metadata fields
        if business_description is not None:
            col_schema["business_description"] = business_description
        
        if example_values is not None:
            col_schema["example_values"] = example_values
        
        if business_rules is not None:
            col_schema["business_rules"] = business_rules
        
        if common_queries is not None:
            col_schema["common_queries"] = common_queries
        
        # Save to YAML
        import copy
        snapshot = copy.deepcopy(self.schema_info)
        
        def write_yaml():
            try:
                self._save_schema_to_yaml(snapshot)
                logger.info(f"Enhanced metadata saved for {table_name}.{column_name}")
            except Exception as e:
                logger.error(f"Failed to save enhanced metadata: {e}")
        
        threading.Thread(target=write_yaml, daemon=True).start()
        
        logger.info(f"Enhanced metadata for column: {table_name}.{column_name}")
        return True

    def get_enhanced_schema_context(self) -> str:
        """
        Build enhanced schema context including business metadata
        Extends the base schema context with business descriptions, examples, and rules
        """
        context = self.get_schema_context()
        
        # Add enhanced metadata section
        all_tables = self.schema_info.get("tables", {})
        active_names = self.selected_tables if self.selected_tables is not None else list(all_tables.keys())
        active_tables = {n: all_tables[n] for n in active_names if n in all_tables}
        
        has_enhanced = False
        enhanced_section = "\n\n═══════════════════════════════════════════════════════════════\n"
        enhanced_section += "ENHANCED BUSINESS METADATA\n"
        enhanced_section += "═══════════════════════════════════════════════════════════════\n\n"
        
        for tname, tinfo in active_tables.items():
            table_has_enhanced = False
            table_section = f"📊 Table: {tname}\n"
            
            for col_name, col_info in tinfo.get("columns", {}).items():
                if not isinstance(col_info, dict):
                    continue
                
                col_enhanced = []
                
                if "business_description" in col_info and col_info["business_description"]:
                    col_enhanced.append(f"  Business Context: {col_info['business_description']}")
                
                if "example_values" in col_info and col_info["example_values"]:
                    examples = ", ".join(str(v) for v in col_info["example_values"][:5])
                    col_enhanced.append(f"  Example Values: {examples}")
                
                if "business_rules" in col_info and col_info["business_rules"]:
                    col_enhanced.append(f"  Business Rules:")
                    for rule in col_info["business_rules"]:
                        col_enhanced.append(f"    • {rule}")
                
                if "common_queries" in col_info and col_info["common_queries"]:
                    col_enhanced.append(f"  Common Usage:")
                    for query in col_info["common_queries"]:
                        col_enhanced.append(f"    • {query}")
                
                if col_enhanced:
                    table_has_enhanced = True
                    table_section += f"\n  Column: {col_name}\n"
                    table_section += "\n".join(col_enhanced) + "\n"
            
            if table_has_enhanced:
                has_enhanced = True
                enhanced_section += table_section + "\n"
        
        if has_enhanced:
            context += enhanced_section
        
        return context

    def bulk_enhance_from_dict(self, enhancements: Dict[str, Any]) -> Dict[str, int]:
        """
        Bulk enhance schema from a dictionary of enhancements
        
        Expected format:
        {
            "table_name": {
                "column_name": {
                    "business_description": "...",
                    "example_values": ["val1", "val2"],
                    "business_rules": ["rule1", "rule2"],
                    "common_queries": ["pattern1", "pattern2"]
                }
            }
        }
        
        Returns:
            Dict with counts of successful and failed enhancements
        """
        results = {"success": 0, "failed": 0, "skipped": 0}
        
        for table_name, columns in enhancements.items():
            if table_name not in self.schema_info.get("tables", {}):
                logger.warning(f"Table '{table_name}' not found, skipping")
                results["skipped"] += len(columns)
                continue
            
            for column_name, metadata in columns.items():
                success = self.enhance_column_metadata(
                    table_name=table_name,
                    column_name=column_name,
                    business_description=metadata.get("business_description"),
                    example_values=metadata.get("example_values"),
                    business_rules=metadata.get("business_rules"),
                    common_queries=metadata.get("common_queries")
                )
                
                if success:
                    results["success"] += 1
                else:
                    results["failed"] += 1
        
        logger.info(f"Bulk enhancement complete: {results}")
        return results

    # ── CSV Support ────────────────────────────────────────────────────────

    @classmethod
    def from_csv(cls, csv_path: str, schema_alias: str = None, original_filename: str = None) -> "DatabaseManager":
        """Load CSV into temp SQLite DB and auto-build schema."""
        df = pd.read_csv(csv_path)

        # Deterministic path derived from csv_path (NOT tempfile.mkstemp).
        # Using a fresh random temp file on every call meant every re-upload,
        # SharePoint reconnect, or server restart/hydration cycle minted a
        # brand-new tmpXXXXX.db that nothing ever cleaned up — cleanup() only
        # ever ran on the *current* manager when a source was explicitly
        # disconnected, never on whatever stale .db file preceded it. Deriving
        # the path from csv_path makes this idempotent: the same csv always
        # maps to the same .db path, so re-loading it just reconnects to and
        # overwrites (if_exists="replace") that one file instead of minting
        # another orphan.
        tmp_db_path = f"{csv_path}.sqlite.db"

        conn = sqlite3.connect(tmp_db_path)

        # Use original filename if provided, otherwise fall back to temp file name
        if original_filename:
            base_name = os.path.splitext(os.path.basename(original_filename))[0]
        else:
            base_name = os.path.splitext(os.path.basename(csv_path))[0]
        
        table_name = re.sub(r'[^a-zA-Z0-9_]', '_', base_name)
        if table_name and table_name[0].isdigit():
            table_name = f"table_{table_name}"
        if not table_name:
            table_name = "uploaded_data"

        df.to_sql(table_name, conn, if_exists="replace", index=False)
        conn.close()

        # Use db_type="csv" for CSV uploads, not "sqlite"
        manager = cls(db_path=tmp_db_path, db_type="csv", schema_alias=schema_alias)
        # Store the original csv_path so cleanup() can delete it alongside the temp .db
        manager._source_csv_path = csv_path
        # Don't call set_selected_tables here - let the caller do it with proper API key
        # so that schema generation happens with LLM descriptions

        logger.info(f"CSV loaded: '{table_name}' ({len(df)} rows, {len(df.columns)} cols)")
        return manager

    def purge_from_yaml(self, table_names: Optional[List[str]] = None):
        """
        Remove this source's entries from the global schema YAML.

        Args:
            table_names: Specific table names to remove (bare names, no prefix).
                         If None, ALL keys matching this source's db_type prefix
                         are removed — use with care for shared db_types like
                         'sqlite' or 'csv' where multiple sources share the prefix.
                         For CSV sources, always pass the explicit table_names so
                         only THIS file's tables are purged, not other CSV uploads.
        """
        try:
            with self._global_schema_lock():
                global_data = self._read_global_yaml()

                if table_names is not None:
                    # Remove only the specified tables (safe for shared db_type namespaces)
                    for tname in table_names:
                        key = self._table_key(tname)
                        global_data.pop(key, None)
                else:
                    # Remove ALL keys for this db_type (only safe for unique db_types)
                    prefix = f"{self.db_type}."
                    keys_to_remove = [k for k in global_data if k.startswith(prefix)]
                    for k in keys_to_remove:
                        global_data.pop(k, None)

                # Remove relationships belonging to purged tables
                prefix = f"{self.db_type}."
                surviving_rels = []
                for rel in global_data.get("relationships", []):
                    if table_names is not None:
                        # Keep if rel doesn't reference any of the purged tables
                        purged_keys = {self._table_key(t) for t in table_names}
                        if not any(rel.startswith(k) or f" {k}" in rel for k in purged_keys):
                            surviving_rels.append(rel)
                    else:
                        if not rel.startswith(prefix):
                            surviving_rels.append(rel)
                global_data["relationships"] = surviving_rels

                self._write_global_yaml(global_data)
            logger.info(
                f"Purged schema entries for {self.db_type} "
                f"tables={table_names or 'ALL'} from global YAML"
            )
        except Exception as e:
            logger.error(f"purge_from_yaml failed: {e}")

    def cleanup(self):
        """
        Release all resources held by this manager.

        For CSV sources this deletes the temporary SQLite .db file that was
        created from the uploaded CSV.  For pooled sources (PostgreSQL, MySQL,
        Snowflake) the SQLAlchemy engine is disposed so connections are returned
        to the OS.  For Databricks the cached connection is closed.
        """
        try:
            if self.db_type == "csv":
                # Delete the temp SQLite .db file
                if self.db_path and os.path.exists(self.db_path):
                    os.remove(self.db_path)
                    logger.info(f"Deleted temp CSV database: {self.db_path}")
                # Delete the original CSV/Excel source file (schema is intentionally kept)
                source_csv = getattr(self, "_source_csv_path", None)
                if source_csv and os.path.exists(source_csv):
                    os.remove(source_csv)
                    logger.info(f"Deleted source CSV file: {source_csv}")

            elif self.db_type in ("postgresql", "mysql"):
                engine_attr = f"_{self.db_type.split('+')[0]}_engine"  # _pg_engine / _mysql_engine
                # Use the actual attribute names set in get_connection()
                for attr in ("_pg_engine", "_mysql_engine"):
                    engine = getattr(self, attr, None)
                    if engine is not None:
                        try:
                            engine.dispose()
                        except Exception:
                            pass

            elif self.db_type == "snowflake":
                engine = getattr(self, "_snowflake_engine", None)
                if engine is not None:
                    try:
                        engine.dispose()
                    except Exception:
                        pass

            elif self.db_type == "databricks":
                conn = getattr(self, "_databricks_conn", None)
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    self._databricks_conn = None

        except Exception as e:
            logger.error(f"cleanup() failed for {self.db_type}: {e}")