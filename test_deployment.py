"""Check that the production image includes every local runtime dependency."""

import ast
import shlex
from pathlib import Path


def test_docker_copies_transitive_runtime_modules():
    root = Path(__file__).resolve().parent
    dockerfile = (root / 'Dockerfile').read_text(encoding='utf-8')
    copied = set()
    for line in dockerfile.replace('\\\n', ' ').splitlines():
        parts = shlex.split(line, comments=True)
        if parts and parts[0] == 'COPY':
            copied.update(Path(source).name for source in parts[1:-1])

    pending = ['simple_web_app', 'gunicorn_config']
    visited = set()
    while pending:
        module = pending.pop()
        if module in visited:
            continue
        visited.add(module)
        source = root / f'{module}.py'
        assert source.name in copied, f'Docker image is missing {source.name}'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split('.')[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split('.')[0]]
            pending.extend(name for name in names if (root / f'{name}.py').is_file())

    assert 'builtin_exams' in copied, 'Docker image is missing built-in exam data'
