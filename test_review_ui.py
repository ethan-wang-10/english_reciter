from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('test_file', ['test_review_ui.js', 'test_exam_preparation_ui.js'])
def test_review_browser_interactions(test_file):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for isolated review UI tests")
    root = Path(__file__).resolve().parent
    result = subprocess.run(
        [node, "--test", test_file],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
