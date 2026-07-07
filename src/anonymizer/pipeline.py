"""Central anonymization pipeline that orchestrates PII detection and pseudonymization.

Handles anonymization of SQL rows and NoSQL documents using the PII registry
and mapping store to ensure pseudonym consistency.
"""

import json
import math
from decimal import Decimal

from src.anonymizer.pseudonymizer import pseudonymize
from src.detection.registry import PIIRegistry
from src.storage.mapping_store import MappingStore


class AnonymizationPipeline:
    """Anonymization pipeline that transforms PII-containing data into consistent fake data."""

    def __init__(
        self,
        registry: PIIRegistry,
        store: MappingStore,
        session_salt: str,
        locale: str = "fr_FR",
    ):
        """Initialize the anonymization pipeline.

        Args:
            registry: PII registry containing column-to-PII-type mappings per table.
            store: Mapping store for pseudonym caching.
            session_salt: Session salt for deterministic pseudonym generation.
            locale: Faker locale for fake data (default: 'fr_FR').
        """
        self.registry = registry
        self.store = store
        self.session_salt = session_salt
        self.locale = locale
        self._session_id = session_salt[:16]

    def anonymize_value(self, real_value, entity_type: str):
        """Anonymize a single value using the cache or by generating a new pseudonym.

        Args:
            real_value: Real value to anonymize.
            entity_type: PII type (e.g. 'EMAIL', 'PERSON', 'PHONE').

        Returns:
            The pseudonymized value, or the original value if it is None, empty,
            boolean, NaN or Inf.
        """
        if real_value is None or (isinstance(real_value, str) and not real_value.strip()):
            return real_value
        if isinstance(real_value, bool):
            return real_value
        if isinstance(real_value, float) and (math.isnan(real_value) or math.isinf(real_value)):
            return real_value
        if isinstance(real_value, Decimal) and (real_value.is_nan() or real_value.is_infinite()):
            return real_value

        str_val = str(real_value)
        cached = self.store.get(self._session_id, str_val)
        if cached is not None:
            return cached

        fake_val = pseudonymize(real_value, entity_type, self.session_salt, self.locale)
        self.store.put(self._session_id, entity_type, str_val, str(fake_val))
        return fake_val

    def anonymize_rows(
        self,
        db_id: str,
        table: str,
        columns: list[str],
        rows: list[tuple],
    ) -> list[list]:
        """Anonymize a set of SQL rows by replacing PII columns.

        Args:
            db_id: Database identifier.
            table: Table name (or '__query__' for query results).
            columns: List of column names in result order.
            rows: List of tuples representing data rows.

        Returns:
            List of lists with PII values replaced by pseudonyms.
            Binary data is replaced by '[BINARY DATA]'.
        """
        pii_map = self.registry.get_pii_columns(db_id, table)
        result = []
        for row in rows:
            new_row = []
            for i, value in enumerate(row):
                col_name = columns[i] if i < len(columns) else f"col_{i}"
                pii_type = pii_map.get(col_name)

                if pii_type and pii_type != "SAFE":
                    if isinstance(value, bytes):
                        new_row.append("[BINARY DATA]")
                    elif isinstance(value, (dict, list)):
                        new_row.append(self._walk_json(value, pii_map, col_name))
                    else:
                        new_row.append(self.anonymize_value(value, pii_type))
                elif isinstance(value, bytes):
                    new_row.append("[BINARY DATA]")
                else:
                    new_row.append(value)
            result.append(new_row)
        return result

    def anonymize_documents(
        self,
        db_id: str,
        collection: str,
        documents: list[dict],
    ) -> list[dict]:
        """Anonymize a list of MongoDB documents by replacing PII fields.

        Args:
            db_id: Database identifier.
            collection: MongoDB collection name.
            documents: List of dicts representing MongoDB documents.

        Returns:
            List of dicts with PII fields replaced by pseudonyms.
        """
        pii_map = self.registry.get_pii_columns(db_id, collection)
        return [self._walk_dict(doc, pii_map) for doc in documents]

    def _walk_dict(self, doc: dict, pii_map: dict[str, str], prefix: str = "") -> dict:
        """Recursively traverse a dictionary and anonymize PII fields.

        Args:
            doc: Dictionary to traverse.
            pii_map: Mapping {field_name: PII_type} to identify fields to anonymize.
            prefix: Path prefix for nested keys (e.g. 'address.street').

        Returns:
            New dictionary with PII values replaced.
        """
        result = {}
        for key, value in doc.items():
            full_key = f"{prefix}.{key}" if prefix else key
            pii_type = pii_map.get(full_key) or pii_map.get(key)

            if isinstance(value, dict):
                result[key] = self._walk_dict(value, pii_map, full_key)
            elif isinstance(value, list):
                result[key] = [
                    self._walk_dict(item, pii_map, full_key) if isinstance(item, dict)
                    else (self.anonymize_value(item, pii_type) if pii_type else item)
                    for item in value
                ]
            elif isinstance(value, bytes):
                result[key] = "[BINARY DATA]"
            elif pii_type and pii_type != "SAFE":
                result[key] = self.anonymize_value(value, pii_type)
            else:
                result[key] = value
        return result

    def _walk_json(self, value, pii_map: dict, parent_col: str):
        """Traverse and anonymize a JSON value (JSON string, dict or list).

        Args:
            value: Value to process - can be a JSON string, dict or list.
            pii_map: Mapping {field_name: PII_type} for detection.
            parent_col: Parent column name containing this JSON value.

        Returns:
            The value with PII fields anonymized. JSON strings are parsed,
            anonymized then re-serialized.
        """
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return json.dumps(self._walk_dict(parsed, pii_map))
            except (json.JSONDecodeError, TypeError):
                return value
        if isinstance(value, dict):
            return self._walk_dict(value, pii_map)
        if isinstance(value, list):
            return [self._walk_json(item, pii_map, parent_col) for item in value]
        return value
