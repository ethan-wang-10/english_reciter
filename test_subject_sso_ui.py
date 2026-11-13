"""Run isolated browser-side SSO state, storage and navigation regressions."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_subject_sso_browser_handoff():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for isolated browser tests')
    result = subprocess.run(
        [node, '--test', '--test-reporter=dot', 'test_subject_sso_ui.js'],
        cwd=Path(__file__).resolve().parent, capture_output=True, text=True,
        encoding='utf-8', timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
