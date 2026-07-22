#!/usr/bin/env python3
"""
Import a local blocklist file into the SQLite domains table.

Nothing in this script downloads anything — point it at a file you already
have on disk (e.g. a StevenBlack/hosts, hagezi, or OISD list you downloaded
yourself).

Supported formats (auto-detected per line):
  - hosts file:   0.0.0.0 ads.example.com   /   127.0.0.1 ads.example.com
  - plain domain:  ads.example.com
  - AdBlock-style: ||ads.example.com^        (basic subset only)
  - comments (# ...) and blank lines are skipped

Usage:
  python3 import_blocklist.py --source stevenblack /path/to/hosts.txt
  python3 import_blocklist.py --source hagezi --list-type allow /path/to/allow.txt
"""

import argparse
import re
import sys
from pathlib import Path

import db

HOSTS_LINE_RE = re.compile(r"^(?:0\.0\.0\.0|127\.0\.0\.1|::1?)\s+([a-zA-Z0-9.\-]+)")
ADBLOCK_LINE_RE = re.compile(r"^\|\|([a-zA-Z0-9.\-]+)\^?$")
PLAIN_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)+$")

SKIP_DOMAINS = {"localhost", "local", "broadcasthost", "0.0.0.0"}


def parse_line(line: str) -> str | None:
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("!"):
        return None

    m = HOSTS_LINE_RE.match(line)
    if m:
        domain = m.group(1).lower()
    else:
        m = ADBLOCK_LINE_RE.match(line)
        if m:
            domain = m.group(1).lower()
        elif PLAIN_DOMAIN_RE.match(line):
            domain = line.lower()
        else:
            return None

    if domain in SKIP_DOMAINS:
        return None
    return domain


def load_domains(path: Path) -> list[str]:
    domains = []
    for line in path.read_text(errors="replace").splitlines():
        d = parse_line(line)
        if d:
            domains.append(d)
    return domains


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", type=Path, help="Path to a local blocklist file")
    ap.add_argument("--source", required=True, help="Label for this list, e.g. 'stevenblack', 'hagezi'")
    ap.add_argument("--list-type", choices=["block", "allow"], default="block")
    args = ap.parse_args()

    if not args.file.exists():
        print(f"error: file not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    domains = load_domains(args.file)
    if not domains:
        print("No domains parsed from that file — check the format.", file=sys.stderr)
        sys.exit(1)

    conn = db.connect()
    db.init_db(conn)
    inserted = db.import_domains(conn, domains, args.list_type, args.source)
    conn.close()

    print(f"Parsed {len(domains)} domains from {args.file}")
    print(f"Inserted {inserted} new '{args.list_type}' entries (source={args.source})")
    print(f"Skipped {len(domains) - inserted} (already present — manual overrides are never clobbered)")


if __name__ == "__main__":
    main()
