"""SQL query security validation.

Blocks write queries, dialect-specific dangerous functions
and access to sensitive system tables.
"""

import re

_WRITE_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|COPY|CREATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)

_DANGEROUS_FUNCTIONS = {
    "postgresql": re.compile(
        r"\b(pg_read_file|pg_read_binary_file|lo_import|lo_export|"
        r"pg_execute_server_program|dblink|encode|convert_from|convert_to)\s*\(",
        re.IGNORECASE,
    ),
    "mysql": re.compile(
        r"\b(LOAD_FILE|INTO\s+OUTFILE|INTO\s+DUMPFILE|BENCHMARK|SLEEP)\s*\(",
        re.IGNORECASE,
    ),
    "mssql": re.compile(
        r"\b(xp_cmdshell|xp_regread|xp_regwrite|xp_dirtree|xp_fileexist|"
        r"OPENROWSET|OPENDATASOURCE|sp_configure|sp_addextendedproc)\s*\(?",
        re.IGNORECASE,
    ),
}

_BLOCKED_TABLES = {
    "postgresql": [
        "pg_shadow", "pg_authid", "pg_roles", "pg_stat_activity",
        "pg_user", "pg_settings", "pg_class", "pg_proc", "pg_namespace",
        "information_schema",
    ],
    "mysql": [
        "mysql.user", "mysql.db", "mysql.tables_priv", "mysql.columns_priv",
        "mysql.global_grants", "mysql.password_history",
        "performance_schema", "mysql.slow_log", "mysql.general_log",
    ],
    "mssql": [
        "sys.sql_logins", "sys.syslogins", "sys.server_principals",
        "sys.credentials", "sys.configurations", "sys.dm_exec_connections",
        "sys.linked_logins", "sys.asymmetric_keys", "sys.symmetric_keys",
    ],
}

_SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _strip_string_literals(query: str) -> str:
    """Strip comments and string literals from a SQL query.

    Required to avoid false positives during validation
    (e.g. an INSERT keyword inside a string literal).

    Args:
        query: Raw SQL query.

    Returns:
        The cleaned query with comments and strings replaced by placeholders.
    """
    result = re.sub(r"--[^\n]*", " ", query)
    result = re.sub(r"/\*.*?\*/", " ", result, flags=re.DOTALL)
    result = re.sub(r"'(?:[^']|'')*'", "''", result)
    result = re.sub(r'"(?:[^"\\]|\\.)*"', '""', result)
    return result


def validate_identifier(name: str) -> str:
    """Validate that a SQL identifier contains only safe characters.

    Supports schema-qualified names (e.g. 'employees.salary').
    Each segment is validated individually.

    Args:
        name: SQL identifier name to validate (e.g. 'table' or 'schema.table').

    Returns:
        The validated name, unchanged.

    Raises:
        ValueError: If any segment contains non-alphanumeric/underscore characters.
    """
    parts = name.split(".")
    if len(parts) > 2:
        raise ValueError(f"Identifiant SQL invalide: {name}")
    for part in parts:
        if not _SAFE_IDENTIFIER.match(part):
            raise ValueError(f"Identifiant SQL invalide: {name}")
    return name


def validate_query(
    query: str,
    blocked_tables: list[str] | None = None,
    blocked_functions: list[str] | None = None,
    dialect: str = "postgresql",
) -> tuple[bool, str | None]:
    """Validate a SQL query against security constraints.

    Checks in order: non-empty query, no multi-statements, no write keywords,
    no dangerous functions (dialect-specific) and no access to blocked system tables.

    Args:
        query: SQL query to validate.
        blocked_tables: Additional tables to block (on top of default system tables).
        blocked_functions: Currently unused (reserved for future extension).
        dialect: SQL dialect ('postgresql', 'mysql', 'mssql') for dialect-specific rules.

    Returns:
        Tuple (valid, reason) - (True, None) if the query is allowed,
        (False, error_message) otherwise.
    """
    stripped = query.strip()
    if not stripped:
        return False, "Requête vide"

    cleaned = _strip_string_literals(stripped)

    parts = cleaned.split(";")
    non_empty = [p.strip() for p in parts if p.strip()]
    if len(non_empty) > 1:
        return False, "Multi-statements interdits"

    match = _WRITE_KEYWORDS.search(cleaned)
    if match:
        return False, f"Opération d'écriture interdite: {match.group(1).upper()}"

    func_pattern = _DANGEROUS_FUNCTIONS.get(dialect)
    if func_pattern:
        match = func_pattern.search(cleaned)
        if match:
            return False, f"Fonction dangereuse interdite: {match.group(1)}"

    default_blocked = _BLOCKED_TABLES.get(dialect, [])
    all_blocked = list(set(default_blocked + (blocked_tables or [])))
    for table in all_blocked:
        pattern = re.compile(rf"\b{re.escape(table)}\b", re.IGNORECASE)
        if pattern.search(cleaned):
            return False, f"Table système interdite: {table}"

    return True, None
