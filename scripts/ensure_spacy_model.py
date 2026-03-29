#!/usr/bin/env python3
"""在部署机同一 venv 中安装 en_core_web_sm（与 simple_web_app 使用的模型一致）。"""
import subprocess
import sys


def main() -> None:
    subprocess.check_call(
        [sys.executable, "-m", "spacy", "download", "en_core_web_sm"],
        timeout=600,
    )
    import spacy

    spacy.load("en_core_web_sm")
    print("en_core_web_sm OK")


if __name__ == "__main__":
    main()
