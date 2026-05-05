#!/usr/bin/env bash
# Compatible deployment helper for english_reciter.
# Supports macOS/Linux with PM2, native Gunicorn, and Docker Compose v1/v2.

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MODE="${DEPLOY_MODE:-auto}"
BRANCH="${DEPLOY_BRANCH:-}"
SKIP_PULL=0
SKIP_INSTALL=0
DRY_RUN=0
RUN_TESTS=0
NO_RESTART=0
STRICT_DIRTY=0
UPGRADE_PIP="${DEPLOY_UPGRADE_PIP:-0}"
INSTALL_SPACY_MODEL="${DEPLOY_INSTALL_SPACY_MODEL:-0}"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WORKERS="${WEB_CONCURRENCY:-1}"
THREADS="${GUNICORN_THREADS:-4}"
TZ_VALUE="${TZ:-Asia/Shanghai}"
VENV_DIR="${VENV_DIR:-.venv}"
REQUIREMENTS="${REQUIREMENTS:-requirements-simple.txt}"
PYTHON_BIN="${PYTHON_BIN:-}"
HEALTH_PATH="${HEALTH_PATH:-/api/health}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-30}"
NATIVE_PID_FILE="${NATIVE_PID_FILE:-user_data_simple/gunicorn.pid}"
LOCAL_RUNTIME_PATHS="${DEPLOY_LOCAL_RUNTIME_PATHS:-static/wordbanks/words_v2.json}"

usage() {
  cat <<'EOF_USAGE'
Usage:
  scripts/deploy.sh [options]

Options:
  --mode auto|pm2|native|docker  Deployment backend (default: auto)
  --branch NAME                  Pull this branch with --ff-only before deploy
  --host ADDR                    Bind address for PM2/native (default: 0.0.0.0)
  --port PORT                    Host/app port (default: 8000; Docker maps PORT:8000)
  --workers N                    Gunicorn workers for PM2/native (default: 1)
  --threads N                    Gunicorn threads for PM2/native (default: 4)
  --skip-pull                    Do not run git pull
  --skip-install                 Do not install/update Python dependencies
  --no-restart                   Prepare/check only; do not restart the service
  --test                         Run pytest if it is available
  --strict-dirty                 Refuse any tracked local changes before pull
  --upgrade-pip                  Upgrade pip before installing requirements
  --install-spacy-model          Best-effort install en_core_web_sm after deps
  --dry-run                      Print actions without changing files/services
  -h, --help                     Show this help

Environment overrides:
  DEPLOY_MODE, DEPLOY_BRANCH, PYTHON_BIN, VENV_DIR, HOST, PORT,
  WEB_CONCURRENCY, GUNICORN_THREADS, HEALTH_TIMEOUT, NATIVE_PID_FILE, TZ,
  DEPLOY_LOCAL_RUNTIME_PATHS, DEPLOY_UPGRADE_PIP, DEPLOY_INSTALL_SPACY_MODEL

Examples:
  scripts/deploy.sh --mode pm2
  scripts/deploy.sh --mode docker
  PORT=9000 scripts/deploy.sh --mode docker
  scripts/deploy.sh --mode native --skip-pull
  scripts/deploy.sh --dry-run --skip-pull
EOF_USAGE
}

log() {
  printf '[deploy] %s\n' "$*"
}

warn() {
  printf '[deploy][warn] %s\n' "$*" >&2
}

die() {
  printf '[deploy][error] %s\n' "$*" >&2
  exit 1
}

run() {
  log "+ $*"
  if [ "$DRY_RUN" = "1" ]; then
    return 0
  fi
  "$@"
}

require_value() {
  opt="$1"
  val="${2:-}"
  if [ -z "$val" ]; then
    die "$opt requires a value"
  fi
}

is_positive_int() {
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
    *) [ "$1" -gt 0 ] ;;
  esac
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode)
      require_value "$1" "${2:-}"
      MODE="$2"
      shift 2
      ;;
    --branch)
      require_value "$1" "${2:-}"
      BRANCH="$2"
      shift 2
      ;;
    --host)
      require_value "$1" "${2:-}"
      HOST="$2"
      shift 2
      ;;
    --port)
      require_value "$1" "${2:-}"
      PORT="$2"
      shift 2
      ;;
    --workers)
      require_value "$1" "${2:-}"
      WORKERS="$2"
      shift 2
      ;;
    --threads)
      require_value "$1" "${2:-}"
      THREADS="$2"
      shift 2
      ;;
    --skip-pull)
      SKIP_PULL=1
      shift
      ;;
    --skip-install)
      SKIP_INSTALL=1
      shift
      ;;
    --no-restart)
      NO_RESTART=1
      shift
      ;;
    --test)
      RUN_TESTS=1
      shift
      ;;
    --strict-dirty)
      STRICT_DIRTY=1
      shift
      ;;
    --upgrade-pip)
      UPGRADE_PIP=1
      shift
      ;;
    --install-spacy-model)
      INSTALL_SPACY_MODEL=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

