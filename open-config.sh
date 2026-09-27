#!/bin/bash
# Open the Turing Smart Screen configuration window.
# Uses the project virtualenv. The system has no `python` command, and the
# packages the window needs (sv_ttk, ruamel.yaml, pyserial) live in the venv.
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
exec "$DIR/venv/bin/python" "$DIR/configure.py" "$@"
