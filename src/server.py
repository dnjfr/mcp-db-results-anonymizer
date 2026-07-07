"""MCP (Model Context Protocol) server for automatic database result anonymization.

Main server entry point. Configures SQL/NoSQL connectors, initializes the
anonymization pipeline and exposes MCP tools (listTables, describeTable,
querySql, queryNosql, queryNosqlAggregate, generateTestFixtures).
"""

import atexit
import logging
import os
import threading
import time
from contextlib import asynccontextmanager

import uvicorn
from starlette.applications import Starlette
from mcp.server.fastmcp import FastMCP

from src.security.auth import BearerAuthMiddleware
from src.security.rate_limiter import RateLimitMiddleware

from src.anonymizer.pipeline import AnonymizationPipeline
from src.anonymizer.pseudonymizer import generate_session_salt
from src.config import load_config
from src.database.nosql_connector import NoSQLConnector
from src.database.sql_connector import BaseSQLConnector, PostgreSQLConnector
from src.database.mysql_connector import MySQLConnector
from src.database.mssql_connector import MSSQLConnector
from src.detection.registry import PIIRegistry
from src.storage.mapping_store import MappingStore
from src.tools import metadata as meta_tools
from src.tools import query_nosql as nosql_tools
from src.tools import fixtures as fixture_tools
from src.tools import query_sql as sql_tools

logger = logging.getLogger("mcp.server")

_transport = os.environ.get("MCP_TRANSPORT", "stdio")
_mcp_host = os.environ.get("MCP_HOST", "127.0.0.1")
_mcp_port = int(os.environ.get("MCP_PORT", "8080"))


def _build_instructions(mode: str, ttl_minutes: int) -> str:
    """Build the MCP server instruction text based on anonymization mode.

    Args:
        mode: Anonymization mode ('ephemeral' or 'session').
        ttl_minutes: TTL in minutes for session mode mappings.

    Returns:
        Instruction string describing the server behavior to the LLM.
    """
    text = (
        "Anonymizing proxy for SQL and MongoDB databases. "
        "Results are pseudonymized before being returned - the agent never sees real PII.\n\n"
        "Workflow: listTables → describeTable → querySql / queryNosql / queryNosqlAggregate.\n"
        "Read-only, fail-closed if PII detection fails.\n"
    )
    if mode == "ephemeral":
        text += "Pseudonyms are not consistent across queries (purged after each call).\n"
    else:
        text += (
            f"Pseudonyms are consistent within the session (TTL {ttl_minutes} min). "
            "Call purgeMappings() when the analysis is done.\n"
        )
    return text


mcp = FastMCP(
    "mcp-db-results-anonymizer",
    host=_mcp_host,
    port=_mcp_port,
    instructions=_build_instructions("ephemeral", 30),
)

_SQL_CONNECTOR_TYPES: dict[str, type[BaseSQLConnector]] = {
    "postgresql": PostgreSQLConnector,
    "mysql": MySQLConnector,
    "mssql": MSSQLConnector,
}

_config: dict = {}
_sql_connectors: dict[str, BaseSQLConnector] = {}
_nosql_connectors: dict[str, NoSQLConnector] = {}
_registry = PIIRegistry()
_store: MappingStore | None = None
_pipeline: AnonymizationPipeline | None = None
_session_id: str | None = None
_mode: str = "ephemeral"
_ttl_minutes: int = 30
_ttl_timer: threading.Timer | None = None
_ttl_lock = threading.Lock()


def _cleanup():
    """Clean up resources on server shutdown."""
    global _ttl_timer
    with _ttl_lock:
        if _ttl_timer is not None:
            _ttl_timer.cancel()
            _ttl_timer = None
    if _store:
        _store.purge()
        _store.close()


def _post_query_cleanup():
    """Purge mappings based on the configured mode (called after each query)."""
    global _ttl_timer
    if _mode == "ephemeral":
        if _store and _session_id:
            _store.clear_session(_session_id)
    elif _mode == "session":
        with _ttl_lock:
            if _ttl_timer is not None:
                _ttl_timer.cancel()
            _ttl_timer = threading.Timer(_ttl_minutes * 60, _purge_on_ttl)
            _ttl_timer.daemon = True
            _ttl_timer.start()


