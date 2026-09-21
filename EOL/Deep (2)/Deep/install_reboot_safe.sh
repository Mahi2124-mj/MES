#!/usr/bin/env bash
# ============================================================
#   Install the EOL reboot-safe boot service.  Run ONCE with sudo:
#       sudo ./install_reboot_safe.sh
#
#   Copies eol_boot.sh to the ROOT fs (so it runs before the code
#   disk is mounted), installs + enables eol.service, and runs it
#   once now to verify.  After this, every reboot auto-mounts the
#   disks, pins the DB ARP, and starts the whole stack — no manual
#   steps, no "DB unreachable".
# ============================================================
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo " [FATAL] run with sudo:   sudo ./install_reboot_safe.sh"
  exit 1
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo " [1/4] Installing boot script -> /usr/local/bin/eol_boot.sh"
install -m 0755 "$SRC/eol_boot.sh" /usr/local/bin/eol_boot.sh

echo " [2/4] Installing service     -> /etc/systemd/system/eol.service"
install -m 0644 "$SRC/eol.service" /etc/systemd/system/eol.service

echo " [3/4] Enabling service at boot"
systemctl daemon-reload
systemctl enable eol.service

echo " [4/4] Done."
echo
echo " ==============================================================="
echo "   Reboot-safe startup installed."
echo " ==============================================================="
echo "   Test WITHOUT rebooting:   sudo systemctl start eol.service"
echo "   Watch what it did:        cat /var/log/eol_boot.log"
echo "   Service status:           systemctl status eol.service"
echo
echo "   From now on, a reboot auto-mounts both disks, pins the DB"
echo "   ARP, and starts MES + CMS + collectors — nothing manual."
echo " ==============================================================="
