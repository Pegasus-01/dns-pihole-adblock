#!/usr/bin/env bash
# Redirects local DNS queries (port 53 UDP/TCP) to the proxy on port 5335.
# Run as root: sudo bash config/setup-iptables.sh
#
# NOTE: the proxy listens on 5335, not 5353 — 5353 is avahi-daemon's mDNS
# port on most desktop Linux installs and is already bound.

set -euo pipefail

ACTION="${1:-add}"   # add | remove
PROXY_PORT=5335

if [[ "$ACTION" == "add" ]]; then
    echo "[+] Redirecting UDP/TCP port 53 → $PROXY_PORT for local DNS..."
    iptables -t nat -A OUTPUT -p udp --dport 53 -j REDIRECT --to-ports "$PROXY_PORT"
    iptables -t nat -A OUTPUT -p tcp --dport 53 -j REDIRECT --to-ports "$PROXY_PORT"
    echo "[+] Done. DNS queries from this machine now go through the ad-block proxy."
elif [[ "$ACTION" == "remove" ]]; then
    echo "[-] Removing DNS redirect rules..."
    iptables -t nat -D OUTPUT -p udp --dport 53 -j REDIRECT --to-ports "$PROXY_PORT" 2>/dev/null || true
    iptables -t nat -D OUTPUT -p tcp --dport 53 -j REDIRECT --to-ports "$PROXY_PORT" 2>/dev/null || true
    echo "[-] Done. DNS is back to normal."
else
    echo "Usage: $0 [add|remove]"
    exit 1
fi
