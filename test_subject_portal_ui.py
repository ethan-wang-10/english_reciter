"""Exercise the subject portal's actual browser logic with isolated DOM and HTTP mocks."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_subject_portal_browser_flows():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for isolated browser tests')
    root = Path(__file__).resolve().parent
    result = subprocess.run(
        [node, '--test', '--test-reporter=dot', 'test_subject_portal_ui.js'],
        cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
