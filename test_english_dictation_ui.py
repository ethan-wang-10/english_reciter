"""Exercise the English paper dictation browser workflow without external audio."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_english_dictation_browser_flows():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for isolated browser tests')
    root = Path(__file__).resolve().parent
    result = subprocess.run(
        [node, '--test', '--test-reporter=dot', 'test_english_dictation_ui.js', 'test_english_dictation_host_ui.js'],
        cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