case "$MODE" in
  auto|pm2|native|docker) ;;
  *) die "--mode must be one of: auto, pm2, native, docker" ;;
esac

is_positive_int "$PORT" || die "--port/PORT must be a positive integer"
is_positive_int "$WORKERS" || die "--workers/WEB_CONCURRENCY must be a positive integer"
is_positive_int "$THREADS" || die "--threads/GUNICORN_THREADS must be a positive integer"
is_positive_int "$HEALTH_TIMEOUT" || die "HEALTH_TIMEOUT must be a positive integer"

# Make values visible to PM2 ecosystem.config.cjs and Docker Compose interpolation.
export HOST PORT WEB_CONCURRENCY="$WORKERS" GUNICORN_THREADS="$THREADS" TZ="$TZ_VALUE"

have() {
  command -v "$1" >/dev/null 2>&1
}

python_version_ok() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 9) else 1)
PY
}

find_python() {
  required="${1:-required}"

  if [ -n "$PYTHON_BIN" ]; then
    if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
      [ "$required" = "optional" ] && { warn "PYTHON_BIN not found: $PYTHON_BIN"; PYTHON_BIN=""; return 1; }
      die "PYTHON_BIN not found: $PYTHON_BIN"
    fi
  elif have python3; then
    PYTHON_BIN="$(command -v python3)"
  elif have python; then
    PYTHON_BIN="$(command -v python)"
  else
    [ "$required" = "optional" ] && { warn "Python not found; skip host Python checks"; return 1; }
    die "Python 3.9+ is required for $MODE mode"
  fi

  if ! python_version_ok "$PYTHON_BIN"; then
    [ "$required" = "optional" ] && { warn "Python 3.9+ not found; skip host Python checks"; PYTHON_BIN=""; return 1; }
    die "Python 3.9+ is required for $MODE mode"
  fi

  log "Python: $("$PYTHON_BIN" --version 2>&1)"
}

env_has_value() {
  key="$1"
  [ -f .env ] && grep -Eq "^[[:space:]]*${key}[[:space:]]*=[[:space:]]*[^[:space:]#]+" .env
}

append_env_line() {
  line="$1"
  label="${line%%=*}"
  if [ "$DRY_RUN" = "1" ]; then
    log "+ append $label to .env"
    return 0
  fi
  [ -s .env ] && printf '\n' >> .env
  printf '%s\n' "$line" >> .env
}

generate_secret() {
  if have openssl; then
    openssl rand -hex 48
    return 0
  fi
  if [ -n "$PYTHON_BIN" ]; then
    "$PYTHON_BIN" - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
    return 0
  fi
  die "Cannot generate SECRET_KEY; install openssl/Python or create .env manually"
}

ensure_runtime_files() {
  run mkdir -p user_data_simple static

  if [ ! -f config.json ] && [ -f config.example.json ]; then
    run cp config.example.json config.json
  fi

  if ! env_has_value SECRET_KEY; then
    if [ "$DRY_RUN" = "1" ]; then
      append_env_line "SECRET_KEY=***"
    else
      secret="${SECRET_KEY:-}"
      [ -n "$secret" ] || secret="$(generate_secret)"
      append_env_line "SECRET_KEY=$secret"
    fi
    log "SECRET_KEY ensured in .env"
  fi

  if ! env_has_value TZ; then
    append_env_line "TZ=$TZ_VALUE"
  fi
}

is_local_runtime_path() {
  path="$1"
  for runtime_path in $LOCAL_RUNTIME_PATHS; do
    [ "$path" = "$runtime_path" ] && return 0
  done
  return 1
}

dirty_non_runtime_files() {
  {
    git diff --name-only
    git diff --cached --name-only
  } | sort -u | while IFS= read -r path; do
    [ -z "$path" ] && continue
    if is_local_runtime_path "$path"; then
      continue
    fi
    printf '%s\n' "$path"
  done
}

