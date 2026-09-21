#!/usr/bin/env bash
# ============================================================
#   EOL LINUX PROVISIONING
#
#   Run ONCE with sudo:   sudo ./provision_linux.sh
#
#   Installs what start_everything.sh needs and builds the Linux
#   venv.  The venv is created as the INVOKING user (not root) so
#   you can later pip-install without sudo.
#
#   Installs nothing on the factory network and touches no DB.
# ============================================================
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo " [FATAL] run with sudo:   sudo ./provision_linux.sh"
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MES_DIR="$ROOT/Phase2"
REAL_USER="${SUDO_USER:-root}"
VENV="$MES_DIR/.venv-linux"

echo " ==============================================================="
echo "   EOL Linux provisioning"
echo "   target user: $REAL_USER"
echo " ==============================================================="
echo

# --- 0. Force IPv4 ----------------------------------------------
# This box resolves archive.ubuntu.com to AAAA records (2620:2d:...)
# but has NO IPv6 default route — so apt opens an IPv6 socket and
# stalls until timeout, showing zero progress.  Pin apt to IPv4.
APT_OPTS=(-o Acquire::ForceIPv4=true)

# --- 1. Package index ------------------------------------------
echo " [1/5] apt update..."
apt-get "${APT_OPTS[@]}" update

# --- 2. Pick a Python -------------------------------------------
# requirements.txt pins fastapi 0.110 / pydantic 2.6 / psycopg2-binary
# 2.9.9 — none have wheels for 3.14 (pydantic-core would need a Rust
# toolchain).  Prefer 3.12; fall back to 3.13, then deadsnakes.
echo " [2/5] Selecting Python..."
PYVER=""
for v in 3.12 3.13; do
  if apt-cache show "python${v}" >/dev/null 2>&1; then PYVER="$v"; break; fi
done

if [[ -z "$PYVER" ]]; then
  echo "       No python3.12/3.13 in the configured repos."
  echo "       Adding deadsnakes PPA..."
  apt-get "${APT_OPTS[@]}" install -y software-properties-common
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get "${APT_OPTS[@]}" update
  PYVER="3.12"
fi
echo "       Using python${PYVER}"

# --- 3. System packages -----------------------------------------
# NOT -qq: this pulls ~250-300 MB and silence is indistinguishable
# from a hang (which is exactly what happened on the first run).
echo " [3/5] Installing packages (~250-300 MB, progress shown)..."
DEBIAN_FRONTEND=noninteractive \
apt-get "${APT_OPTS[@]}" install -y \
  "python${PYVER}" "python${PYVER}-venv" "python${PYVER}-dev" \
  build-essential libpq-dev \
  nodejs npm \
  psmisc iproute2
#   psmisc  -> fuser   (used by start/stop for port cleanup)
#   iproute2-> ss      (used by the 5656 idempotency check)
#   libpq-dev + build-essential -> psycopg2 build fallback if no wheel

echo "       node:  $(node --version 2>/dev/null || echo MISSING)"
echo "       npm:   $(npm --version 2>/dev/null || echo MISSING)"

# --- 4. Build the venv as the real user -------------------------
echo " [4/5] Building Linux venv at $VENV ..."
if [[ -d "$VENV" ]]; then
  echo "       already exists — leaving it alone (delete it to rebuild)"
else
  sudo -u "$REAL_USER" "python${PYVER}" -m venv "$VENV"
fi

echo "       Installing requirements (progress shown)..."
# Also not quiet, same reasoning as the apt step.  --timeout guards
# against the link stalling the way apt did.
sudo -u "$REAL_USER" "$VENV/bin/pip" install --timeout 60 --upgrade pip
sudo -u "$REAL_USER" "$VENV/bin/pip" install --timeout 60 -r "$MES_DIR/requirements.txt"

# --- 5. Verify ---------------------------------------------------
echo " [5/5] Verifying imports..."
sudo -u "$REAL_USER" "$VENV/bin/python" - <<'PYEOF'
mods = ["fastapi", "uvicorn", "psycopg2", "pydantic",
        "dotenv", "requests", "openpyxl", "pymcprotocol"]
bad = []
for m in mods:
    try:
        __import__(m)
    except Exception as e:
        bad.append(f"{m}: {e}")
if bad:
    print("  MISSING:")
    for b in bad:
        print("   -", b)
    raise SystemExit(1)
print("  all imports OK")
PYEOF

echo
echo " ==============================================================="
echo "   Provisioning complete."
echo " ==============================================================="
echo "   Next:  ./start_everything.sh          (API + dashboard only)"
echo
echo "   Collectors stay gated until the Windows box is down."
echo "   See start_everything.sh --help"
echo " ==============================================================="