def _purge_on_ttl():
    """TTL callback: purge mappings after inactivity."""
    if _store and _session_id:
        _store.clear_session(_session_id)
        logger.info("TTL expiré (%d min) - mappings de session purgés", _ttl_minutes)


def _init():
    """Initialize the server: load config, create store, pipeline and connectors.

    Configures SQL and NoSQL connectors based on databases declared in config.yaml,
    registers the atexit cleanup hook. Connectors are always created even if
    the database is temporarily unreachable (graceful degradation) - a warning is logged
    and the connection will be attempted on the first query.

    Returns:
        None
    """
    global _config, _store, _pipeline, _session_id, _mode, _ttl_minutes

    _config = load_config()

    storage_cfg = _config.get("storage", {})
    _mode = os.environ.get("ANONYMIZER_MODE", storage_cfg.get("mode", "ephemeral"))
    if _mode not in ("ephemeral", "session"):
        raise ValueError(f"ANONYMIZER_MODE invalide : '{_mode}' (attendu: 'ephemeral' ou 'session')")
    _ttl_minutes = int(os.environ.get("ANONYMIZER_TTL_MINUTES", str(storage_cfg.get("ttl_minutes", 30))))

    db_path = storage_cfg.get("path", ".db-anonymized/mappings.db")
    _store = MappingStore(db_path)
    _store.purge()

    mcp._mcp_server.instructions = _build_instructions(_mode, _ttl_minutes)
    logger.info("Mode mapping : %s%s", _mode, f" (TTL: {_ttl_minutes} min)" if _mode == "session" else "")

    if _mode == "session":
        mcp.tool(
            name="purgeMappings",
            title="Purge pseudonymization mappings for this session",
            description=(
                "Purges all pseudonymization mappings for the current session. "
                "Call this when your analysis is complete to immediately delete "
                "real-value-to-pseudonym associations. Only available in session mode."
            ),
        )(_purge_mappings)

    salt = generate_session_salt()
    _session_id = salt[:16]
    locale = _config.get("anonymization", {}).get("locale", "fr_FR")
    _pipeline = AnonymizationPipeline(_registry, _store, salt, locale)

    atexit.register(_cleanup)

    for db_id, db_conf in _config.get("databases", {}).items():
        db_type = db_conf.get("type", "")
        connector_cls = _SQL_CONNECTOR_TYPES.get(db_type)
        if connector_cls:
            try:
                connector = connector_cls(db_conf)
                with connector.engine.connect():
                    pass
                logger.info("Connecteur SQL initialisé | database=%s | type=%s", db_id, db_type)
            except Exception as e:
                logger.warning(
                    "Base '%s' (%s) injoignable au démarrage - "
                    "elle sera disponible dès que la connexion sera possible : %s",
                    db_id, db_type, e,
                )
                connector = connector_cls(db_conf)
            _sql_connectors[db_id] = connector
        elif db_type == "mongodb":
            connector = NoSQLConnector(db_conf)
            try:
                connector.db.command("ping")
                logger.info("Connecteur NoSQL initialisé | database=%s", db_id)
            except Exception as e:
                logger.warning(
                    "Base '%s' (mongodb) injoignable au démarrage - "
                    "elle sera disponible dès que la connexion sera possible : %s",
                    db_id, e,
                )
            _nosql_connectors[db_id] = connector

    available = list(_sql_connectors.keys()) + list(_nosql_connectors.keys())
    if not available:
        logger.error("Aucune base de données disponible - le serveur démarrera mais aucun outil ne fonctionnera")
    else:
        logger.info("Bases disponibles : %s", ", ".join(available))

def _get_patterns() -> dict[str, list[str]]:
    """Retrieve PII column detection patterns from configuration.

    Returns:
        Dict {PII_type: [glob_patterns]} (e.g. {'EMAIL': ['*email*']}).
    """
    return _config.get("detection", {}).get("column_patterns", {})


def _get_overrides() -> dict[str, str]:
    """Retrieve manual PII detection overrides from configuration.

    Returns:
        Dict {column_name: PII_type} for forced classifications.
    """
    return _config.get("detection", {}).get("overrides", {})


