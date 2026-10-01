"""Operator home and the kernel directory on sys.path."""
import os
import sys
from pathlib import Path


def _bootstrap() -> None:
    """Put the kernel directory on sys.path. Idempotent."""
    import operator_kernel
    origin = getattr(operator_kernel, "__file__", None)
    directory = str(Path(origin).resolve().parent) if origin else ""
    if directory and directory not in sys.path:
        sys.path.insert(0, directory)


def _home(given: "str | None") -> Path:
    """--home, else COPILOT_OPERATOR_HOME, else ~/.operator."""
    if given:
        return Path(given).expanduser()
    override = os.environ.get("COPILOT_OPERATOR_HOME")
    return Path(override) if override else Path.home() / ".operator"


def _settle_home(given: "str | None") -> Path:
    """Resolve the home and export it so child processes agree."""
    os.environ["COPILOT_OPERATOR_HOME"] = str(home := _home(given))
    return home
