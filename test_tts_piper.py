#!/usr/bin/env python3
"""tts_piper 基础测试"""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tts_piper import (
    piper_last_result_metadata,
    piper_runtime_ready,
    piper_synthesize_wav,
)


class TestTtsPiper(unittest.TestCase):
    def test_no_model_env_means_not_ready(self):
        old = os.environ.pop("PIPER_MODEL", None)
        try:
            self.assertFalse(piper_runtime_ready())
        finally:
            if old is not None:
                os.environ["PIPER_MODEL"] = old

    def test_synthesis_uses_bounded_disk_cache_keyed_by_model_and_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model = root / 'voice.onnx'
            binary = root / 'piper'
            cache = root / 'cache'
            model.write_bytes(b'model-v1')
            binary.write_bytes(b'fake-binary')
            calls = []

            def run(args, **kwargs):
                calls.append((args, kwargs))
                Path(args[args.index('--output_file') + 1]).write_bytes(b'RIFF' + b'\0' * 128)
                return SimpleNamespace(returncode=0, stderr=b'')

            env = {
                'PIPER_MODEL': str(model),
                'PIPER_BINARY': str(binary),
                'PIPER_CACHE_DIR': str(cache),
            }
            with patch.dict(os.environ, env, clear=False), patch('tts_piper.subprocess.run', side_effect=run):
                first = piper_synthesize_wav('Hello world')
                self.assertFalse(piper_last_result_metadata()['cache_hit'])
                second = piper_synthesize_wav('Hello   world')
                self.assertTrue(piper_last_result_metadata()['cache_hit'])

            self.assertEqual(first, second)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(list(cache.glob('*.wav'))), 1)


if __name__ == "__main__":
    unittest.main()