def _get_sensitivity() -> dict | None:
    """Retrieve sensitivity configuration from config.

    Returns:
        Sensitivity dict {level: [patterns]} or None if not configured.
    """
    return _config.get("detection", {}).get("sensitivity")


def _get_table_context() -> dict | None:
    """Retrieve table-context PII detection rules from config.

    Returns:
        Dict {PII_type: {tables: [...], columns: [...]}} or None.
    """
    return _config.get("detection", {}).get("table_context")


def _get_max_rows() -> int:
    """Retrieve the maximum number of rows allowed per query.

    Returns:
        Integer max_rows from security configuration (default: 1000).
    """
    return _config.get("security", {}).get("max_rows", 1000)


def _resolve_db(database: str) -> tuple[str, BaseSQLConnector | None, NoSQLConnector | None]:
    """Resolve a database identifier to its connector.

    Args:
        database: Database identifier (e.g. 'pagila', 'ecommerce').

    Returns:
        Tuple (db_id, sql_connector_or_None, nosql_connector_or_None).

    Raises:
        ValueError: If the identifier does not match any configured database.
    """
    if database in _sql_connectors:
        return database, _sql_connectors[database], None
    if database in _nosql_connectors:
        return database, None, _nosql_connectors[database]
    available = list(_sql_connectors.keys()) + list(_nosql_connectors.keys())
    raise ValueError(f"Base '{database}' inconnue. Disponibles: {available}")


def _clamp_limit(limit: int) -> int:
    """Clamp the limit parameter between 1 and max_rows.

    Single validation point for limits received from the LLM.
    Prevents negative, zero or excessive values.

    Args:
        limit: Raw limit parameter value from the client.

    Returns:
        The value clamped between 1 and max_rows (default: 1000).
    """
    return max(1, min(limit, _get_max_rows()))


def _purge_mappings() -> dict:
    """Purge pseudonymization mappings for the current session.
    Call this tool when your analysis is complete to immediately
    delete real value to pseudonym associations.

    Returns:
        Dict with 'status' and 'message' keys confirming the purge.
    """
    global _ttl_timer
    with _ttl_lock:
        if _ttl_timer is not None:
            _ttl_timer.cancel()
            _ttl_timer = None
    if _store and _session_id:
        _store.clear_session(_session_id)
        logger.info("purgeMappings | mappings de session purgés manuellement")
    return {"status": "ok", "message": "Session mappings purged."}


# --- MCP Tools ---

@mcp.tool(
    name="listTables",
    title="List database tables or collections [read-only]",
    description=(
        "Lists all tables (SQL) or collections (MongoDB) available in the specified database. "
        "Use this as the first step to discover what data is available before querying.\n\n"
        "Example: listTables('pagila') → returns the list of tables in the Pagila database."
    ),
)
def list_tables(database: str) -> dict:
    """List tables (SQL) or collections (NoSQL) from a database."""
    t0 = time.perf_counter()
    logger.info("listTables | database=%s", database)
    db_id, sql_conn, nosql_conn = _resolve_db(database)
    if sql_conn:
        result = meta_tools.list_tables(sql_conn)
        logger.info("listTables | database=%s | %d tables | %.0fms", database, len(result), (time.perf_counter() - t0) * 1000)
        return {"database": db_id, "tables": result}
    result = meta_tools.list_collections(nosql_conn)
    logger.info("listTables | database=%s | %d collections | %.0fms", database, len(result), (time.perf_counter() - t0) * 1000)
    return {"database": db_id, "collections": result}


