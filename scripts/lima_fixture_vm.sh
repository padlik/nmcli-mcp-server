#!/usr/bin/env bash
# VM-side (re-runnable) test-fixture setup for the Lima VM `nsjail-test`.
# Creates the fake WireGuard "Test VPN" NM connection carrying the expected
# routes, plus the TOML fixture config used by the integration tests.
set -euo pipefail

TEST_USER="${NMCLI_MCP_VM_USER:-paulpronko}"
# Lima VM users have a ".guest"-suffixed home directory; never assume /home/<user>.
TEST_HOME="$(getent passwd "$TEST_USER" | cut -d: -f6)"
FIXTURE_DIR="${NMCLI_MCP_FIXTURE_DIR:-${TEST_HOME}/nmcli-mcp-fixture}"
CON_NAME="Test VPN"

log() { printf '[lima-fixture] %s\n' "$*"; }

if [[ $EUID -ne 0 ]]; then
    echo "run as root: sudo bash $0" >&2
    exit 1
fi

log "ensuring ${TEST_USER} can control NetworkManager (netdev group)"
usermod -aG netdev "$TEST_USER"

log "(re-)creating WireGuard connection '${CON_NAME}'"
nmcli connection delete "$CON_NAME" >/dev/null 2>&1 || true

# CAUTION: the generated private key is passed as nmcli argv, so it is
# briefly visible in `ps` to other local users, and it is stored inside the
# NM system connection. Acceptable ONLY because this is a throwaway key for
# a peerless fake tunnel in a single-user test VM — never reuse this pattern
# for a real WireGuard key.
PRIV_KEY="$(wg genkey)"

nmcli connection add \
    type wireguard \
    ifname wg0 \
    con-name "$CON_NAME" \
    wireguard.private-key "$PRIV_KEY" \
    ipv4.method manual \
    ipv4.addresses "10.99.0.2/24" \
    ipv4.routes "10.13.0.0/16, 10.12.0.0/16, 10.8.0.5/32" \
    ipv4.never-default yes \
    connection.autoconnect no

log "writing fixture config to ${FIXTURE_DIR}/config.toml"
sudo -u "$TEST_USER" mkdir -p "$FIXTURE_DIR"
cat > "${FIXTURE_DIR}/config.toml" <<'EOF'
# Fixture for integration tests: id -> NM connection mapping plus the
# routes the fake WireGuard "Test VPN" connection carries in this VM.
[[vpn]]
id = "testvpn"
connection = "Test VPN"
expected_routes = ["10.13.0.0/16", "10.12.0.0/16", "10.8.0.5/32"]
EOF
chown "$TEST_USER": "$FIXTURE_DIR" "${FIXTURE_DIR}/config.toml"

log "fixture setup complete"