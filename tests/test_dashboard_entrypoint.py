"""Regression test for Streamlit's file-based dashboard import path."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path


def test_dashboard_analytics_import_resolves_without_repository_root(monkeypatch):
    """Streamlit adds the script directory, not necessarily its parent, to sys.path."""
    root = Path(__file__).resolve().parents[1]
    dashboard_dir = root / "dashboard"
    app_file = dashboard_dir / "app.py"
    tree = ast.parse(app_file.read_text(encoding="utf-8"))

    analytics_imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and (node.module == "analytics_ui" or node.module.endswith(".analytics_ui"))
    ]
    assert len(analytics_imports) == 1

    # Reproduce Streamlit's script-directory path while excluding the repo root.
    monkeypatch.setattr(
        sys,
        "path",
        [str(dashboard_dir)] + [
            path for path in sys.path
            if path and Path(path).resolve() not in {root, dashboard_dir}
        ],
    )
    spec = importlib.util.find_spec(analytics_imports[0])
    assert spec is not None and spec.origin is not None
    assert Path(spec.origin).resolve() == (dashboard_dir / "analytics_ui.py").resolve()
