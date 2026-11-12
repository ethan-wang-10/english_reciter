import shutil
import subprocess
from pathlib import Path

import pytest


def test_chat_browser_state_regressions():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for browser state regression tests')
    result = subprocess.run([node, str(Path(__file__).with_suffix('.js'))],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