@mcp.tool(
    name="describeTable",
    title="Describe table schema and detect PII columns [read-only]",
    description=(
        "Describes the schema of a SQL table or MongoDB collection and automatically detects "
        "columns containing personal data (PII). Returns column names, types, and which columns "
        "will be anonymized.\n\n"
        "Use this before querying to understand the table structure and see which columns are protected.\n\n"
        "Example: describeTable('pagila', 'customer') → schema with first_name, last_name, email flagged as PII."
    ),
)
def describe_table(database: str, table: str) -> dict:
    """Describe the schema of a SQL table or NoSQL collection."""
    t0 = time.perf_counter()
    logger.info("describeTable | database=%s | table=%s", database, table)
    db_id, sql_conn, nosql_conn = _resolve_db(database)
    patterns = _get_patterns()
    overrides = _get_overrides()
    sample_cfg = _config.get("detection", {}).get("value_scan", {})

    sensitivity = _get_sensitivity()

    if sql_conn:
        result = meta_tools.describe_table(
            sql_conn, table, db_id, _registry, patterns, overrides,
            scan_values_enabled=sample_cfg.get("enabled", True),
            sample_size=sample_cfg.get("sample_size", 50),
            sensitivity=sensitivity,
            table_context=_get_table_context(),
        )
    else:
        result = meta_tools.describe_collection(
            nosql_conn, table, db_id, _registry, patterns, overrides,
            sample_size=_config.get("mongodb", {}).get("schema_sample_size", 100),
            sensitivity=sensitivity,
        )
    logger.info("describeTable | database=%s | table=%s | %.0fms", database, table, (time.perf_counter() - t0) * 1000)
    return result


@mcp.tool(
    name="querySql",
    title="Execute SQL query and return anonymized results [read-only]",
    description=(
        "Executes a read-only SQL SELECT query and returns the results with personal data automatically "
        "anonymized. Names, emails, phone numbers, addresses and other PII are replaced with consistent "
        "fake data (pseudonyms). Non-PII columns (IDs, dates, statuses, booleans) pass through unchanged.\n\n"
        "Only SELECT queries are allowed - INSERT, UPDATE, DELETE and other write operations are blocked.\n\n"
        "Example: querySql('pagila', 'SELECT * FROM customer WHERE customer_id = 42')"
    ),
)
def query_sql(database: str, query: str) -> dict:
    """Execute a read-only SQL query and return anonymized results."""
    t0 = time.perf_counter()
    logger.info("querySql | database=%s | query=%s", database, query[:120])
    db_id, sql_conn, _ = _resolve_db(database)
    if sql_conn is None:
        return {"error": f"'{database}' n'est pas une base SQL"}
    result = sql_tools.execute_query(
        query, sql_conn, _pipeline, _registry, db_id, _config, _get_patterns(),
        sensitivity=_get_sensitivity(),
        table_context=_get_table_context(),
    )
    _post_query_cleanup()
    row_count = len(result.get("rows", []))
    logger.info("querySql | database=%s | %d lignes | %.0fms", database, row_count, (time.perf_counter() - t0) * 1000)
    return result


@mcp.tool(
    name="queryNosql",
    title="Execute MongoDB query and return anonymized documents [read-only]",
    description=(
        "Executes a MongoDB find query and returns anonymized documents. Personal data (names, emails, "
        "phones, etc.) is automatically replaced with fake data. Pass a JSON filter to select documents "
        "and an optional projection to choose fields.\n\n"
        "Example: queryNosql('ecommerce', 'customers', '{\"city\": \"Paris\"}', limit=50)"
    ),
)
def query_nosql(
    database: str,
    collection: str,
    filter: str = "{}",
    projection: str | None = None,
    limit: int = 100,
) -> dict:
    """Execute a NoSQL (MongoDB) query and return anonymized documents."""
    limit = _clamp_limit(limit)
    t0 = time.perf_counter()
    logger.info("queryNosql | database=%s | collection=%s | filter=%s", database, collection, filter[:120])
    db_id, _, nosql_conn = _resolve_db(database)
    if nosql_conn is None:
        return {"error": f"'{database}' n'est pas une base NoSQL"}
    result = nosql_tools.execute_query(
        collection, nosql_conn, _pipeline, _registry, db_id, _config,
        _get_patterns(), filter, projection, limit,
        sensitivity=_get_sensitivity(),
    )
    _post_query_cleanup()
    doc_count = len(result.get("documents", []))
    logger.info("queryNosql | database=%s | collection=%s | %d docs | %.0fms", database, collection, doc_count, (time.perf_counter() - t0) * 1000)
    return result


