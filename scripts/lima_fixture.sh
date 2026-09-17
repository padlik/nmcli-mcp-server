#!/usr/bin/env bash
# Host-side driver: run scripts/lima_fixture_vm.sh inside the Lima VM.
set -euo pipefail

VM="${NMCLI_MCP_LIMA_VM:-nsjail-test}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "[lima-fixture] running fixture setup inside ${VM}"
limactl shell "$VM" -- sudo bash "${REPO_DIR}/scripts/lima_fixture_vm.sh"
echo "[lima-fixture] done"