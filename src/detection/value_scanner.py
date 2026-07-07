"""PII detection by heuristic analysis of column values.

Complements column name detection by scanning actual data
to identify emails, phone numbers, IPs, IBANs, person names, etc.
"""

import re
import unicodedata

VALUE_PATTERNS: dict[str, re.Pattern] = {
    "EMAIL": re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
    "SIRET": re.compile(r"(?<!\d)\d{3}\s?\d{3}\s?\d{3}\s?\d{5}(?!\d)"),
    "SIREN": re.compile(r"(?<!\d)\d{3}\s?\d{3}\s?\d{3}(?!\d)"),
    "PHONE": re.compile(r"\+?\d[\d\s\-().]{7,}"),
    "IP": re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"),
    "IBAN": re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{4,30}"),
    "CREDIT_CARD": re.compile(r"\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{1,4}"),
    "URL": re.compile(r"https?://\S+"),
}

_NUMERIC_OR_DATE = re.compile(
    r"^-?\d+([.,]\d+)?$"
    r"|^\d{4}-\d{2}-\d{2}"
    r"|^\d{2}[/.-]\d{2}[/.-]\d{4}"
)

_NAME_WORD = re.compile(
    r"^[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]{1,25}$"
    r"|^[A-ZÀ-ÖØ-Þ]{2,25}$"
    r"|^[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]+[-'][A-ZÀ-ÖØ-Þa-zà-öø-ÿ]+"
)

_COMMON_NON_NAMES = frozenset({
    "null", "none", "true", "false", "yes", "no", "ok",
    "active", "inactive", "pending", "done", "todo",
    "prospect", "client", "lead", "won", "lost",
    "email", "phone", "appel", "reunion", "prevu", "effectue",
    "entrant", "sortant", "linkedin", "site", "recommandation",
    "esn", "cabinet", "startup", "grand_groupe", "eti", "pme",
    "it", "finance", "industrie", "conseil", "sante",
    "test", "admin", "user", "manager", "data",
})


def _looks_like_name(value: str) -> bool:
    """Determine if a value looks like a person name using heuristics.

    Checks that the value contains capitalized words typical of a name,
    is not a common non-name word and does not contain digits.

    Args:
        value: String to evaluate.

    Returns:
        True if the value looks like a person name, False otherwise.
    """
    v = value.strip()
    if not v or len(v) < 2 or len(v) > 60:
        return False
    if v.lower() in _COMMON_NON_NAMES:
        return False
    if any(c.isdigit() for c in v):
        return False
    parts = re.split(r"[\s\-]+", v)
    if not parts:
        return False
    name_parts = sum(1 for p in parts if _NAME_WORD.match(p))
    return name_parts >= 1 and name_parts / len(parts) >= 0.5


def scan_values(values: list, threshold: float = 0.3) -> str | None:
    """Analyze a sample of values to detect a dominant PII type.

    Tests each value against regex patterns (email, phone, IP, IBAN, etc.)
    and the name detection heuristic. If a type exceeds the threshold, it is returned.

    Args:
        values: List of values to analyze (None and empty strings are ignored).
        threshold: Minimum proportion of matching values to trigger detection
                   (default: 0.3 = 30%).

    Returns:
        The detected PII type (e.g. 'EMAIL', 'PHONE', 'PERSON') or None if no type
        exceeds the threshold.
    """
    non_null = [str(v) for v in values if v is not None and str(v).strip()]
    if not non_null:
        return None

    for pii_type, pattern in VALUE_PATTERNS.items():
        candidates = non_null
        if pii_type in ("PHONE", "CREDIT_CARD", "SIRET", "SIREN"):
            candidates = [v for v in non_null if not _NUMERIC_OR_DATE.match(v.strip())]
            if not candidates:
                continue
        match_count = sum(1 for v in candidates if pattern.fullmatch(v.strip()))
        if match_count / len(candidates) >= threshold:
            return pii_type

    name_count = sum(1 for v in non_null if _looks_like_name(v))
    if non_null and name_count / len(non_null) >= threshold:
        return "PERSON"

    return None


_PG_KEY_VALUE = re.compile(r"\(([^)]+)\)=\(([^)]+)\)")

_NAME_LIKE = re.compile(r"\b[A-Z][a-zà-ÿ]{1,20}(?:\s+[A-Z][a-zà-ÿ]{1,20}){1,3}\b")


def scan_error_message(message: str) -> str:
    """Sanitize a SQL error message by masking potentially sensitive values.

    Replaces emails, phone numbers, IPs, URLs, PostgreSQL key values and person
    names with '[VALUE]' placeholders.

    Args:
        message: Raw error message to sanitize.

    Returns:
        The error message with sensitive values replaced by '[VALUE]'.
    """
    result = message
    for pii_type, pattern in VALUE_PATTERNS.items():
        result = pattern.sub("[VALUE]", result)
    result = _PG_KEY_VALUE.sub("([COLUMN])=([VALUE])", result)
    result = _NAME_LIKE.sub("[VALUE]", result)
    return result
