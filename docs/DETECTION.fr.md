# Détection et anonymisation des données

## Types de données détectés

### PII classiques (pseudonymisation Faker)

| Type | Patterns de colonnes | Regex sur valeurs |
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
| FINANCIAL | `*iban*`, `*credit_card*`, `*card_number*` | IBAN / carte de crédit |
| IP | `*ip_address*`, `*ip_addr*` | `\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}` |
| GEO | `*latitude*`, `*longitude*`, `*geo_*` | - |
| URL | `*_url`, `*website*`, `*avatar*` | `https?://\S+` |
| DATE_OF_BIRTH | `*birth*`, `*dob*`, `*naissance*` | - |

### Données business sensibles (perturbation numérique)

| Type | Patterns de colonnes | Stratégie |
|---|---|---|
| SALARY | `*salary*`, `*salaire*`, `*wage*`, `*compensation*` | ±15% |
| REVENUE | `*revenue*`, `*margin*`, `*marge*`, `*profit*`, `*turnover*` | ±15% |
| SCORE | `*_score*`, `*credit_score*`, `*fraud_score*`, `*rating*` | ±10% |
| PRICE | `*unit_price*`, `*prix*`, `*discount_rate*`, `*taux_*` | ±15% |

## Pipeline de détection (5 couches)

1. **Couche 0 - Classification manuelle** : `secret` → masqué, `confidential` → perturbé, `public` → jamais anonymisé
2. **Couche 1 - Pattern matching** : noms de colonnes comparés aux patterns de `config.yaml` (`*email*` → EMAIL, etc.)
3. **Couche 2 - Value scan (describeTable)** : regex sur un échantillon de valeurs pour rattraper les colonnes mal nommées
4. **Couche 3 - Lignée SQL** : traçage via sqlglot - remonte des alias, fonctions SQL, sous-requêtes et UNION vers les colonnes sources
5. **Couche 4 - Fallback value scan** : scan sur les résultats de requête pour rattraper les PII qui ont échappé aux couches précédentes

## Classification manuelle (optionnelle)

En plus de l'auto-détection, vous pouvez configurer des niveaux de sensibilité dans `config.yaml` :

```yaml
detection:
  sensitivity:
    secret:                    # → toujours [REDACTED]
     - password_hash
     - api_key
    confidential:              # → perturbation ±15% (nombres) ou [REDACTED] (texte)
     - internal_margin
     - acquisition_cost
    public:                    # → jamais anonymisé, même si détecté comme PII
     - id
     - created_at
     - status
```

Priorité : `secret` > `confidential` > `public` > overrides > patterns > value scan.

Les patterns sont extensibles via `config.yaml`. Un système d'overrides permet de forcer ou exclure des colonnes spécifiques.

## Pseudonymisation déterministe

Faker avec seed basé sur `hash(valeur + sel_session)` - même requête = mêmes pseudonymes dans une session. La cohérence entre requêtes dépend du mode de mapping (voir [CONFIGURATION.md](CONFIGURATION.md#modes-de-mapping)).

## Débugger avec des données anonymisées - est-ce que ça marche ?

Oui. Un agent IA n'a pas besoin des vraies données personnelles pour corriger un bug. Ce qui compte :

- **La structure** - types de colonnes, relations, clés étrangères → passent **intacts**
- **La cohérence** - grâce au seed déterministe, la même personne est toujours remplacée par le même pseudonyme. Les jointures, doublons et incohérences restent **visibles**
- **Les patterns d'erreur** - `NULL` inattendu, contrainte violée, type incorrect, jointure vide → **préservés**
- **Les données non-PII** - IDs, montants, dates, statuts, booléens → passent **en clair**

**Limites** : un bug lié à un contenu textuel précis (ex: un caractère spécial dans un email qui casse un parser) ne sera pas reproductible. Ces cas restent rares.
