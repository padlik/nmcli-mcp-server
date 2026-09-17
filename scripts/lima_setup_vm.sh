#!/usr/bin/env bash
# VM-side (re-runnable) rig setup for the Lima VM `nsjail-test`.
# Installs NetworkManager + WireGuard tools and uv.
#
# Safety: NetworkManager is pinned to NOT manage the VM's default interface
# (eth0) BEFORE it is started, so Lima connectivity stays on systemd-networkd.
set -euo pipefail

DEFAULT_IFACE="${NMCLI_MCP_DEFAULT_IFACE:-eth0}"
TEST_USER="${NMCLI_MCP_VM_USER:-paulpronko}"

log() { printf '[lima-setup] %s\n' "$*"; }

if [[ $EUID -ne 0 ]]; then
    echo "run as root: sudo bash $0" >&2
    exit 1
fi

log "pinning ${DEFAULT_IFACE} as unmanaged by NetworkManager (before NM starts)"
install -d -m 0755 /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/99-unmanage-default-iface.conf <<EOF
# nmcli-mcp-server test rig: NetworkManager must never manage the VM's
# default interface; Lima connectivity stays on systemd-networkd.
[keyfile]
unmanaged-devices=interface-name:${DEFAULT_IFACE}
EOF

log "installing NetworkManager + WireGuard tools"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq network-manager wireguard-tools

log "allowing netdev group members to control networking from SSH sessions"
# The stock Ubuntu rule requires a local, active (seat) session — Lima SSH
# sessions satisfy neither, so unprivileged nmcli control would be denied.
# Scope: connection up/down only (network-control). System connection
# create/edit/delete (settings.modify.system) is NOT needed by the rig —
# the fixture script runs those as root.
install -d -m 0755 /etc/polkit-1/rules.d
cat > /etc/polkit-1/rules.d/49-nmcli-mcp-test-rig.rules <<'EOF'
// nmcli-mcp-server test rig: allow netdev members to activate/deactivate
// connections from any session (integration tests run over Lima SSH).
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.NetworkManager.network-control" &&
        subject.isInGroup("netdev")) {
        return polkit.Result.YES;
    }
});
EOF

log "enabling NetworkManager"
systemctl enable --now NetworkManager

log "verifying NetworkManager is responsive"
nmcli general status >/dev/null

log "verifying ${DEFAULT_IFACE} is unmanaged by NetworkManager"
state="$(nmcli -t -f DEVICE,STATE device | grep "^${DEFAULT_IFACE}:" | cut -d: -f2 || true)"
if [[ "$state" != "unmanaged" ]]; then
    echo "FATAL: ${DEFAULT_IFACE} is '${state}', expected 'unmanaged'" >&2
    exit 1
fi

log "verifying systemd-networkd still owns the link"
systemctl is-active --quiet systemd-networkd
ip -4 addr show dev "$DEFAULT_IFACE" | grep -q "inet "
ping -c1 -W3 1.1.1.1 >/dev/null

log "installing uv (user-local) for the integration test run"
if ! sudo -u "$TEST_USER" bash -lc 'command -v uv >/dev/null 2>&1'; then
    sudo -u "$TEST_USER" bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi
sudo -u "$TEST_USER" bash -lc 'command -v uv >/dev/null 2>&1' \
    || { echo "FATAL: uv still not on PATH for $TEST_USER" >&2; exit 1; }

log "VM-side setup complete"