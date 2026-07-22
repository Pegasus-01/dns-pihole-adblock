#!/usr/bin/env python3
"""
Local DNS Ad-Block Proxy — database-backed (SQLite), no external AI calls.
Intercepts DNS queries and decides BLOCK/ALLOW by walking the domain and its
parent suffixes against the `domains` table (see schema.sql).
"""

import asyncio
import logging
import socket
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import db

# ─── Config ────────────────────────────────────────────────────────────────

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 5335          # iptables redirects 53 -> 5335
                            # NOTE: 5353 is avoided on purpose — avahi-daemon (mDNS)
                            # binds 0.0.0.0:5353 by default on most desktop Linux
                            # installs, which would make bind() fail here.
UPSTREAM_DNS = "8.8.8.8"
UPSTREAM_PORT = 53

CACHE_SIZE = 4000
CACHE_TTL = 30               # short TTL: dashboard edits show up within this window,
                              # no proxy restart required
LOG_FILE = Path(__file__).parent / "logs" / "proxy.log"

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger("adblock-proxy")


# ─── Small in-process cache (avoids a DB round trip on every hot query) ────

@dataclass
class CacheEntry:
    verdict: str
    matched: Optional[str]
    source: str
    timestamp: float = field(default_factory=time.time)

    def is_fresh(self) -> bool:
        return (time.time() - self.timestamp) < CACHE_TTL


class LRUCache:
    def __init__(self, maxsize: int = CACHE_SIZE):
        self._cache: "OrderedDict[str, CacheEntry]" = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: str) -> Optional[CacheEntry]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        if not entry.is_fresh():
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return entry

    def set(self, key: str, entry: CacheEntry):
        self._cache[key] = entry
        self._cache.move_to_end(key)
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)


cache = LRUCache()


# ─── DNS packet helpers ────────────────────────────────────────────────────

def parse_domain_from_query(data: bytes) -> Optional[str]:
    try:
        idx = 12
        labels = []
        while idx < len(data):
            length = data[idx]
            if length == 0:
                break
            idx += 1
            labels.append(data[idx: idx + length].decode("ascii", errors="replace"))
            idx += length
        return ".".join(labels) if labels else None
    except Exception:
        return None


def build_nxdomain_response(query: bytes) -> bytes:
    txid = query[:2]
    flags = b"\x81\x83"  # QR=1 Response, RCODE=3 NXDOMAIN
    counts = b"\x00\x01\x00\x00\x00\x00\x00\x00"
    question = query[12:]
    return txid + flags + counts + question


async def forward_dns(query: bytes) -> bytes:
    loop = asyncio.get_event_loop()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(3)
        await loop.run_in_executor(None, lambda: sock.sendto(query, (UPSTREAM_DNS, UPSTREAM_PORT)))
        try:
            response, _ = await loop.run_in_executor(None, lambda: sock.recvfrom(4096))
            return response
        except socket.timeout:
            return build_nxdomain_response(query)


# ─── Core decision engine ──────────────────────────────────────────────────

def decide(conn, domain: str) -> tuple[str, Optional[str], str]:
    cached = cache.get(domain)
    if cached:
        return cached.verdict, cached.matched, cached.source

    verdict, matched, source = db.lookup(conn, domain)
    cache.set(domain, CacheEntry(verdict=verdict, matched=matched, source=source))
    return verdict, matched, source


# ─── UDP server ─────────────────────────────────────────────────────────────

class DNSProxyProtocol(asyncio.DatagramProtocol):
    def __init__(self, conn):
        self.conn = conn
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr):
        asyncio.create_task(self._handle(data, addr))

    async def _handle(self, data: bytes, addr):
        domain = parse_domain_from_query(data)
        if not domain:
            response = await forward_dns(data)
            self.transport.sendto(response, addr)
            return

        verdict, matched, source = decide(self.conn, domain)
        db.log_decision(self.conn, domain, verdict, matched, source)
        icon = "\U0001f6ab" if verdict == "BLOCK" else "✅"
        log.info(f"{icon} [{source:9s}] {verdict:5s}  {domain}" + (f"  (matched {matched})" if matched else ""))

        if verdict == "BLOCK":
            self.transport.sendto(build_nxdomain_response(data), addr)
        else:
            response = await forward_dns(data)
            self.transport.sendto(response, addr)


# ─── Entry point ─────────────────────────────────────────────────────────────

async def main():
    conn = db.connect()
    db.init_db(conn)

    log.info(f"Starting DB-backed Ad-Block DNS Proxy on {LISTEN_HOST}:{LISTEN_PORT}")
    log.info(f"Database: {db.DB_PATH}  |  Upstream DNS: {UPSTREAM_DNS}")

    loop = asyncio.get_event_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: DNSProxyProtocol(conn),
        local_addr=(LISTEN_HOST, LISTEN_PORT),
    )
    log.info("Proxy running. Press Ctrl+C to stop.")
    try:
        await asyncio.sleep(float("inf"))
    finally:
        transport.close()
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
