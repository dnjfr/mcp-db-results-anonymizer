"""SQL query execution with automatic result anonymization.

Orchestrates security validation, PII detection (by schema, SQL lineage
and value scanning) then anonymization of result rows.
"""

import re

from src.anonymizer.pipeline import AnonymizationPipeline
from src.database.sql_connector import BaseSQLConnector
from src.detection.registry import PIIRegistry
from src.detection.sql_lineage import trace_column_lineage
from src.detection.value_scanner import scan_error_message, scan_values
from src.security.sql_validator import validate_query
from src.tools.metadata import describe_table

_TABLE_PATTERN = re.compile(
    r"\bFROM\s+([\w]+(?:\.[\w]+)?)|\bJOIN\s+([\w]+(?:\.[\w]+)?)",
    re.IGNORECASE,
)


def _extract_tables(query: str) -> list[str]:
    """Extract table names referenced in a SQL query (FROM and JOIN).

    Args:
        query: SQL query to analyze.

    Returns:
        Deduplicated list of table names (lowercase).
    """
    matches = _TABLE_PATTERN.findall(query)
    tables = set()
    for groups in matches:
        for g in groups:
            if g:
                tables.add(g.lower())
    return list(tables)


def _resolve_lineage_pii(
    query: str,
    result_columns: list[str],
    known_pii: dict[str, str],
    dialect: str,
) -> dict[str, str]:
    """Propagate PII types from source columns to result columns via SQL lineage analysis.

    Uses sqlglot to trace lineage through aliases, SQL functions
    (UPPER, TRIM, CAST...) and concatenations.

    Args:
        query: Executed SQL SELECT query.
        result_columns: Column names in the query result.
        known_pii: Already detected PII types {column_name: PII_type}.
        dialect: SQL dialect for parsing ('postgresql', 'mysql', 'mssql').

    Returns:
        Dict of new PII columns detected by lineage {result_column_name: PII_type}.
    """
    lineage_pii: dict[str, str] = {}
    try:
        lineage = trace_column_lineage(query, dialect=dialect)
    except Exception:
        return lineage_pii

    result_lower_map = {c.lower(): c for c in result_columns}

    for output_col, source_cols in lineage.items():
        actual_col = result_lower_map.get(output_col)
        if not actual_col:
            continue
        if actual_col in known_pii:
            continue
        for src in source_cols:
            pii_type = known_pii.get(src) or known_pii.get(src.lower())
            if pii_type and pii_type != "SAFE":
                lineage_pii[actual_col] = pii_type
                break

    return lineage_pii


_SKIP_SCAN_TYPES = (int, float, bool, type(None))

def _should_scan_column(values: list) -> bool:
    """Check if a column's values are text-like and worth scanning for PII.

    Skips columns where all non-null values are numeric, date, or datetime.
    """
    from datetime import date, datetime
    from decimal import Decimal
    skip_types = (int, float, bool, Decimal, date, datetime)
    non_null = [v for v in values if v is not None]
    if not non_null:
        return False
    return not all(isinstance(v, skip_types) for v in non_null)


def _fallback_value_scan(columns: list[str], rows: list[tuple], known_pii: dict[str, str]) -> dict[str, str]:
    """Last resort scan: analyze values of columns not yet classified as PII.

    Only scans text-like columns. Skips numeric, date, and datetime columns
    to avoid false positives.

    Args:
        columns: Result column names.
        rows: Data rows (tuples).
        known_pii: Already detected PII columns (excluded from scan).

    Returns:
        Dict of newly detected PII columns {column_name: PII_type}.
    """
    extra_pii: dict[str, str] = {}
    for i, col in enumerate(columns):
        if col in known_pii:
            continue
        values = [row[i] for row in rows[:50] if i < len(row)]
        if not _should_scan_column(values):
            continue
        pii_type = scan_values(values)
        if pii_type:
            extra_pii[col] = pii_type
    return extra_pii


def execute_query(
    query: str,
    connector: BaseSQLConnector,
    pipeline: AnonymizationPipeline,
    registry: PIIRegistry,
    db_id: str,
    config: dict,
    patterns: dict[str, list[str]],
    sensitivity: dict | None = None,
    table_context: dict | None = None,
) -> dict:
    """Execute a read-only SQL query and return anonymized results.

    Full pipeline: security validation -> table PII detection ->
    execution -> PII propagation via SQL lineage -> fallback value scan -> anonymization.

    Args:
        query: SQL SELECT query to execute.
        connector: Active SQL connector.
        pipeline: Anonymization pipeline.
        registry: PII registry for detection and caching.
        db_id: Database identifier.
        config: Global configuration (security.max_rows, security.blocked_tables, etc.).
        patterns: PII detection patterns {PII_type: [glob_patterns]}.
        sensitivity: Manual sensitivity configuration.
        table_context: Table-context PII rules {PII_type: {tables: [...], columns: [...]}}.

    Returns:
        Dict with keys: columns, rows (anonymized), row_count,
        tables_detected, pii_columns_anonymized. On error: {'error': message}.
    """
    valid, reason = validate_query(
        query,
        blocked_tables=config.get("security", {}).get("blocked_tables"),
        dialect=connector.dialect,
    )
    if not valid:
        return {"error": reason}

    tables = _extract_tables(query)
    detection_failed = False
    for table in tables:
        if not registry.is_registered(db_id, table):
            try:
                describe_table(
                    connector, table, db_id, registry, patterns,
                    sensitivity=sensitivity, table_context=table_context,
                )
            except Exception:
                detection_failed = True

    if detection_failed:
        return {"error": "Unable to detect PII (no table identified). Query blocked for security reasons."}

    max_rows = config.get("security", {}).get("max_rows", 1000)

    try:
        columns, rows = connector.execute(query, max_rows)
    except Exception as e:
        error_msg = str(e)
        if config.get("security", {}).get("scan_error_messages", True):
            error_msg = scan_error_message(error_msg)
        return {"error": error_msg}

    all_pii: dict[str, str] = {}
    for table in tables:
        all_pii.update(registry.get_pii_columns(db_id, table))

    lineage_pii = _resolve_lineage_pii(query, columns, all_pii, connector.dialect)
    all_pii.update(lineage_pii)

    extra_pii = _fallback_value_scan(columns, rows, all_pii)
    all_pii.update(extra_pii)

    registry.register(db_id, "__query__", all_pii)

    anonymized = pipeline.anonymize_rows(db_id, "__query__", columns, rows)

    return {
        "columns": columns,
        "rows": anonymized,
        "row_count": len(anonymized),
        "tables_detected": tables,
        "pii_columns_anonymized": [c for c in columns if c in all_pii and all_pii[c] != "SAFE"],
    }
