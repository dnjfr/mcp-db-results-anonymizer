"""Trace SQL column lineage: for each result column, find the source column
(table.column) to propagate PII detection even through aliases,
SQL functions (UPPER, TRIM, CAST...) and concatenations."""

from __future__ import annotations

import sqlglot
from sqlglot import expressions as exp

_NUMERIC_WINDOW_FUNCTIONS = frozenset({
    "ROWNUMBER", "RANK", "DENSERANK", "NTILE",
    "CUMEDIST", "PERCENTRANK",
    "COUNT", "SUM", "AVG",
})


def _is_numeric_window(node: exp.Expression) -> bool:
    """Detect whether a node is a purely numeric window function.

    These functions (ROW_NUMBER, RANK, COUNT...) produce numeric values
    that do not contain PII data from columns referenced in
    ORDER BY / PARTITION BY.

    Args:
        node: sqlglot AST node to check.

    Returns:
        True if the node is a numeric window function, False otherwise.
    """
    if not isinstance(node, exp.Window):
        return False
    func = node.this
    if isinstance(func, exp.Anonymous):
        return func.name.upper() in _NUMERIC_WINDOW_FUNCTIONS
    return type(func).__name__.upper() in _NUMERIC_WINDOW_FUNCTIONS


def _collect_source_columns(node: exp.Expression) -> set[str]:
    """Recursively traverse a SQL expression and collect all source column names.

    Args:
        node: sqlglot AST node to traverse.

    Returns:
        Set of referenced source column names (lowercase).
    """
    if _is_numeric_window(node):
        return set()
    sources: set[str] = set()
    if isinstance(node, exp.Column):
        sources.add(node.name.lower())
    for child in node.iter_expressions():
        sources.update(_collect_source_columns(child))
    return sources


_DIALECT_MAP = {
    "postgresql": "postgres",
    "mssql": "tsql",
}


def trace_column_lineage(
    query: str,
    dialect: str = "postgres",
) -> dict[str, set[str]]:
    """Parse a SELECT query and trace result column lineage back to source columns.

    Propagates PII detection through aliases, SQL functions
    (UPPER, TRIM, CAST...) and concatenations.

    Args:
        query: SQL SELECT query to analyze.
        dialect: SQL dialect for parsing ('postgresql', 'mysql', 'mssql').

    Returns:
        Dict {result_column_name: {source_columns}}.
        E.g. {"fn": {"first_name"}, "c": {"a", "b"}}.
        Returns an empty dict on parsing error.
    """
    dialect = _DIALECT_MAP.get(dialect, dialect)
    mapping: dict[str, set[str]] = {}

    try:
        parsed = sqlglot.parse(query, read=dialect)
    except sqlglot.errors.ParseError:
        return mapping

    if not parsed:
        return mapping

    statement = parsed[0]
    if not isinstance(statement, exp.Select):
        selects = list(statement.find_all(exp.Select))
        if selects:
            statement = selects[0]
        else:
            return mapping

    for i, select_expr in enumerate(statement.expressions):
        if isinstance(select_expr, exp.Alias):
            output_name = select_expr.alias.lower()
            sources = _collect_source_columns(select_expr.this)
        elif isinstance(select_expr, exp.Column):
            output_name = select_expr.name.lower()
            sources = {output_name}
        elif isinstance(select_expr, exp.Star):
            continue
        else:
            output_name = f"_expr_{i}"
            sources = _collect_source_columns(select_expr)

        if sources:
            mapping[output_name] = sources

    return mapping