protect_local_runtime_paths() {
  [ "$STRICT_DIRTY" = "1" ] && return 0
  for runtime_path in $LOCAL_RUNTIME_PATHS; do
    [ -n "$runtime_path" ] || continue
    if ! git ls-files --error-unmatch -- "$runtime_path" >/dev/null 2>&1; then
      continue
    fi
    if ! git diff --cached --quiet -- "$runtime_path"; then
      die "Runtime file has staged changes: $runtime_path. Unstage it or use --strict-dirty."
    fi
    if ! git diff --quiet -- "$runtime_path"; then
      warn "Preserving local runtime file during deploy pull: $runtime_path"
      warn "It will be marked skip-worktree in this checkout; keep backing it up separately."
    fi
    if [ "$DRY_RUN" = "1" ]; then
      log "+ git update-index --skip-worktree -- $runtime_path"
    else
      git update-index --skip-worktree -- "$runtime_path"
    fi
  done
}

git_pull_ff_only() {
  [ "$SKIP_PULL" = "1" ] && return 0
  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    warn "Not a git worktree; skip pull"
    return 0
  fi
  protect_local_runtime_paths
  dirty_files="$(dirty_non_runtime_files)"
  if [ -n "$dirty_files" ]; then
    warn "Local modified/staged files:"
    printf '%s\n' "$dirty_files" >&2
    die "Refuse to pull with local changes. Commit/stash them or use --skip-pull."
  fi

  if [ -n "$BRANCH" ]; then
    run git fetch origin "$BRANCH"
    current_branch="$(git branch --show-current 2>/dev/null || true)"
    if [ "$current_branch" != "$BRANCH" ]; then
      run git checkout "$BRANCH"
    fi
    run git pull --ff-only origin "$BRANCH"
  else
    run git pull --ff-only
  fi
}

venv_python_path() {
  if [ -x "$VENV_DIR/bin/python" ]; then
    printf '%s\n' "$VENV_DIR/bin/python"
  elif [ -x "$VENV_DIR/Scripts/python.exe" ]; then
    printf '%s\n' "$VENV_DIR/Scripts/python.exe"
  else
    printf '%s\n' "$VENV_DIR/bin/python"
  fi
}

venv_bin_path() {
  bin_name="$1"
  if [ -x "$VENV_DIR/bin/$bin_name" ]; then
    printf '%s\n' "$VENV_DIR/bin/$bin_name"
  elif [ -x "$VENV_DIR/Scripts/$bin_name.exe" ]; then
    printf '%s\n' "$VENV_DIR/Scripts/$bin_name.exe"
  else
    printf '%s\n' "$VENV_DIR/bin/$bin_name"
  fi
}

ensure_venv_and_deps() {
  [ "$SKIP_INSTALL" = "1" ] && return 0
  [ -f "$REQUIREMENTS" ] || die "Requirements file not found: $REQUIREMENTS"
  if [ ! -x "$VENV_DIR/bin/python" ] && [ ! -x "$VENV_DIR/Scripts/python.exe" ]; then
    run "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi
  app_py="$(venv_python_path)"
  if [ "$UPGRADE_PIP" = "1" ]; then
    run "$app_py" -m pip install --upgrade pip
  fi
  run "$app_py" -m pip install -r "$REQUIREMENTS"
  if [ "$INSTALL_SPACY_MODEL" = "1" ]; then
    # GitHub may be unreachable on China/offline servers; keep deploy usable.
    if [ "$DRY_RUN" = "1" ]; then
      log "+ $app_py -m spacy download en_core_web_sm"
    elif ! "$app_py" -m spacy download en_core_web_sm; then
      warn "Failed to install en_core_web_sm; app will fall back to heuristic lemmatization."
    fi
  fi
}

has_compose() {
  { have docker && docker compose version >/dev/null 2>&1; } || have docker-compose
}

detect_mode() {
  [ "$MODE" != "auto" ] && return 0
  if have pm2 && [ -f ecosystem.config.cjs ]; then
    MODE="pm2"
  elif has_compose; then
    MODE="docker"
  else
    MODE="native"
  fi
  log "Auto-selected mode: $MODE"
}

