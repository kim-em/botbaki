"""Claude quota checking for review generation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Literal

QuotaModel = Literal["opus", "sonnet"]


def check_claude_quota(verbose: bool = False) -> Optional[QuotaModel]:
    """
    Check Claude CLI quota availability.

    Uses the claude-available-model script copied to botbaki/bin/

    Returns:
        "opus" if Opus quota available
        "sonnet" if only Sonnet quota available
        None if no quota available
    """
    script_path = Path(__file__).parent.parent / "bin" / "claude-available-model"

    if not script_path.exists():
        if verbose:
            print("Warning: claude-available-model script not found", file=sys.stderr)
        return None

    try:
        result = subprocess.run(
            [str(script_path)],
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode == 0:
            model = result.stdout.strip()
            if model in ("opus", "sonnet"):
                return model
        return None
    except Exception as e:
        if verbose:
            print(f"Error checking quota: {e}", file=sys.stderr)
        return None


def has_anthropic_api_key() -> bool:
    """Check if ANTHROPIC_API_KEY environment variable is set."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))
