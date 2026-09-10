from pathlib import Path
import shutil
import subprocess

import pytest


def test_question_authoring_browser_workbench():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for isolated browser workbench tests")
    root = Path(__file__).resolve().parent
    result = subprocess.run(
        [node, "--test", "test_question_authoring_ui.js"],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
