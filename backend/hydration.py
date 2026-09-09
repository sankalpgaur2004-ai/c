# hydration.py
# On server startup, restore each user's data sources from the DB
# so they don't have to reconnect after a server restart.

import logging
from typing import Dict

logger = logging.getLogger(__name__)


async def hydrate_user(user_id: str, notebook_id: str = None):
    """Load a single user's saved data sources into memory.
    If notebook_id is given, only load sources for that project."""
    rows = _get_all_saved_sources(user_id, notebook_id=notebook_id)
    if not rows:
        return
    from datasources import _get_nb_sources, _get_nb_engine
    count_ok = 0
    for row in rows:
        try:
            nb_id = row.get("notebook_id")
            manager = _create_manager(row["db_type"], row["alias"], row["connection_config"], user_id)
            if manager is None:
                continue
            sources = _get_nb_sources(user_id, nb_id)
            engine  = _get_nb_engine(user_id, nb_id)
            sources[row["alias"]] = manager
            engine.add_source(row["alias"], manager)
            if row["selected_tables"]:
                manager.selected_tables = row["selected_tables"]
            count_ok += 1
            logger.info("Login hydration [%s] '%s' (%s) nb=%s", user_id[:8], row["alias"], row["db_type"], nb_id)
        except Exception as e:
            logger.warning("Login hydration failed [%s] '%s': %s", user_id[:8], row["alias"], e)
    logger.info("Login hydration complete for [%s]: %d sources", user_id[:8], count_ok)


async def hydrate_all_users():
    """
    Called once at startup. Reads every saved data source from user_store
    and re-creates the in-memory DatabaseManager + FederatedQueryEngine
    for each user. Errors on individual sources are logged but don't
    prevent other sources from loading.
    """
    import user_store
    from datasources import _get_nb_sources, _get_nb_engine
    from federated_query_engine import FederatedQueryEngine

    all_sources = _get_all_saved_sources()
    if not all_sources:
        logger.info("Hydration: no saved data sources found")
        return

    count_ok = 0
    count_fail = 0

    for row in all_sources:
        user_id  = row["user_id"]
        alias    = row["alias"]
        db_type  = row["db_type"]
        config   = row["connection_config"]
        selected = row["selected_tables"]
        nb_id    = row.get("notebook_id")

        try:
            manager = _create_manager(db_type, alias, config, user_id)
            if manager is None:
                continue

            # Use notebook-scoped in-memory store
            sources = _get_nb_sources(user_id, nb_id)
            engine  = _get_nb_engine(user_id, nb_id)
            sources[alias] = manager
            engine.add_source(alias, manager)

            # Restore selected tables (no LLM — schema already in YAML)
            if selected:
                manager.selected_tables = selected

            count_ok += 1
            logger.info("Hydrated [%s] source '%s' (%s) nb=%s", user_id[:8], alias, db_type, nb_id)

        except Exception as e:
            count_fail += 1
            logger.warning("Hydration failed [%s] '%s': %s", user_id[:8], alias, e)

    logger.info("Hydration complete — %d ok, %d failed", count_ok, count_fail)


def _get_all_saved_sources(user_id: str = None, notebook_id: str = None):
    try:
        from sqlite3 import connect
        from pathlib import Path
        import json

        db_path = Path(__file__).resolve().parent / "data" / "users.db"
        if not db_path.exists():
            return []

        c = connect(str(db_path))
        c.row_factory = lambda cur, row: {
            col[0]: row[i] for i, col in enumerate(cur.description)
        }
        if user_id and notebook_id:
            rows = c.execute(
                "SELECT user_id, alias, db_type, connection_config, selected_tables, notebook_id "
                "FROM user_data_sources WHERE user_id = ? AND notebook_id = ? ORDER BY created_at",
                (user_id, notebook_id)
            ).fetchall()
        elif user_id:
            rows = c.execute(
                "SELECT user_id, alias, db_type, connection_config, selected_tables, notebook_id "
                "FROM user_data_sources WHERE user_id = ? ORDER BY created_at",
                (user_id,)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT user_id, alias, db_type, connection_config, selected_tables, notebook_id "
                "FROM user_data_sources ORDER BY created_at"
            ).fetchall()
        c.close()

        for r in rows:
            r["connection_config"] = json.loads(r["connection_config"])
            r["selected_tables"]   = json.loads(r["selected_tables"])
        return rows
    except Exception as e:
        logger.error("Could not read saved sources: %s", e)
        return []


def _create_manager(db_type: str, alias: str, config: Dict, user_id: str):
    from database_manager import DatabaseManager

    schema_alias = f"{user_id[:8]}_{alias}"

    try:
        if db_type == "sqlite":
            return DatabaseManager(
                db_path=config.get("db_path", ""),
                db_type="sqlite",
                schema_alias=schema_alias,
            )
        elif db_type in ("mysql", "postgresql"):
            return DatabaseManager(
                db_type=db_type,
                connection_config=config,
                schema_alias=schema_alias,
            )
        elif db_type == "databricks":
            return DatabaseManager(
                db_type="databricks",
                connection_config=config,
                schema_alias=schema_alias,
            )
        elif db_type == "snowflake":
            return DatabaseManager(
                db_type="snowflake",
                connection_config=config,
                schema_alias=schema_alias,
            )
        elif db_type == "csv":
            csv_path = config.get("csv_path")
            if not csv_path:
                logger.warning("CSV source '%s' has no csv_path — skipping", alias)
                return None
            return DatabaseManager.from_csv(
                csv_path=csv_path,
                schema_alias=schema_alias,
                original_filename=config.get("original_filename", alias + ".csv"),
            )
        else:
            logger.warning("Unknown db_type '%s' — skipping", db_type)
            return None
    except Exception as e:
        logger.warning("Could not create manager for '%s': %s", alias, e)
        return None