"""Request failures and execution policy shared by DeepSeek batch jobs."""

from __future__ import annotations

import ssl
import urllib.error
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Iterator, Optional


class DeepSeekRequestError(Exception):
    def __init__(
        self,
        kind: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: Optional[int] = None,
        retry_after_sec: Optional[float] = None,
    ) -> None:
        if kind not in {"configuration", "transport", "response_format"}:
            raise ValueError(f"unknown DeepSeek request error kind: {kind}")
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after_sec = retry_after_sec


class JobPaused(Exception):
    """A request policy paused the job without consuming a quality attempt."""


def is_deepseek_off_peak(moment: Optional[datetime] = None) -> bool:
    """Match DeepSeek's weekday peak windows of 01-04 and 06-10 UTC."""
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    if current.weekday() >= 5:
        return True
    minute = current.hour * 60 + current.minute
    return not (60 <= minute < 240 or 360 <= minute < 600)


_request_guards: ContextVar[tuple[Callable[[], bool], ...]] = ContextVar(
    "deepseek_request_guards", default=(),
)


@contextmanager
def off_peak_requests(
    enabled: bool = True,
    *,
    clock: Optional[Callable[[], datetime]] = None,
    predicate: Optional[Callable[[], bool]] = None,
) -> Iterator[None]:
    """Apply an off-peak guard within this execution context only."""
    if not enabled:
        yield
        return
    guard = predicate or (lambda: is_deepseek_off_peak(clock() if clock else None))
    token = _request_guards.set((*_request_guards.get(), guard))
    try:
        yield
    finally:
        _request_guards.reset(token)


def check_before_request() -> None:
    if any(not guard() for guard in _request_guards.get()):
        raise JobPaused("DeepSeek peak hours started; saved work can resume off-peak")


def retry_after_seconds(headers, *, now: Optional[datetime] = None) -> Optional[float]:
    """Parse both Retry-After formats without imposing a blocking delay."""
    raw = headers.get("Retry-After") if headers else None
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(str(raw))
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            current = now or datetime.now(timezone.utc)
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            seconds = (target - current).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    # Nonfinite values must never reach sleep().
    if seconds != seconds or seconds in (float("inf"), float("-inf")):
        return None
    return max(0.0, seconds)


def request_error(error: BaseException) -> DeepSeekRequestError:
    if isinstance(error, DeepSeekRequestError):
        return error
    if isinstance(error, urllib.error.HTTPError):
        transient = error.code in (408, 425, 429) or error.code >= 500
        return DeepSeekRequestError(
            "transport" if transient else "configuration",
            f"DeepSeek HTTP {error.code}: {error.reason}",
            retryable=transient,
            status_code=error.code,
            retry_after_sec=retry_after_seconds(error.headers),
        )
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, ssl.SSLCertVerificationError):
        return DeepSeekRequestError("configuration", f"DeepSeek TLS configuration: {reason}")
    transient = isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError))
    return DeepSeekRequestError(
        "transport", f"DeepSeek request failed: {error}", retryable=transient,
    )


def api_error(error) -> DeepSeekRequestError:
    """Classify an API error returned inside a successful HTTP envelope."""
    code = str(error.get("code") or error.get("type") or "").lower() if isinstance(error, dict) else ""
    if code in {"400", "401", "402", "403", "404", "422", "invalid_api_key",
                "authentication_error", "insufficient_balance", "invalid_request_error"}:
        kind, retryable = "configuration", False
    else:
        kind = "transport"
        retryable = code in {"429", "500", "502", "503", "504", "rate_limit_error",
                             "rate_limit_exceeded", "server_error", "overloaded_error"}
    return DeepSeekRequestError(kind, f"DeepSeek API returned an error: {error}", retryable=retryable)
