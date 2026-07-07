"""Abstract SQL connectors and PostgreSQL implementation via SQLAlchemy.

Provides a common interface for read-only query execution,
schema introspection and value sampling.
"""

from abc import ABC, abstractmethod

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL

from src.security.sql_validator import validate_identifier


class BaseSQLConnector(ABC):
    """Abstract base class for SQL connectors. Provides read-only execution and introspection."""

    def __init__(self, config: dict):
        """Initialize the connector with connection configuration.

        Args:
            config: Configuration dictionary (keys: user, password, host, port, database).
        """
        self._engine: Engine = self._build_engine(config)

    @abstractmethod
    def _build_engine(self, config: dict) -> Engine:
        """Build the SQLAlchemy engine for the specific dialect.

        Args:
            config: Connection configuration dictionary.

        Returns:
            Configured SQLAlchemy Engine instance.
        """
        ...

    @abstractmethod
    def _set_read_only(self, conn) -> None:
        """Set the connection to read-only mode.

        Args:
            conn: Active SQLAlchemy connection.

        Returns:
            None
        """
        ...

    @abstractmethod
    def list_tables(self) -> list[str]:
        """List all user tables in the database.

        Returns:
            Sorted list of table names.
        """
        ...

    @abstractmethod
    def get_table_schema(self, table_name: str) -> list[dict]:
        """Retrieve the detailed schema of a table.

        Args:
            table_name: Table name.

        Returns:
            List of dicts with keys: name, type, nullable, default, primary_key.
        """
        ...

    @property
    def dialect(self) -> str:
        """Return the SQL dialect name of the engine (e.g. 'postgresql', 'mysql', 'mssql').

        Returns:
            Dialect name as a string.
        """
        return self._engine.dialect.name

    def execute(self, query: str, max_rows: int = 1000) -> tuple[list[str], list[tuple]]:
        """Execute a SQL query in read-only mode and return the results.

        Args:
            query: SQL query to execute.
            max_rows: Maximum number of rows to fetch (default: 1000).

        Returns:
            Tuple (columns, rows) where columns is a list of names and rows
            is a list of tuples containing the data.
        """
        with self._engine.connect() as conn:
            self._set_read_only(conn)
            result = conn.execute(text(query))
            columns = list(result.keys())
            rows = result.fetchmany(max_rows)
            return columns, [tuple(row) for row in rows]

    def sample_values(self, table_name: str, column_name: str, limit: int = 50) -> list:
        """Sample distinct non-null values from a column.

        Args:
            table_name: Table name (validated against SQL injection).
            column_name: Column name (validated against SQL injection).
            limit: Maximum number of values to fetch (default: 50).

        Returns:
            List of sampled distinct values.

        Raises:
            ValueError: If the table or column name contains invalid characters.
        """
        safe_table = validate_identifier(table_name)
        safe_column = validate_identifier(column_name)
        with self._engine.connect() as conn:
            self._set_read_only(conn)
            result = conn.execute(text(
                f"SELECT DISTINCT {safe_column} FROM {safe_table} "
                f"WHERE {safe_column} IS NOT NULL LIMIT :limit"
            ), {"limit": limit})
            return [row[0] for row in result]

    @property
    def engine(self) -> Engine:
        """Return the underlying SQLAlchemy engine.

        Returns:
            SQLAlchemy Engine instance.
        """
        return self._engine


class PostgreSQLConnector(BaseSQLConnector):
    """PostgreSQL connector with read-only transactions."""

    def _build_engine(self, config: dict) -> Engine:
        """Build the SQLAlchemy engine for PostgreSQL.

        Args:
            config: Configuration with keys user, password, host, port, database.

        Returns:
            SQLAlchemy Engine configured for PostgreSQL with pool_pre_ping.
        """
        url = URL.create(
            "postgresql",
            username=config["user"],
            password=config["password"],
            host=config["host"],
            port=config["port"],
            database=config["database"],
        )
        return create_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_timeout=30,
            pool_recycle=1800,
        )

    def _set_read_only(self, conn) -> None:
        """Enable read-only mode via SET default_transaction_read_only.

        Args:
            conn: Active SQLAlchemy connection.

        Returns:
            None
        """
        conn.execute(text("SET default_transaction_read_only = ON"))

    def list_tables(self) -> list[str]:
        """List tables from all user schemas of the PostgreSQL database.

        Returns schema-qualified names (schema.table) for non-public schemas,
        and plain names for the public schema.

        Returns:
            Sorted list of table names.
        """
        with self._engine.connect() as conn:
            result = conn.execute(text(
                "SELECT schemaname, tablename FROM pg_tables "
                "WHERE schemaname NOT IN ('pg_catalog', 'information_schema') "
                "ORDER BY schemaname, tablename"
            ))
            tables = []
            for schema, table in result:
                if schema == "public":
                    tables.append(table)
                else:
                    tables.append(f"{schema}.{table}")
            return tables

    @staticmethod
    def _parse_table_name(table_name: str) -> tuple[str, str]:
        """Parse a possibly schema-qualified table name into (schema, table).

        Args:
            table_name: 'table' or 'schema.table'.

        Returns:
            Tuple (schema, table_name).
        """
        if "." in table_name:
            schema, table = table_name.split(".", 1)
            return schema, table
        return "public", table_name

    def get_table_schema(self, table_name: str) -> list[dict]:
        """Retrieve the schema of a PostgreSQL table via information_schema.

        Supports schema-qualified names (e.g. 'employees.salary').

        Args:
            table_name: Table name, optionally schema-qualified.

        Returns:
            List of dicts with keys: name, type, nullable, default, primary_key.
        """
        schema, table = self._parse_table_name(table_name)
        with self._engine.connect() as conn:
            result = conn.execute(text("""
                SELECT
                    c.column_name,
                    c.data_type,
                    c.is_nullable,
                    c.column_default,
                    CASE WHEN pk.column_name IS NOT NULL THEN true ELSE false END as is_primary_key
                FROM information_schema.columns c
                LEFT JOIN (
                    SELECT ku.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage ku
                        ON tc.constraint_name = ku.constraint_name
                    WHERE tc.table_schema = :schema
                      AND tc.table_name = :table
                      AND tc.constraint_type = 'PRIMARY KEY'
                ) pk ON c.column_name = pk.column_name
                WHERE c.table_schema = :schema AND c.table_name = :table
                ORDER BY c.ordinal_position
            """), {"schema": schema, "table": table})
            return [
                {
                    "name": row[0],
                    "type": row[1],
                    "nullable": row[2] == "YES",
                    "default": row[3],
                    "primary_key": row[4],
                }
                for row in result
            ]


SQLConnector = PostgreSQLConnector
