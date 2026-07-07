"""Anonymized test fixture generation from SQL tables or MongoDB collections.

Exported data is pseudonymized and ready for unit tests
with no risk of personal data leakage.
"""

import csv
import io
import json

from src.anonymizer.pipeline import AnonymizationPipeline
from src.database.nosql_connector import NoSQLConnector
from src.database.sql_connector import BaseSQLConnector
from src.detection.registry import PIIRegistry
from src.security.sql_validator import validate_identifier
from src.tools.metadata import describe_table, describe_collection


def generate_sql_fixtures(
    connector: BaseSQLConnector,
    table: str,
    db_id: str,
    pipeline: AnonymizationPipeline,
    registry: PIIRegistry,
    patterns: dict[str, list[str]],
    limit: int = 10,
    format: str = "json",
    sensitivity: dict | None = None,
    table_context: dict | None = None,
) -> dict:
    """Generate anonymized test fixtures from a SQL table.

    Exports real data, anonymizes it and formats it as JSON or CSV.

    Args:
        connector: Active SQL connector.
        table: Table name (validated against SQL injection).
        db_id: Database identifier.
        pipeline: Anonymization pipeline.
        registry: PII registry.
        patterns: PII detection patterns.
        limit: Number of rows to export (default: 10).
        format: Output format - 'json' or 'csv' (default: 'json').
        sensitivity: Manual sensitivity configuration.
        table_context: Table-context PII rules.

    Returns:
        Dict with keys: format, table, row_count, pii_columns_anonymized, content.
        On error: {'error': message}.
    """
    safe_table = validate_identifier(table)

    if not registry.is_registered(db_id, safe_table):
        try:
            describe_table(
                connector, safe_table, db_id, registry, patterns,
                sensitivity=sensitivity, table_context=table_context,
            )
        except Exception:
            return {"error": f"Unable to detect PII for table '{safe_table}'. Query blocked for security reasons."}

    if connector.dialect == "mssql":
        columns, rows = connector.execute(
            f"SELECT TOP {int(limit)} * FROM {safe_table}", max_rows=limit,
        )
    else:
        columns, rows = connector.execute(
            f"SELECT * FROM {safe_table} LIMIT {int(limit)}", max_rows=limit,
        )

    all_pii = registry.get_pii_columns(db_id, table)
    registry.register(db_id, "__fixtures__", all_pii)
    anonymized = pipeline.anonymize_rows(db_id, "__fixtures__", columns, rows)

    records = [dict(zip(columns, row)) for row in anonymized]

    if format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        for row in anonymized:
            writer.writerow(str(v) if v is not None else "" for v in row)
        return {
            "format": "csv",
            "table": table,
            "row_count": len(records),
            "pii_columns_anonymized": [c for c in columns if c in all_pii and all_pii[c] != "SAFE"],
            "content": buf.getvalue(),
        }

    return {
        "format": "json",
        "table": table,
        "row_count": len(records),
        "pii_columns_anonymized": [c for c in columns if c in all_pii and all_pii[c] != "SAFE"],
        "content": json.dumps(records, ensure_ascii=False, default=str),
    }


def generate_nosql_fixtures(
    connector: NoSQLConnector,
    collection: str,
    db_id: str,
    pipeline: AnonymizationPipeline,
    registry: PIIRegistry,
    patterns: dict[str, list[str]],
    limit: int = 10,
    sensitivity: dict | None = None,
) -> dict:
    """Generate anonymized test fixtures from a MongoDB collection.

    Exports real documents, anonymizes them and formats them as JSON.

    Args:
        connector: Active NoSQL connector.
        collection: MongoDB collection name.
        db_id: Database identifier.
        pipeline: Anonymization pipeline.
        registry: PII registry.
        patterns: PII detection patterns.
        limit: Number of documents to export (default: 10).
        sensitivity: Manual sensitivity configuration.

    Returns:
        Dict with keys: format, collection, document_count,
        pii_fields_anonymized, content (JSON). On error: {'error': message}.
    """
    if not registry.is_registered(db_id, collection):
        try:
            describe_collection(connector, collection, db_id, registry, patterns, sensitivity=sensitivity)
        except Exception:
            return {"error": f"Unable to detect PII for collection '{collection}'. Query blocked for security reasons."}

    docs = connector.find(collection, {}, None, limit)
    pii_map = registry.get_pii_columns(db_id, collection)
    anonymized = pipeline.anonymize_documents(db_id, collection, docs)

    return {
        "format": "json",
        "collection": collection,
        "document_count": len(anonymized),
        "pii_fields_anonymized": [k for k, t in pii_map.items() if t != "SAFE"],
        "content": json.dumps(anonymized, ensure_ascii=False, default=str),
    }
