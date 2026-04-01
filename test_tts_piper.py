#!/usr/bin/env python3
"""tts_piper 基础测试"""

import os
import unittest

from tts_piper import piper_runtime_ready


class TestTtsPiper(unittest.TestCase):
    def test_no_model_env_means_not_ready(self):
        old = os.environ.pop("PIPER_MODEL", None)
        try:
            self.assertFalse(piper_runtime_ready())
        finally:
            if old is not None:
                os.environ["PIPER_MODEL"] = old


if __name__ == "__main__":
    unittest.main()
