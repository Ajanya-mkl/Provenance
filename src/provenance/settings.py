"""Non-secret project settings from config/config.example.yaml."""

import os
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "config.example.yaml"
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def load_project_config(config_path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    with Path(config_path).open(encoding="utf-8") as config_file:
        return yaml.safe_load(config_file)


def usaspending_offline(config_path: str | Path = CONFIG_PATH) -> bool:
    """Whether to load the cached USASpending sample instead of calling the API.

    PROVENANCE_USASPENDING_OFFLINE overrides the config, so CI can pin the
    sample without editing a tracked file.
    """
    override = os.environ.get("PROVENANCE_USASPENDING_OFFLINE")
    if override is not None:
        value = override.strip().lower()
        if value not in TRUE_VALUES | FALSE_VALUES:
            raise ValueError(
                f"PROVENANCE_USASPENDING_OFFLINE must be true or false, got {override!r}"
            )
        return value in TRUE_VALUES
    setting = load_project_config(config_path).get("ingestion", {}).get(
        "usaspending_offline", False
    )
    if not isinstance(setting, bool):
        raise ValueError(f"ingestion.usaspending_offline must be true or false, got {setting!r}")
    return setting
