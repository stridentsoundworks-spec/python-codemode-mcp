"""Environment configuration loader."""

import os


def get_env(key: str, default: str = "") -> str:
    """Get an environment variable with optional default."""
    return os.getenv(key, default)
