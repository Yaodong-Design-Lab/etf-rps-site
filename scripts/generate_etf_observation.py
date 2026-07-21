#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANONICAL_SCRIPTS = ROOT / "scripts"
CANONICAL_FILE = CANONICAL_SCRIPTS / "generate_etf_observation.py"


def _load_canonical_module():
    sys.path.insert(0, str(CANONICAL_SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "codex_canonical_generate_etf_observation",
        CANONICAL_FILE,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load canonical script: {CANONICAL_FILE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_canonical_module()

simplify_name = _MODULE.simplify_name
theme_of = _MODULE.theme_of
sub_theme_of = _MODULE.sub_theme_of
dedupe_by_theme = _MODULE.dedupe_by_theme
main = getattr(_MODULE, "main", None)


if __name__ == "__main__" and main is not None:
    main()