smoke_check() {
  app_py="$(venv_python_path)"
  py=""
  if [ -x "$app_py" ]; then
    py="$app_py"
  elif [ -n "$PYTHON_BIN" ]; then
    py="$PYTHON_BIN"
  fi

  if [ -n "$py" ]; then
    run "$py" -m py_compile simple_web_app.py reciter.py user_store.py auth_session_store.py wordbank_v2.py
  elif [ "$MODE" = "docker" ]; then
    warn "Python not available on host; Docker build will validate Python dependencies"
  else
    die "Python 3.9+ is required for host smoke checks"
  fi

  if have node; then
    run node --check static/js/app.js
  else
    warn "node not found; skip JavaScript syntax check"
  fi

  if [ "$RUN_TESTS" = "1" ]; then
    [ -n "$py" ] || die "--test requires Python/pytest on the host"
    run "$py" -m pytest -q
  fi
}

restart_pm2() {
  have pm2 || die "pm2 not found; install PM2 or use --mode native/docker"
  [ -f ecosystem.config.cjs ] || die "ecosystem.config.cjs not found"
  if pm2 describe english-reciter >/dev/null 2>&1; then
    run pm2 reload english-reciter --update-env
  else
    run pm2 start ecosystem.config.cjs --update-env
  fi
  run pm2 save
}

restart_native() {
  gunicorn_bin="$(venv_bin_path gunicorn)"
  [ -x "$gunicorn_bin" ] || die "gunicorn not found in $VENV_DIR; run without --skip-install"
  run mkdir -p "$(dirname "$NATIVE_PID_FILE")"

  if [ -f "$NATIVE_PID_FILE" ]; then
    old_pid="$(cat "$NATIVE_PID_FILE" 2>/dev/null || true)"
    if [ -n "$old_pid" ] && kill -0 "$old_pid" >/dev/null 2>&1; then
      run kill "$old_pid"
      if [ "$DRY_RUN" != "1" ]; then
        i=0
        while kill -0 "$old_pid" >/dev/null 2>&1 && [ "$i" -lt 20 ]; do
          i=$((i + 1))
          sleep 1
        done
        if kill -0 "$old_pid" >/dev/null 2>&1; then
          die "Old Gunicorn process did not stop: $old_pid"
        fi
      fi
    fi
  fi

  run "$gunicorn_bin" \
    -c gunicorn_config.py \
    --bind "$HOST:$PORT" \
    --workers "$WORKERS" \
    --threads "$THREADS" \
    --pid "$NATIVE_PID_FILE" \
    --daemon \
    simple_web_app:app
}

restart_docker() {
  if have docker && docker compose version >/dev/null 2>&1; then
    run docker compose up -d --build
  elif have docker-compose; then
    run docker-compose up -d --build
  else
    die "Docker Compose not found; install Docker Compose v2 or docker-compose"
  fi
}

probe_health() {
  url="$1"
  body=""

  if have curl; then
    body="$(curl -fsS --max-time 2 "$url" 2>/dev/null || true)"
  elif have wget; then
    body="$(wget -qO- --timeout=2 "$url" 2>/dev/null || true)"
  elif [ -n "$PYTHON_BIN" ]; then
    body="$("$PYTHON_BIN" - "$url" <<'PY' 2>/dev/null || true
import sys
import urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=2) as resp:
    sys.stdout.write(resp.read(500).decode("utf-8", "replace"))
PY
)"
  else
    die "Health check requires curl, wget, or Python on the host"
  fi

  case "$body" in
    *healthy*) return 0 ;;
    *) return 1 ;;
  esac
}

health_check() {
  [ "$NO_RESTART" = "1" ] && return 0
  [ "$DRY_RUN" = "1" ] && return 0
  url="http://127.0.0.1:${PORT}${HEALTH_PATH}"

  log "Health check: $url"
  i=0
  while [ "$i" -lt "$HEALTH_TIMEOUT" ]; do
    if probe_health "$url"; then
      log "Health check passed"
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  die "Health check failed: $url"
}

main() {
  log "Project root: $ROOT_DIR"
  git_pull_ff_only
  detect_mode

  if [ "$MODE" = "pm2" ] || [ "$MODE" = "native" ]; then
    find_python required
  else
    find_python optional || true
  fi

  ensure_runtime_files

  if [ "$MODE" = "pm2" ] || [ "$MODE" = "native" ]; then
    ensure_venv_and_deps
  fi

  smoke_check

  if [ "$NO_RESTART" = "1" ]; then
    log "Prepared successfully (--no-restart)"
    return 0
  fi

  case "$MODE" in
    pm2) restart_pm2 ;;
    native) restart_native ;;
    docker) restart_docker ;;
  esac

  health_check
  log "Deployment completed"
}

main "$@"
