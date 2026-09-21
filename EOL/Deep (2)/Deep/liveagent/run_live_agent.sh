#!/usr/bin/env bash
# Wrapper so systemd never has to deal with the spaces in this install path
# ("server backup", "Deep (2)") — quoting them in ExecStart fails 203/EXEC.
ROOT="/run/media/server/5b4e6f01-4c04-4bc3-b34e-b0df376c067f/server backup/D DRIVE/EOL/EOL/Deep (2)/Deep"
cd "$ROOT" || exit 1
exec "$ROOT/Phase2/.venv-linux/bin/python" "$ROOT/liveagent/live_agent.py" \
     --interval "${LIVE_AGENT_INTERVAL:-30}" --port "${LIVE_AGENT_PORT:-8097}"
