"""MySQL connector via SQLAlchemy with pymysql."""

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL

from src.database.sql_connector import BaseSQLConnector
from src.security.sql_validator import validate_identifier


class MySQLConnector(BaseSQLConnector):
    """MySQL connector with read-only transactions."""

    def _build_engine(self, config: dict) -> Engine:
        """Build the SQLAlchemy engine for MySQL via pymysql.

        Args:
            config: Configuration with keys user, password, host, port, database.

        Returns:
            SQLAlchemy Engine configured for MySQL with utf8mb4 charset.
        """
        url = URL.create(
            "mysql+pymysql",
            username=config["user"],
            password=config["password"],
            host=config["host"],
            port=config["port"],
            database=config["database"],
            query={"charset": "utf8mb4"},
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
        """Enable read-only mode via SET SESSION TRANSACTION READ ONLY.

        Args:
            conn: Active SQLAlchemy connection.

        Returns:
            None
        """
        conn.execute(text("SET SESSION TRANSACTION READ ONLY"))

    def list_tables(self) -> list[str]:
        """List BASE TABLE type tables from the current MySQL database.

        Returns:
            Sorted list of table names.
        """
        with self._engine.connect() as conn:
            result = conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            ))
            return [row[0] for row in result]

    def get_table_schema(self, table_name: str) -> list[dict]:
        """Retrieve the schema of a MySQL table via information_schema.

        Args:
            table_name: Table name.

        Returns:
            List of dicts with keys: name, type, nullable, default, primary_key.
        """
        with self._engine.connect() as conn:
            result = conn.execute(text("""
                SELECT
                    c.COLUMN_NAME,
                    c.DATA_TYPE,
                    c.IS_NULLABLE,
                    c.COLUMN_DEFAULT,
                    CASE WHEN c.COLUMN_KEY = 'PRI' THEN 1 ELSE 0 END as is_primary_key
                FROM information_schema.columns c
                WHERE c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = :table
                ORDER BY c.ORDINAL_POSITION
            """), {"table": table_name})
            return [
                {
                    "name": row[0],
                    "type": row[1],
                    "nullable": row[2] == "YES",
                    "default": row[3],
                    "primary_key": bool(row[4]),
                }
                for row in result
            ]

    def sample_values(self, table_name: str, column_name: str, limit: int = 50) -> list:
        """Sample distinct non-null values from a MySQL column.

        Args:
            table_name: Table name (validated against SQL injection).
            column_name: Column name (validated against SQL injection).
            limit: Maximum number of values (default: 50).

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
