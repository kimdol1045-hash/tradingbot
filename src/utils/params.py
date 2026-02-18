"""
Agent parameter loading from JSON files.
Supports atomic write (temp→rename) for Evolver updates.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from src.utils.config import PARAMS_DIR

logger = logging.getLogger(__name__)


def load_params(agent_id: str) -> dict:
    """Load params.json for an agent."""
    path = PARAMS_DIR / agent_id / "params.json"
    if not path.exists():
        raise FileNotFoundError(f"Params not found: {path}")
    with open(path) as f:
        return json.load(f)


def save_params(agent_id: str, params: dict) -> None:
    """
    Atomically save params.json for an agent.
    Uses temp file + os.rename() to prevent race conditions.
    """
    target = PARAMS_DIR / agent_id / "params.json"
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(target.parent), suffix=".tmp", prefix="params_"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(params, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.rename(tmp_path, str(target))
        logger.info("Params saved: %s", target)
    except Exception:
        os.unlink(tmp_path)
        raise


def load_all_params() -> dict[str, dict]:
    """Load params for all agents."""
    result = {}
    for agent_dir in sorted(PARAMS_DIR.iterdir()):
        if agent_dir.is_dir() and (agent_dir / "params.json").exists():
            agent_id = agent_dir.name
            result[agent_id] = load_params(agent_id)
    return result
