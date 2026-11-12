"""Package versioned source and new files without private runtime state."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED = {'.test_tmp', '.nox', '.cache', '.git', 'user_data_simple', 'backups',
            'archives', 'design', '.vscode'}
PRIVATE_NAMES = {'config.json', '.env', 'learning_data.json'}


def package(destination):
    raw = subprocess.check_output([
        'git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z', '--', '.',
        ':!.test_tmp', ':!.nox', ':!.cache',
    ], cwd=ROOT)
    paths = sorted({path.decode('utf-8') for path in raw.split(b'\0') if path})
    files, contents = {}, {}
    for name in paths:
        path = ROOT / name
        if (Path(name).parts[0] in EXCLUDED or Path(name).name in PRIVATE_NAMES
                or not path.is_file() or path.is_symlink()):
            continue
        content = path.read_bytes()
        if path.suffix == '.sh':
            content = content.replace(b'\r\n', b'\n')
        files[name] = hashlib.sha256(content).hexdigest()
        contents[name] = content
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, 'w:gz') as archive:
        for name in files:
            member = tarfile.TarInfo(name)
            member.size = len(contents[name])
            member.mode = 0o755 if Path(name).suffix == '.sh' else 0o644
            archive.addfile(member, io.BytesIO(contents[name]))
    manifest = {'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(), 'files': files}
    destination.with_suffix(destination.suffix + '.json').write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({'archive': str(destination), 'sha256': manifest['sha256'], 'files': len(files)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination')
    package(parser.parse_args().destination)
