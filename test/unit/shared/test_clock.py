# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import ast
from datetime import timezone
from pathlib import Path

from src.shared.clock import utc_now

SRC_ROOT = Path(__file__).resolve().parents[3] / "src"
CLOCK_MODULE = SRC_ROOT / "shared" / "clock.py"


def test_utc_now_is_timezone_aware_utc():
    now = utc_now()

    assert now.tzinfo is not None
    assert now.utcoffset() == timezone.utc.utcoffset(None)


def _naive_now_calls(tree: ast.AST) -> bool:
    """True when the module calls ``datetime.now()`` / ``datetime.datetime.now()`` with no
    timezone argument, which yields a naive local-time value."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or node.args or node.keywords:
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "now":
            continue
        base = func.value
        base_name = base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute) else ""
        if base_name == "datetime":
            return True
    return False


def test_no_source_file_builds_a_naive_now():
    """Every timestamp column is ``timestamptz``. A bare ``datetime.now()`` yields the host's
    local wall clock, which shifts any window computed from it by the host's UTC offset -
    correct only on a UTC host. ``utc_now`` is the single source of "now"."""
    offenders = [
        path.relative_to(SRC_ROOT).as_posix()
        for path in SRC_ROOT.rglob("*.py")
        if path != CLOCK_MODULE and _naive_now_calls(ast.parse(path.read_text(encoding="utf-8")))
    ]

    assert offenders == []
