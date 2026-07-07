"""Consistent pseudonymized value generation from real data.

Uses Faker with a deterministic seed based on a SHA-256 hash to ensure
the same source value always produces the same pseudonym within a given session.
"""

import hashlib
import random
import uuid
from decimal import Decimal

from faker import Faker

_faker_cache: dict[str, Faker] = {}

_GENERATORS: dict[str, str] = {
    "PERSON": "name",
    "FIRST_NAME": "first_name",
    "LAST_NAME": "last_name",
    "EMAIL": "email",
    "PHONE": "phone_number",
    "ADDRESS": "address",
    "CITY": "city",
    "COUNTRY": "country",
    "IP": "ipv4",
    "IBAN": "iban",
    "FINANCIAL": "credit_card_number",
    "GEO": "latitude",
    "URL": "url",
    "SSN": "ssn",
    "CREDENTIAL": "password",
    "DATE_OF_BIRTH": "date_of_birth",
}

_NUMERIC_TYPES: dict[str, float] = {
    "SALARY": 0.15,
    "REVENUE": 0.15,
    "SCORE": 0.10,
    "PRICE": 0.15,
    "CONFIDENTIAL": 0.15,
}


def _generate_siren(rng: random.Random) -> str:
    """Generate a fake 9-digit SIREN number.

    Args:
        rng: Seeded random generator to ensure determinism.

    Returns:
        9-digit string representing a fake SIREN.
    """
    return "".join(str(rng.randint(0, 9)) for _ in range(9))


def _generate_siret(rng: random.Random) -> str:
    """Generate a fake 14-digit SIRET number (SIREN + NIC).

    Args:
        rng: Seeded random generator to ensure determinism.

    Returns:
        14-digit string representing a fake SIRET.
    """
    return _generate_siren(rng) + "".join(str(rng.randint(0, 9)) for _ in range(5))


def generate_session_salt() -> str:
    """Generate a unique salt for the pseudonymization session.

    Returns:
        A random 32-character hexadecimal string (UUID v4).
    """
    return uuid.uuid4().hex


def _perturb_numeric(value, session_salt: str, noise_pct: float = 0.15):
    """Perturb a numeric value by adding deterministic noise.

    The noise is based on a SHA-256 hash of the value and session salt,
    ensuring reproducible perturbation for the same input.

    Args:
        value: Numeric value to perturb (int, float or Decimal).
        session_salt: Session salt for deterministic hashing.
        noise_pct: Maximum noise percentage to apply (default: 0.15 = +/-15%).

    Returns:
        The perturbed value, in the same type as the input (int, Decimal or float).
        Returns "[REDACTED]" if the value cannot be converted to a number.
    """
    try:
        num_val = float(value)
    except (ValueError, TypeError):
        return "[REDACTED]"

    if num_val == 0:
        return value

    str_value = str(value)
    seed = int(
        hashlib.sha256(f"{str_value}{session_salt}".encode()).hexdigest(), 16
    ) % (10**9)
    rng = random.Random(seed)
    factor = 1 + rng.uniform(-noise_pct, noise_pct)
    result = num_val * factor

    if isinstance(value, int):
        return int(round(result))
    if isinstance(value, Decimal):
        return Decimal(str(round(result, 2)))
    return round(result, 2)


def pseudonymize(real_value, entity_type: str, session_salt: str, locale: str = "fr_FR"):
    """Replace a real value with a consistent fake value based on the PII type.

    Uses Faker with a deterministic seed to generate reproducible pseudonyms
    within the same session.

    Args:
        real_value: Real value to pseudonymize.
        entity_type: PII type (e.g. 'PERSON', 'EMAIL', 'PHONE', 'SECRET', 'SALARY').
        session_salt: Session salt for deterministic hashing.
        locale: Faker locale for fake data generation (default: 'fr_FR').

    Returns:
        The pseudonymized value as a string, the original value if empty/None,
        or "[REDACTED]" for SECRET or unknown types.
    """
    if real_value is None or (isinstance(real_value, str) and not real_value.strip()):
        return real_value

    if entity_type == "SECRET":
        return "[REDACTED]"

    noise_pct = _NUMERIC_TYPES.get(entity_type)
    if noise_pct is not None:
        return _perturb_numeric(real_value, session_salt, noise_pct)

    str_value = str(real_value)
    seed = int(
        hashlib.sha256(f"{str_value}{session_salt}".encode()).hexdigest(), 16
    ) % (10**9)

    if entity_type == "SIRET":
        return _generate_siret(random.Random(seed))
    if entity_type == "SIREN":
        return _generate_siren(random.Random(seed))

    fake = _faker_cache.get(locale)
    if fake is None:
        fake = Faker(locale)
        _faker_cache[locale] = fake
    fake.seed_instance(seed)

    method_name = _GENERATORS.get(entity_type)
    if method_name and hasattr(fake, method_name):
        result = getattr(fake, method_name)()
        if entity_type == "GEO":
            return str(round(float(result), 4))
        if entity_type == "DATE_OF_BIRTH":
            return str(result)
        return str(result) if not isinstance(result, str) else result

    return "[REDACTED]"
