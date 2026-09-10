#!/usr/bin/env python3
"""Claim and submit externally authored questions without calling a model API."""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid


API_ROOT = "/api/admin/gaokao/authoring"
DEFAULT_BASE_URL = "https://english.itorange.online"
KINDS = ("generation", "revision", "recognition_blind", "context_blind", "feedback")
TOKEN_ENV = "ENGLISH_RECITER_ADMIN_TOKEN"


class ClientError(Exception):
    def __init__(self, message: str, *, response: dict | None = None):
        super().__init__(message)
        self.response = response


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validated_base_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ClientError("Invalid server URL") from exc
    if (
        not host or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or any(ord(char) < 33 for char in value)
    ):
        raise ClientError("Server URL must not contain credentials, query parameters, or fragments")
    local = host.lower() == "localhost"
    try:
        local = local or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise ClientError("HTTPS is required except for localhost or loopback addresses")
    return value.rstrip("/")


def _read_token(token_file: str | None) -> str:
    if token_file:
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise ClientError("Could not read the administrator token file") from exc
    else:
        token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        raise ClientError(f"Set {TOKEN_ENV} or provide --token-file")
    if not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token):
        raise ClientError("Administrator token has an invalid format")
    return token


def _decode_response(raw: bytes) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClientError("Server returned an invalid JSON response") from exc
    if not isinstance(value, dict):
        raise ClientError("Server response must be a JSON object")
    return value


def request_json(
    base_url: str, token: str, method: str, path: str, *,
    body: dict | None = None, query: dict | None = None, timeout: float = 60,
) -> dict:
    url = _validated_base_url(base_url) + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(_NoRedirects())
    try:
        with opener.open(request, timeout=timeout) as response:
            return _decode_response(response.read())
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            exc.close()
            raise ClientError("Server redirect refused; use the final HTTPS server URL") from exc
        try:
            payload = _decode_response(exc.read())
        except ClientError:
            payload = {"error": f"HTTP {exc.code}"}
        finally:
            exc.close()
        raise ClientError(f"HTTP {exc.code}", response={**payload, "http_status": exc.code}) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ClientError("Network request failed; retry with the same request or submission ID") from exc


def _bounded_int(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be an integer") from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return number
    return parse


def _timeout(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if not math.isfinite(number) or not 0 < number <= 3600:
        raise argparse.ArgumentTypeError("timeout must be greater than 0 and at most 3600 seconds")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", default=DEFAULT_BASE_URL)
    common.add_argument("--worker-id", required=True)
    common.add_argument("--token-file", help=f"Plain token file; otherwise read {TOKEN_ENV}")
    common.add_argument("--timeout", type=_timeout, default=60)
    common.add_argument("--output", help="Write JSON response to this file instead of stdout")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("pending", "claim", "get", "submit", "renew", "release"):
        command = commands.add_parser(name, parents=[common])
        if name in ("pending", "claim"):
            command.add_argument("--kind", choices=KINDS, default="generation")
            command.add_argument("--level", default="")
            command.add_argument("--limit", type=_bounded_int(1, 10), default=10)
            command.add_argument("--words", nargs="+", help="Restrict to 1 to 10 specific word keys")
        else:
            command.add_argument("job_id")
        if name in ("claim", "renew"):
            command.add_argument("--ttl-seconds", type=_bounded_int(60, 86400), default=3600)
        if name == "claim":
            command.add_argument("--request-id", help="Reuse this ID when retrying a claim")
        if name == "submit":
            command.add_argument("--submission-id", help="Reuse this ID when retrying the same payload")
            command.add_argument("--input", required=True, help="JSON items array or object containing only items")
    return parser


def _submission_items(filename: str) -> list:
    try:
        value = json.loads(Path(filename).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClientError("Could not read valid JSON from --input") from exc
    if isinstance(value, dict):
        if set(value) != {"items"}:
            raise ClientError("Submission JSON object must contain only items")
        value = value["items"]
    if not isinstance(value, list) or not value:
        raise ClientError("Submission items must be a nonempty JSON array")
    for item in value:
        if (
            not isinstance(item, dict) or set(item) != {"item_id", "result"}
            or not isinstance(item["item_id"], str) or not item["item_id"].strip()
            or not isinstance(item["result"], dict)
        ):
            raise ClientError("Each submitted item must contain item_id and a result object")
    if len({item["item_id"] for item in value}) != len(value):
        raise ClientError("Submitted item IDs must be unique")
    return value


def _build_request(args) -> tuple[str, str, dict | None, dict | None, dict]:
    path = API_ROOT
    body, query, metadata = None, None, {}
    if args.command == "pending":
        method, path = "GET", path + "/pending"
        query = {"kind": args.kind, "worker_id": args.worker_id, "level": args.level, "limit": args.limit}
    elif args.command == "claim":
        method, path = "POST", path + "/claims"
        request_id = args.request_id or str(uuid.uuid4())
        metadata["request_id"] = request_id
        body = {
            "request_id": request_id, "kind": args.kind, "worker_id": args.worker_id,
            "level": args.level, "limit": args.limit, "ttl_seconds": args.ttl_seconds,
        }
    else:
        path += "/claims/" + urllib.parse.quote(args.job_id, safe="")
        method = "GET" if args.command == "get" else "POST"
        if args.command == "get":
            query = {"worker_id": args.worker_id}
        else:
            body = {"worker_id": args.worker_id}
            path += "/submissions" if args.command == "submit" else f"/{args.command}"
            if args.command == "renew":
                body["ttl_seconds"] = args.ttl_seconds
            elif args.command == "submit":
                submission_id = args.submission_id or str(uuid.uuid4())
                body.update(submission_id=submission_id, items=_submission_items(args.input))
                metadata["submission_id"] = submission_id
    if args.command in ("pending", "claim") and args.words is not None:
        words = [" ".join(word.strip().casefold().split()) for word in args.words]
        if (not 1 <= len(words) <= 10 or len(set(words)) != len(words) or any(
                not word or len(original) > 128 or any(ord(char) < 32 or ord(char) == 127 for char in original)
                for word, original in zip(words, args.words))):
            raise ClientError("--words requires 1 to 10 unique nonempty word keys of at most 128 characters")
        words.sort()
        if args.command == "pending":
            query["words"] = json.dumps(words, ensure_ascii=False)
        else:
            body["words"] = words
    return method, path, body, query, {"method": method, "path": path, **metadata}


def _emit(payload: dict, filename: str | None, token: str) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if token:
        text = text.replace(token, "[REDACTED]")
    if filename:
        Path(filename).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    token, metadata, exit_code = "", {}, 0
    try:
        token = _read_token(args.token_file)
        method, path, body, query, metadata = _build_request(args)
        for field in ("request_id", "submission_id"):
            if field in metadata:
                print(f"{field}={metadata[field]}".replace(token, "[REDACTED]"), file=sys.stderr, flush=True)
        payload = request_json(args.base_url, token, method, path, body=body, query=query, timeout=args.timeout)
    except ClientError as exc:
        payload = exc.response or {"error": str(exc)}
        exit_code = 1
    try:
        _emit({**payload, "client_request": metadata}, args.output, token)
    except OSError:
        print("Could not write the JSON response to --output", file=sys.stderr)
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
