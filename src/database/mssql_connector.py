"""Microsoft SQL Server connector via SQLAlchemy and pyodbc."""

from urllib.parse import quote_plus

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

from src.database.sql_connector import BaseSQLConnector
from src.security.sql_validator import validate_identifier


class MSSQLConnector(BaseSQLConnector):
    """MSSQL connector with read-only connection at the pyodbc level."""

    def _build_engine(self, config: dict) -> Engine:
        """Build the SQLAlchemy engine for MSSQL via pyodbc.

        Configures the connection in read-only mode via a listener on the
        SQLAlchemy 'connect' event.

        Args:
            config: Configuration with keys user, password, host, port, database
                    and optionally driver (default: 'ODBC Driver 18 for SQL Server').

        Returns:
            SQLAlchemy Engine configured for MSSQL.
        """
        user = config["user"]
        password = config["password"]
        host = config["host"]
        port = config["port"]
        database = config["database"]
        driver = config.get("driver", "ODBC Driver 18 for SQL Server")
        params = quote_plus(
            f"DRIVER={{{driver}}};SERVER={host},{port};DATABASE={database};"
            f"UID={user};PWD={password};TrustServerCertificate=yes"
        )
        url = f"mssql+pyodbc:///?odbc_connect={params}"
        engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_timeout=30,
            pool_recycle=1800,
        )

        @event.listens_for(engine, "connect")
        def _set_pyodbc_readonly(dbapi_conn, connection_record):
            """SQLAlchemy listener that forces read-only mode at the pyodbc level.

            Args:
                dbapi_conn: Raw pyodbc connection.
                connection_record: SQLAlchemy connection record (unused).

            Returns:
                None
            """
            dbapi_conn.readonly = True

        return engine

    def _set_read_only(self, conn) -> None:
        """No-op: read-only is handled at the pyodbc level in _build_engine.

        Args:
            conn: Active SQLAlchemy connection (unused).

        Returns:
            None
        """
        pass

    def list_tables(self) -> list[str]:
        """List user (non-system) tables from the MSSQL database.

        Returns:
            Sorted list of table names.
        """
        with self._engine.connect() as conn:
            result = conn.execute(text(
                "SELECT t.name FROM sys.tables t "
                "WHERE t.is_ms_shipped = 0 "
                "ORDER BY t.name"
            ))
            return [row[0] for row in result]

    def get_table_schema(self, table_name: str) -> list[dict]:
        """Retrieve the schema of a MSSQL table via INFORMATION_SCHEMA ('dbo' schema).

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
                    CASE WHEN pk.COLUMN_NAME IS NOT NULL THEN 1 ELSE 0 END as is_primary_key
                FROM INFORMATION_SCHEMA.COLUMNS c
                LEFT JOIN (
                    SELECT ku.COLUMN_NAME
                    FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                    JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku
                        ON tc.CONSTRAINT_NAME = ku.CONSTRAINT_NAME
                    WHERE tc.TABLE_NAME = :table AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
                ) pk ON c.COLUMN_NAME = pk.COLUMN_NAME
                WHERE c.TABLE_NAME = :table AND c.TABLE_SCHEMA = 'dbo'
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
        """Sample distinct non-null values from a MSSQL column via TOP.

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
                f"SELECT DISTINCT TOP(:limit) {safe_column} FROM {safe_table} "
                f"WHERE {safe_column} IS NOT NULL"
            ), {"limit": limit})
            return [row[0] for row in result]
