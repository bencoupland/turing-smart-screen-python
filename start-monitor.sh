#!/bin/bash
# Start the Turing Smart Screen monitor with its virtualenv.
# After login the USB display can take a few seconds to enumerate.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

for _ in $(seq 1 30); do
    if compgen -G "/dev/ttyACM*" > /dev/null; then
        break
    fi
    sleep 1
done

exec "$DIR/venv/bin/python" "$DIR/main.py" "$@"
