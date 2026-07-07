"""Central registry of detected PII columns, indexed by database and table."""


class PIIRegistry:
    """In-memory registry of detected PII columns by database and table.

    Stores detection results to avoid re-scanning tables
    already analyzed within the same server session.
    """

    def __init__(self):
        """Initialize an empty PII registry."""
        self._registry: dict[str, dict[str, str]] = {}

    def _key(self, db_id: str, table: str) -> str:
        """Build the internal registry key for a table.

        Args:
            db_id: Database identifier.
            table: Table or collection name.

        Returns:
            Key in 'db_id.table' format.
        """
        return f"{db_id}.{table}"

    def register(self, db_id: str, table: str, pii_map: dict[str, str]):
        """Register the detected PII mapping for a table.

        Args:
            db_id: Database identifier.
            table: Table or collection name.
            pii_map: Dict {column_name: PII_type} (e.g. {'email': 'EMAIL'}).

        Returns:
            None
        """
        self._registry[self._key(db_id, table)] = pii_map

    def get_pii_columns(self, db_id: str, table: str) -> dict[str, str]:
        """Retrieve the registered PII mapping for a table.

        Args:
            db_id: Database identifier.
            table: Table or collection name.

        Returns:
            Dict {column_name: PII_type}, or empty dict if the table is not registered.
        """
        return self._registry.get(self._key(db_id, table), {})

    def is_pii(self, db_id: str, table: str, column: str) -> str | None:
        """Check if a specific column is identified as PII.

        Args:
            db_id: Database identifier.
            table: Table or collection name.
            column: Column name to check.

        Returns:
            The PII type (e.g. 'EMAIL', 'PERSON') if the column is PII, None otherwise.
        """
        return self.get_pii_columns(db_id, table).get(column)

    def is_registered(self, db_id: str, table: str) -> bool:
        """Check if a table has already been analyzed and registered.

        Args:
            db_id: Database identifier.
            table: Table or collection name.

        Returns:
            True if the table is already in the registry, False otherwise.
        """
        return self._key(db_id, table) in self._registry


pii_registry = PIIRegistry()
