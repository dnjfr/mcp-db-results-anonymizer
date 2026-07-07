"""Metadata tools for schema introspection with automatic PII detection.

Provides list_tables, describe_table (SQL) and their NoSQL equivalents
(list_collections, describe_collection) with built-in value scanning.
"""

from src.database.sql_connector import BaseSQLConnector
from src.database.nosql_connector import NoSQLConnector
from src.detection.column_matcher import detect_table_pii, detect_mongo_pii
from src.detection.value_scanner import scan_values
from src.detection.registry import PIIRegistry


def list_tables(connector: BaseSQLConnector) -> list[str]:
    """List tables from a SQL database via the connector.

    Args:
        connector: SQL connector (PostgreSQL, MySQL or MSSQL).

    Returns:
        Sorted list of table names.
    """
    return connector.list_tables()


def describe_table(
    connector: BaseSQLConnector,
    table_name: str,
    db_id: str,
    registry: PIIRegistry,
    patterns: dict[str, list[str]],
    overrides: dict[str, str] | None = None,
    scan_values_enabled: bool = True,
    sample_size: int = 50,
    sensitivity: dict | None = None,
    table_context: dict[str, dict] | None = None,
) -> dict:
    """Describe the schema of a SQL table with automatic PII column detection.

    Combines column name detection (patterns), table-context rules and value
    analysis (scan_values) for text columns not yet identified. Registers
    results in the PII registry.

    Args:
        connector: Active SQL connector.
        table_name: Table name to describe.
        db_id: Database identifier.
        registry: PII registry for result registration.
        patterns: Detection patterns {PII_type: [glob_patterns]}.
        overrides: Manual overrides {column_name: PII_type}.
        scan_values_enabled: Enable value scanning for text columns (default: True).
        sample_size: Number of values to sample for scanning (default: 50).
        sensitivity: Manual sensitivity configuration.
        table_context: Table-context PII rules {PII_type: {tables: [...], columns: [...]}}.

    Returns:
        Dict with keys: table, columns (schema list enriched with pii_type),
        pii_columns (names of detected PII columns, excluding SAFE).
    """
    schema = connector.get_table_schema(table_name)
    pii_map = detect_table_pii(
        schema, patterns, overrides, sensitivity,
        table_name=table_name, table_context=table_context,
    )

    if scan_values_enabled:
        for col in schema:
            col_name = col["name"]
            if col_name not in pii_map and col["type"].lower() in (
                "character varying", "text", "character", "varchar",
                "nvarchar", "nchar", "ntext", "longtext", "mediumtext", "tinytext",
            ):
                try:
                    values = connector.sample_values(table_name, col_name, sample_size)
                    pii_type = scan_values(values)
                    if pii_type:
                        pii_map[col_name] = pii_type
                except Exception:
                    pass

    registry.register(db_id, table_name, pii_map)

    columns_out = []
    for col in schema:
        col_info = {
            "name": col["name"],
            "type": col["type"],
            "nullable": col["nullable"],
            "primary_key": col["primary_key"],
        }
        if col["name"] in pii_map:
            col_info["pii_type"] = pii_map[col["name"]]
        columns_out.append(col_info)

    return {
        "table": table_name,
        "columns": columns_out,
        "pii_columns": [c for c, t in pii_map.items() if t != "SAFE"],
    }


def list_collections(connector: NoSQLConnector) -> list[str]:
    """List collections from a MongoDB database via the connector.

    Args:
        connector: NoSQL connector (MongoDB).

    Returns:
        Sorted list of collection names.
    """
    return connector.list_collections()


def describe_collection(
    connector: NoSQLConnector,
    collection_name: str,
    db_id: str,
    registry: PIIRegistry,
    patterns: dict[str, list[str]],
    overrides: dict[str, str] | None = None,
    sample_size: int = 100,
    sensitivity: dict | None = None,
) -> dict:
    """Describe the inferred schema of a MongoDB collection with automatic PII detection.

    Infers the schema by sampling documents, then detects PII fields
    and registers results in the registry.

    Args:
        connector: Active NoSQL connector.
        collection_name: Collection name to describe.
        db_id: Database identifier.
        registry: PII registry for result registration.
        patterns: Detection patterns {PII_type: [glob_patterns]}.
        overrides: Manual overrides {field_name: PII_type}.
        sample_size: Number of documents to sample for inference (default: 100).
        sensitivity: Manual sensitivity configuration.

    Returns:
        Dict with keys: collection, fields (schema enriched with pii_type),
        pii_fields (names of detected PII fields, excluding SAFE).
    """
    schema = connector.infer_schema(collection_name, sample_size)
    keys = [field["name"] for field in schema]
    pii_map = detect_mongo_pii(keys, patterns, overrides, sensitivity)

    registry.register(db_id, collection_name, pii_map)

    fields_out = []
    for field in schema:
        field_info = {
            "name": field["name"],
            "types": field["types"],
            "frequency": field["frequency"],
        }
        if field["name"] in pii_map:
            field_info["pii_type"] = pii_map[field["name"]]
        fields_out.append(field_info)

    return {
        "collection": collection_name,
        "fields": fields_out,
        "pii_fields": [k for k, t in pii_map.items() if t != "SAFE"],
    }
