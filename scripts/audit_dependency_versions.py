"""Read public PyPI/OSV advisories for direct dependency pins and allowed floors.

This does not resolve dependencies or replace an installed-environment pip-audit.
"""

import argparse
import concurrent.futures
import datetime
import hashlib
import json
import re
import time
import urllib.request
from pathlib import Path


def _read_metadata(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                return json.load(response)
        except OSError:
            if attempt == 2:
                raise
            time.sleep(1)


def _advisories(metadata):
    return [
        {key: row.get(key) for key in ("id", "aliases", "fixed_in", "link", "source", "summary")}
        for row in metadata["vulnerabilities"] if not row.get("withdrawn")
    ]


def package_report(requirement):
    match = re.match(r"([\w-]+)(==|>=)([\d.]+)", requirement)
    if not match:
        raise ValueError(f"Unsupported requirement: {requirement}")
    name, operator, version = match.groups()
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    metadata = _read_metadata(url)
    latest = _read_metadata(f"https://pypi.org/pypi/{name}/json")
    return {
        "requirement": requirement,
        "name": name,
        "audited_version": version,
        "version_kind": "pin" if operator == "==" else "allowed_floor_not_resolved_install",
        "source": url,
        "vulnerabilities": _advisories(metadata),
        "latest_version": latest["info"]["version"],
        "latest_requires_python": latest["info"]["requires_python"],
        "latest_vulnerabilities": _advisories(latest),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", type=Path, default=Path("requirements-simple.txt"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.requirements.read_bytes()
    requirements = [line.strip() for line in source.decode("utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(package_report, requirements))
    report = {
        "collected_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "requirements_sha256": hashlib.sha256(source).hexdigest(),
        "scope": "Direct pins and allowed version floors; not a transitive installed-environment audit",
        "packages": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    for row in rows:
        print(row["name"], row["audited_version"], "advisories=" + str(len(row["vulnerabilities"])), flush=True)


if __name__ == "__main__":
    main()