@mcp.tool(
    name="queryNosqlAggregate",
    title="Execute MongoDB aggregation pipeline with anonymized output [read-only]",
    description=(
        "Executes a MongoDB aggregation pipeline and returns anonymized results. Supports $match, $group, "
        "$sort, $project, $unwind, $limit and other read-only stages. Write stages ($out, $merge) are blocked.\n\n"
        "Personal data is automatically replaced with fake data in the output.\n\n"
        "Example: queryNosqlAggregate('ecommerce', 'customers', "
        "'[{\"$match\": {\"city\": \"Paris\"}}, {\"$group\": {\"_id\": \"$status\", \"count\": {\"$sum\": 1}}}]')"
    ),
)
def query_nosql_aggregate(
    database: str,
    collection: str,
    pipeline: str = "[]",
    limit: int = 100,
) -> dict:
    """Execute a MongoDB aggregation pipeline and return anonymized results."""
    limit = _clamp_limit(limit)
    t0 = time.perf_counter()
    logger.info("queryNosqlAggregate | database=%s | collection=%s", database, collection)
    db_id, _, nosql_conn = _resolve_db(database)
    if nosql_conn is None:
        return {"error": f"'{database}' n'est pas une base NoSQL"}
    result = nosql_tools.execute_aggregate(
        collection, nosql_conn, _pipeline, _registry, db_id, _config,
        _get_patterns(), pipeline, limit,
        sensitivity=_get_sensitivity(),
    )
    _post_query_cleanup()
    doc_count = len(result.get("documents", []))
    logger.info("queryNosqlAggregate | database=%s | collection=%s | %d docs | %.0fms", database, collection, doc_count, (time.perf_counter() - t0) * 1000)
    return result


@mcp.tool(
    name="generateTestFixtures",
    title="Generate anonymized test fixtures (JSON/CSV) [read-only]",
    description=(
        "Generates anonymized test fixtures from a SQL table or MongoDB collection. "
        "Exported data is pseudonymized and ready to use in unit tests (pytest) with no risk of PII leakage. "
        "Supports JSON and CSV output formats.\n\n"
        "Example: generateTestFixtures('pagila', 'customer', limit=20, format='csv')"
    ),
)
def generate_test_fixtures(
    database: str,
    table: str,
    limit: int = 10,
    format: str = "json",
) -> dict:
    """Generate anonymized test fixtures from a table or collection."""
    limit = _clamp_limit(limit)
    t0 = time.perf_counter()
    logger.info("generateTestFixtures | database=%s | table=%s | limit=%d | format=%s", database, table, limit, format)
    db_id, sql_conn, nosql_conn = _resolve_db(database)
    patterns = _get_patterns()

    if sql_conn:
        result = fixture_tools.generate_sql_fixtures(
            sql_conn, table, db_id, _pipeline, _registry, patterns, limit, format,
            sensitivity=_get_sensitivity(),
            table_context=_get_table_context(),
        )
    else:
        result = fixture_tools.generate_nosql_fixtures(
            nosql_conn, table, db_id, _pipeline, _registry, patterns, limit,
            sensitivity=_get_sensitivity(),
        )
    _post_query_cleanup()
    logger.info("generateTestFixtures | database=%s | table=%s | %.0fms", database, table, (time.perf_counter() - t0) * 1000)
    return result


def main():
    """Main entry point: initialize the server and start the MCP transport.

    Returns:
        None
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _init()

    if _transport == "stdio":
        mcp.run(transport="stdio")
        return

    # Network transport: expose SSE (/sse, /messages) AND streamable-http (/mcp)
    # in parallel on the same port. SSE stays the path used by Claude Code, while
    # /mcp serves clients that require streamable-http (Codex, ...). Any value of
    # MCP_TRANSPORT other than "stdio" enables this dual mode, which keeps backward
    # compatibility with MCP_TRANSPORT=sse.
    sse_app = mcp.sse_app()
    http_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        # streamable-http must start its session manager through its lifespan;
        # the SSE lifespan is started too (harmless if it is empty).
        async with sse_app.router.lifespan_context(app):
            async with http_app.router.lifespan_context(app):
                yield

    app = Starlette(
        routes=[*http_app.routes, *sse_app.routes],
        lifespan=lifespan,
    )
    app.add_middleware(BearerAuthMiddleware)
    app.add_middleware(RateLimitMiddleware, max_requests=60, window_seconds=60)
    logger.info("Starting SSE (/sse) + streamable-http (/mcp) on %s:%d", _mcp_host, _mcp_port)
    uvicorn.run(app, host=_mcp_host, port=_mcp_port)


if __name__ == "__main__":
    main()
