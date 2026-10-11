"""Logging in the '[LEVEL] message' format required by the specification."""
from __future__ import annotations

import logging
import sys

LOGGER = logging.getLogger("rawxmp")


def configure(verbose: bool = False, quiet: bool = False, log_file: str | None = None) -> logging.Logger:
    for stream in (sys.stdout, sys.stderr):          # cp1252/cp850 consoles cannot encode "→", "σ", "−"
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    LOGGER.handlers.clear()
    LOGGER.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("[%(levelname)s] %(message)s")
    sh = logging.StreamHandler(sys.stderr if quiet else sys.stdout)
    sh.setFormatter(fmt)
    if quiet:
        sh.setLevel(logging.ERROR)
    LOGGER.addHandler(sh)
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        LOGGER.addHandler(fh)
    LOGGER.propagate = False
    return LOGGER
