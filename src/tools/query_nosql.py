"""NoSQL (MongoDB) query execution with automatic result anonymization.

Supports find and aggregate operations with built-in security validation.
"""

import json

from src.anonymizer.pipeline import AnonymizationPipeline
from src.database.nosql_connector import NoSQLConnector
from src.detection.registry import PIIRegistry
from src.detection.value_scanner import scan_error_message
from src.tools.metadata import describe_collection


def execute_query(
    collection: str,
    connector: NoSQLConnector,
    pipeline: AnonymizationPipeline,
    registry: PIIRegistry,
    db_id: str,
    config: dict,
    patterns: dict[str, list[str]],
    filter_json: str = "{}",
    projection_json: str | None = None,
    limit: int = 100,
    sensitivity: dict | None = None,
) -> dict:
    """Execute a MongoDB find query and return anonymized documents.

    Pipeline: JSON filter/projection parsing -> collection PII detection ->
    find execution -> document anonymization.

    Args:
        collection: MongoDB collection name.
        connector: Active NoSQL connector.
        pipeline: Anonymization pipeline.
        registry: PII registry.
        db_id: Database identifier.
        config: Global configuration (security.max_rows).
        patterns: PII detection patterns.
        filter_json: MongoDB filter as JSON (default: '{}').
        projection_json: MongoDB projection as JSON (optional).
        limit: Maximum number of documents (default: 100).
        sensitivity: Manual sensitivity configuration.

    Returns:
        Dict with keys: documents (anonymized), count, collection,
        pii_fields_anonymized. On error: {'error': message}.
    """
    try:
        filter_ = json.loads(filter_json)
    except json.JSONDecodeError as e:
        return {"error": f"Filtre JSON invalide: {e}"}

    projection = None
    if projection_json:
        try:
            projection = json.loads(projection_json)
        except json.JSONDecodeError as e:
            return {"error": f"Projection JSON invalide: {e}"}

    if not registry.is_registered(db_id, collection):
        try:
            describe_collection(connector, collection, db_id, registry, patterns, sensitivity=sensitivity)
        except Exception:
            return {"error": f"Unable to detect PII for collection '{collection}'. Query blocked for security reasons."}

    try:
        docs = connector.find(collection, filter_, projection, limit)
    except Exception as e:
        return {"error": scan_error_message(str(e))}

    pii_map = registry.get_pii_columns(db_id, collection)
    anonymized = pipeline.anonymize_documents(db_id, collection, docs)

    return {
        "documents": anonymized,
        "count": len(anonymized),
        "collection": collection,
        "pii_fields_anonymized": [k for k, t in pii_map.items() if t != "SAFE"],
    }


def execute_aggregate(
    collection: str,
    connector: NoSQLConnector,
    pipeline_obj: AnonymizationPipeline,
    registry: PIIRegistry,
    db_id: str,
    config: dict,
    patterns: dict[str, list[str]],
    pipeline_json: str = "[]",
    limit: int = 100,
    sensitivity: dict | None = None,
) -> dict:
    """Execute a MongoDB aggregation pipeline and return anonymized results.

    Write stages ($out, $merge) are blocked by the connector.

    Args:
        collection: MongoDB collection name.
        connector: Active NoSQL connector.
        pipeline_obj: Anonymization pipeline.
        registry: PII registry.
        db_id: Database identifier.
        config: Global configuration (security.max_rows).
        patterns: PII detection patterns.
        pipeline_json: Aggregation pipeline as JSON (default: '[]').
        limit: Maximum number of output documents (default: 100).
        sensitivity: Manual sensitivity configuration.

    Returns:
        Dict with keys: documents (anonymized), count, collection,
        pipeline_stages, pii_fields_anonymized. On error: {'error': message}.
    """
    try:
        stages = json.loads(pipeline_json)
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON pipeline: {e}"}

    if not isinstance(stages, list):
        return {"error": "The pipeline must be a JSON array of stages."}

    if not registry.is_registered(db_id, collection):
        try:
            describe_collection(connector, collection, db_id, registry, patterns, sensitivity=sensitivity)
        except Exception:
            return {"error": f"Unable to detect PII for collection '{collection}'. Query blocked for security reasons."}

    try:
        docs = connector.aggregate(collection, stages, limit)
    except ValueError as e:
        return {"error": scan_error_message(str(e))}
    except Exception as e:
        return {"error": scan_error_message(str(e))}

    pii_map = registry.get_pii_columns(db_id, collection)
    anonymized = pipeline_obj.anonymize_documents(db_id, collection, docs)

    return {
        "documents": anonymized,
        "count": len(anonymized),
        "collection": collection,
        "pipeline_stages": len(stages),
        "pii_fields_anonymized": [k for k, t in pii_map.items() if t != "SAFE"],
    }
