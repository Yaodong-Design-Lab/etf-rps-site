#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANONICAL_SCRIPTS = ROOT / "scripts"
CANONICAL_FILE = CANONICAL_SCRIPTS / "build_etf_mobile_daily_site.py"


def _load_canonical_module():
    sys.path.insert(0, str(CANONICAL_SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "codex_canonical_build_etf_mobile_daily_site",
        CANONICAL_FILE,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load canonical script: {CANONICAL_FILE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_canonical_module()

INDEX_HTML = _MODULE.INDEX_HTML
build_payload = _MODULE.build_payload
main = _MODULE.main


if __name__ == "__main__":
    main()
