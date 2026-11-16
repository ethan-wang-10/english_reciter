"""Exercise the real administrator Kokoro controls without an admin session."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_admin_kokoro_browser_controls():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required')
    result = subprocess.run([node, '--test', '--test-reporter=dot', 'test_admin_kokoro_ui.js'],
                            cwd=Path(__file__).resolve().parent, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
