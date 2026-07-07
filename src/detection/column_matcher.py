"""PII column detection by pattern matching on column names.

Supports sensitivity classification (secret/confidential/public), manual overrides
and configurable glob pattern matching.
"""

from fnmatch import fnmatch

_BOOLEAN_TYPES = {"boolean", "bool", "bit"}

_SENSITIVITY_LEVELS = [
    ("secret", "SECRET"),
    ("confidential", "CONFIDENTIAL"),
    ("public", "SAFE"),
]


def resolve_sensitivity(column_name: str, sensitivity: dict | None) -> str | None:
    """Resolve the sensitivity level of a column based on configuration.

    Checks if the column name matches a pattern in the secret, confidential
    or public levels (in that priority order).

    Args:
        column_name: Column name to evaluate.
        sensitivity: Sensitivity level configuration dictionary
                     (e.g. {'secret': ['*password*'], 'public': ['id']}).

    Returns:
        The mapped type ('SECRET', 'CONFIDENTIAL' or 'SAFE') if a match is found,
        None otherwise.
    """
    if not sensitivity:
        return None
    col_lower = column_name.lower()
    for level, mapped_type in _SENSITIVITY_LEVELS:
        columns = sensitivity.get(level)
        if not isinstance(columns, list):
            continue
        for pattern in columns:
            if fnmatch(col_lower, pattern.lower()):
                return mapped_type
    return None


def match_column(column_name: str, patterns: dict[str, list[str]]) -> str | None:
    """Determine the PII type of a column by glob pattern matching.

    Args:
        column_name: Column name to check.
        patterns: Dict {PII_type: [pattern_list]} (e.g. {'EMAIL': ['*email*', '*mail*']}).

    Returns:
        The matching PII type (e.g. 'EMAIL', 'PHONE') if a pattern matches, None otherwise.
    """
    name_lower = column_name.lower()
    for pii_type, pattern_list in patterns.items():
        for pattern in pattern_list:
            if fnmatch(name_lower, pattern.lower()):
                return pii_type
    return None


def match_table_context(
    column_name: str,
    table_name: str,
    table_context: dict[str, dict] | None,
) -> str | None:
    """Check if a column should be classified as PII based on the table it belongs to.

    When a table name matches a sensitive context (e.g. ``*salary*``), generic
    column names (e.g. ``amount``, ``rate``) inherit the PII type.

    Args:
        column_name: Column name to evaluate.
        table_name: Full table name (may include schema prefix like ``employees.salary``).
        table_context: Config rules ``{PII_type: {tables: [...], columns: [...]}}``.

    Returns:
        The PII type if both table and column match a rule, None otherwise.
    """
    if not table_context:
        return None
    table_lower = table_name.lower()
    table_leaf = table_lower.rsplit(".", 1)[-1]
    col_lower = column_name.lower()
    for pii_type, rule in table_context.items():
        table_patterns = rule.get("tables", [])
        col_names = rule.get("columns", [])
        table_matches = any(
            fnmatch(table_lower, tp.lower()) or fnmatch(table_leaf, tp.lower())
            for tp in table_patterns
        )
        if not table_matches:
            continue
        if col_lower in (c.lower() for c in col_names):
            return pii_type.upper()
    return None


def detect_table_pii(
    columns: list[dict],
    patterns: dict[str, list[str]],
    overrides: dict[str, str] | None = None,
    sensitivity: dict | None = None,
    table_name: str | None = None,
    table_context: dict[str, dict] | None = None,
) -> dict[str, str]:
    """Detect PII columns in a SQL table schema.

    Applies in order: manual sensitivity, overrides, automatic patterns,
    then table-context rules for columns not yet classified.
    Boolean columns are skipped.

    Args:
        columns: List of dicts describing the schema (keys: 'name', 'type').
        patterns: Detection patterns {PII_type: [glob_patterns]}.
        overrides: Manual overrides {column_name: PII_type} (priority over patterns).
        sensitivity: Sensitivity configuration (highest priority).
        table_name: Table name for context-based detection.
        table_context: Table-context rules {PII_type: {tables: [...], columns: [...]}}.

    Returns:
        Dict {column_name: PII_type} for all columns detected as PII.
    """
    lower_overrides = {k.lower(): v for k, v in overrides.items()} if overrides else {}
    result = {}
    for col in columns:
        col_name = col["name"] if isinstance(col, dict) else col
        col_type = (col.get("type", "") if isinstance(col, dict) else "").lower()
        if col_type in _BOOLEAN_TYPES:
            continue
        sens_type = resolve_sensitivity(col_name, sensitivity)
        if sens_type:
            result[col_name] = sens_type
            continue
        col_lower = col_name.lower()
        if col_lower in lower_overrides:
            result[col_name] = lower_overrides[col_lower].upper()
            continue
        pii_type = match_column(col_name, patterns)
        if pii_type:
            result[col_name] = pii_type
            continue
        if table_name:
            ctx_type = match_table_context(col_name, table_name, table_context)
            if ctx_type:
                result[col_name] = ctx_type
    return result


def detect_mongo_pii(
    keys: list[str],
    patterns: dict[str, list[str]],
    overrides: dict[str, str] | None = None,
    sensitivity: dict | None = None,
) -> dict[str, str]:
    """Detect PII fields in a MongoDB collection schema.

    For nested keys (e.g. 'address.street'), only the last segment
    is used for pattern matching.

    Args:
        keys: List of MongoDB schema keys (supports dotted notation).
        patterns: Detection patterns {PII_type: [glob_patterns]}.
        overrides: Manual overrides {field_name: PII_type}.
        sensitivity: Sensitivity configuration (highest priority).

    Returns:
        Dict {field_name: PII_type} for all fields detected as PII.
    """
    lower_overrides = {k.lower(): v for k, v in overrides.items()} if overrides else {}
    result = {}
    for key in keys:
        leaf = key.rsplit(".", 1)[-1] if "." in key else key
        sens_type = resolve_sensitivity(leaf, sensitivity)
        if sens_type:
            result[key] = sens_type
            continue
        key_lower = key.lower()
        if key_lower in lower_overrides:
            result[key] = lower_overrides[key_lower].upper()
            continue
        pii_type = match_column(leaf, patterns)
        if pii_type:
            result[key] = pii_type
    return result
