#!/usr/bin/env bash
# Host-side driver: run scripts/lima_setup_vm.sh inside the Lima VM.
set -euo pipefail

VM="${NMCLI_MCP_LIMA_VM:-nsjail-test}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "[lima-setup] running rig setup inside ${VM}"
limactl shell "$VM" -- sudo bash "${REPO_DIR}/scripts/lima_setup_vm.sh"
echo "[lima-setup] done — Lima connectivity verified by this very session"