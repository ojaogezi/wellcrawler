"""Shared fixtures: a tiny, fully-deterministic messy archive in tmp_path."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_demo_builder():
    spec = importlib.util.spec_from_file_location(
        "make_demo_data", ROOT / "examples" / "make_demo_data.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build


@pytest.fixture()
def demo_tree(tmp_path: Path) -> Path:
    """A generated temp(14)-style tree inside the test's tmp dir."""
    build = _load_demo_builder()
    root = tmp_path / "temp(14)"
    build(str(root))
    return root
