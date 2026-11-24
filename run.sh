#!/usr/bin/env bash
#
# One entry point for the X Timeline Extractor on macOS, Linux and WSL.
#
#   ./run.sh setup      venv, dependencies, Chromium, .env, npm install
#   ./run.sh session    log into X once and save the session
#   ./run.sh start      build the frontend if needed, then serve on :8000
#   ./run.sh dev        backend + Vite dev server together (hot reload)
#   ./run.sh test       backend tests, frontend build, browser smoke test
#   ./run.sh mongo      is the history store reachable, and what is in it?
#   ./run.sh help       this list
#
# Windows: use PowerShell and the commands in README.md instead; this script
# assumes a POSIX shell.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
PY="${PYTHON:-python3}"
PORT="${XTIMELINE_PORT:-8000}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
note() { printf '  %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

need() {
  command -v "$1" >/dev/null 2>&1 || die "$1 is required but not installed"
}

activate_venv() {
  if [ ! -d "$BACKEND/.venv" ]; then
    bold "creating backend/.venv"
    "$PY" -m venv "$BACKEND/.venv"
  fi
  # shellcheck disable=SC1091
  . "$BACKEND/.venv/bin/activate"
  # Some distros and CI images export these; inside a venv they break pip and
  # can shadow the venv's own packages.
  unset PIP_USER PIP_TARGET PYTHONHOME PYTHONPATH
}

cmd_setup() {
  need "$PY"
  need node
  need npm

  activate_venv
  bold "installing backend dependencies"
  pip install -q --upgrade pip
  pip install -q -r "$BACKEND/requirements-dev.txt"

  bold "installing Playwright's Chromium"
  note "the full browser, not just the headless shell: X serves the shell an"
  note "empty timeline, which is why headless runs used to return nothing"
  if ! python -m playwright install chromium; then
    warn "Chromium did not install — the app will not scrape until it does"
    warn "if PLAYWRIGHT_BROWSERS_PATH is set, point it at a writable directory:"
    warn "  export PLAYWRIGHT_BROWSERS_PATH=\"$HOME/.cache/ms-playwright\""
    warn "then re-run: ./run.sh setup"
  fi

  bold "installing frontend dependencies"
  (cd "$FRONTEND" && npm install --silent)

  if [ ! -f "$BACKEND/.env" ]; then
    cp "$BACKEND/.env.example" "$BACKEND/.env"
    chmod 600 "$BACKEND/.env" 2>/dev/null || true
    bold "created backend/.env"
    note "set XTIMELINE_PASSWORD, and XTIMELINE_MONGO_URI if you have one"
  else
    note "backend/.env already exists — left alone"
  fi

  bold "done"
  note "next: ./run.sh session   (log into X once)"
  note "then: ./run.sh start"
}

cmd_session() {
  activate_venv
  bold "opening a browser — log into X, then press Enter here"
  (cd "$BACKEND" && python save_session.py)
}

cmd_start() {
  activate_venv
  if [ ! -f "$FRONTEND/dist/index.html" ]; then
    bold "no frontend/dist — building it first"
    (cd "$FRONTEND" && npm run build)
  fi
  bold "serving http://127.0.0.1:$PORT  (Ctrl-C to stop)"
  cd "$BACKEND"
  exec python run.py
}

cmd_dev() {
  activate_venv
  bold "backend on :$PORT, Vite on :5173 — Ctrl-C stops both"

  (cd "$BACKEND" && python run.py) &
  BACKEND_PID=$!

  cleanup() {
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  }
  trap cleanup EXIT INT TERM

  (cd "$FRONTEND" && npm run dev)
}

cmd_test() {
  activate_venv
  bold "backend tests"
  (cd "$BACKEND" && python -m pytest -q)

  bold "frontend build"
  (cd "$FRONTEND" && npm run build)

  bold "browser smoke test"
  note "drives the built UI in a real browser; stubs the extractor, so no login"
  (cd "$BACKEND" && python tests/smoke_ui.py)
}

cmd_mongo() {
  activate_venv
  (cd "$BACKEND" && python check_mongo.py)
}

cmd_help() {
  bold "X Timeline Extractor"
  note "usage: ./run.sh <command>"
  echo
  note "setup     create the venv, install dependencies, Chromium, .env, npm"
  note "session   log into X once and save the browser session"
  note "start     serve the built app on :$PORT"
  note "dev       backend + Vite dev server, with hot reload"
  note "test      backend tests, frontend build, browser smoke test"
  note "mongo     report on the history store"
  echo
  note "first run: ./run.sh setup && ./run.sh session && ./run.sh start"
}

case "${1:-help}" in
  setup)   cmd_setup ;;
  session) cmd_session ;;
  start)   cmd_start ;;
  dev)     cmd_dev ;;
  test)    cmd_test ;;
  mongo)   cmd_mongo ;;
  help|-h|--help) cmd_help ;;
  *)       die "unknown command '${1}' — try ./run.sh help" ;;
esac
