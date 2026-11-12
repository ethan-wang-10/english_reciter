from pathlib import Path
import shutil
import subprocess

import pytest


def test_account_and_list_browser_flows():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for isolated browser tests")
    root = Path(__file__).resolve().parent
    result = subprocess.run(
        [node, "test_app_ui.js"],
        cwd=root, capture_output=True, text=True, timeout=30,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
