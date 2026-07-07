# Data Detection and Anonymization

## Detected Data Types

### Classic PII (Faker pseudonymization)

| Type | Column Patterns | Value Regex |
|---|---|---|
| FIRST_NAME | `first_name*`, `firstname*`, `prenom` | - |
| LAST_NAME | `last_name*`, `lastname*`, `nom_famille` | - |
| PERSON | `*name*`, `nom` | - |
| EMAIL | `*email*`, `*mail*`, `courriel` | `[a-zA-Z0-9._%+-]+@[a-zA-Z0-9]+` |
| PHONE | `*phone*`, `*tel*`, `*mobile*`, `*fax*` | `\+?\d[\d\s\-().]{7,}` |
| ADDRESS | `*address*`, `*street*`, `*zip*`, `*postal*` | - |
| CITY | `*city*`, `*ville*` | - |
| COUNTRY | `*country*`, `*pays*` | - |
| CREDENTIAL | `*password*`, `*username*`, `*secret*`, `*token*` | - |
| SSN | `*ssn*`, `*social_security*`, `*num_secu*` | - |
| FINANCIAL | `*iban*`, `*credit_card*`, `*card_number*` | IBAN / credit card |
| IP | `*ip_address*`, `*ip_addr*` | `\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}` |
| GEO | `*latitude*`, `*longitude*`, `*geo_*` | - |
| URL | `*_url`, `*website*`, `*avatar*` | `https?://\S+` |
| DATE_OF_BIRTH | `*birth*`, `*dob*`, `*naissance*` | - |

### Sensitive Business Data (numeric perturbation)

| Type | Column Patterns | Strategy |
|---|---|---|
| SALARY | `*salary*`, `*salaire*`, `*wage*`, `*compensation*` | ±15% |
| REVENUE | `*revenue*`, `*margin*`, `*marge*`, `*profit*`, `*turnover*` | ±15% |
| SCORE | `*_score*`, `*credit_score*`, `*fraud_score*`, `*rating*` | ±10% |
| PRICE | `*unit_price*`, `*prix*`, `*discount_rate*`, `*taux_*` | ±15% |

## Detection Pipeline (5 layers)

1. **Layer 0 - Manual classification**: `secret` → redacted, `confidential` → perturbed, `public` → never anonymized
2. **Layer 1 - Pattern matching**: column names compared against patterns from `config.yaml` (`*email*` → EMAIL, etc.)
3. **Layer 2 - Value scan (describeTable)**: regex on a sample of values to catch poorly named columns
4. **Layer 3 - SQL lineage**: tracing via sqlglot - resolves aliases, SQL functions, subqueries and UNIONs back to source columns
5. **Layer 4 - Fallback value scan**: scan on query results to catch PII that escaped previous layers

## Manual Classification (optional)

In addition to auto-detection, you can configure sensitivity levels in `config.yaml`:

```yaml
detection:
  sensitivity:
    secret:                    # → always [REDACTED]
     - password_hash
     - api_key
    confidential:              # → ±15% perturbation (numbers) or [REDACTED] (text)
     - internal_margin
     - acquisition_cost
    public:                    # → never anonymized, even if detected as PII
     - id
     - created_at
     - status
```

Priority: `secret` > `confidential` > `public` > overrides > patterns > value scan.

Patterns are extensible via `config.yaml`. An override system allows forcing or excluding specific columns.

## Deterministic Pseudonymization

Faker with a seed based on `hash(value + session_salt)` - same query = same pseudonyms within a session. Cross-query consistency depends on the mapping mode (see [CONFIGURATION.md](CONFIGURATION.md#mapping-modes)).

## Debugging with Anonymized Data - Does It Work?

Yes. An AI agent doesn't need real personal data to fix a bug. What matters:

- **Structure** - column types, relationships, foreign keys → pass through **intact**
- **Consistency** - thanks to the deterministic seed, the same person is always replaced by the same pseudonym. Joins, duplicates and inconsistencies remain **visible**
- **Error patterns** - unexpected `NULL`, violated constraint, incorrect type, empty join → **preserved**
- **Non-PII data** - IDs, amounts, dates, statuses, booleans → pass through **in plaintext**

**Limitations**: a bug related to specific text content (e.g., a special character in an email that breaks a parser) won't be reproducible. These cases remain rare.
