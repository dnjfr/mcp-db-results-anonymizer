"""YAML configuration loading and resolution with environment variable support."""

import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")
_config_cache: dict | None = None


def _resolve_env_vars(value):
    """Recursively resolve ${VAR:-default} references in configuration values.

    Args:
        value: Value to resolve - can be a string, dict, list or scalar.

    Returns:
        The value with all environment variable references replaced
        by their actual value or default.

    Raises:
        ValueError: If a referenced environment variable does not exist and has no default value.
    """
    if isinstance(value, str):
        def replacer(match):
            """Replace a ${VAR:-default} match with the environment value or fallback.

            Args:
                match: re.Match object containing groups (variable_name, default_value).

            Returns:
                The resolved environment variable value.

            Raises:
                ValueError: If the variable does not exist and has no default.
            """
            var_name = match.group(1)
            default = match.group(2)
            env_val = os.environ.get(var_name)
            if env_val is not None:
                return env_val
            if default is not None:
                return default
            raise ValueError(f"Variable d'environnement manquante: {var_name}")
        return _ENV_VAR_PATTERN.sub(replacer, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


def load_config(path: str | None = None) -> dict:
    """Load configuration from a YAML file and resolve environment variables.

    Loads the .env file from ~/.mcp-db-results-anonymizer/.env if it exists,
    then reads the specified YAML file. The result is cached for subsequent calls.

    Args:
        path: Path to the YAML file. If None, uses the MCP_ANON_CONFIG
              environment variable or 'config.yaml' by default.

    Returns:
        The configuration dictionary with environment variables resolved.

    Raises:
        FileNotFoundError: If the configuration file does not exist.
    """
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    secure_env = Path("~/.mcp-db-results-anonymizer/.env").expanduser()
    if secure_env.exists():
        load_dotenv(secure_env, override=True)
    else:
        load_dotenv(override=True)

    if path is None:
        default_path = str(Path("~/.mcp-db-results-anonymizer/config.yaml").expanduser())
        path = os.environ.get("MCP_ANON_CONFIG", default_path)

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Fichier de configuration introuvable: {path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    _config_cache = _resolve_env_vars(raw)
    return _config_cache


def reset_config():
    """Reset the configuration cache, forcing a reload on the next load_config call.

    Returns:
        None
    """
    global _config_cache
    _config_cache = None
