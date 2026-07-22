#!/usr/bin/env python3
"""
adblock control tool — talks directly to the SQLite database.

Usage:
  python3 ctl.py lookup <domain>          — show what the proxy would decide right now
  python3 ctl.py stats                    — aggregate stats
  python3 ctl.py top-blocked [N]          — top N blocked domains
  python3 ctl.py search <term>            — search decision log
  python3 ctl.py allow <domain>           — add to allowlist
  python3 ctl.py block <domain>           — add to blocklist
"""

import sys
from collections import Counter

import db


def cmd_lookup(domain: str):
    conn = db.connect()
    verdict, matched, source = db.lookup(conn, domain)
    conn.close()
    icon = "\U0001f6ab BLOCK" if verdict == "BLOCK" else "✅ ALLOW"
    print(f"\n  {domain}")
    print(f"  Result: {icon}")
    if matched:
        print(f"  Matched: {matched}  (source: {source})")
    else:
        print(f"  No list match — default allow")
    print()


def cmd_stats():
    conn = db.connect()
    total = conn.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"]
    if not total:
        print("No decisions logged yet.")
        return
    blocked = conn.execute("SELECT COUNT(*) c FROM decisions WHERE verdict='BLOCK'").fetchone()["c"]
    allowed = total - blocked
    sources = dict(conn.execute("SELECT source, COUNT(*) c FROM decisions GROUP BY source").fetchall())
    conn.close()
    print(f"\nStats ({total} total decisions)")
    print(f"  Allowed : {allowed} ({allowed/total*100:.1f}%)")
    print(f"  Blocked : {blocked} ({blocked/total*100:.1f}%)")
    print(f"  Sources : {sources}\n")


def cmd_top_blocked(n: int = 20):
    conn = db.connect()
    rows = conn.execute(
        "SELECT domain, COUNT(*) c FROM decisions WHERE verdict='BLOCK' GROUP BY domain ORDER BY c DESC LIMIT ?",
        (n,),
    ).fetchall()
    conn.close()
    if not rows:
        print("No blocked domains yet.")
        return
    print(f"\nTop {n} Blocked Domains:")
    for r in rows:
        print(f"  {r['c']:>5}x  {r['domain']}")
    print()


def cmd_search(term: str):
    conn = db.connect()
    rows = conn.execute(
        "SELECT ts, domain, verdict, source FROM decisions WHERE domain LIKE ? ORDER BY id DESC LIMIT 40",
        (f"%{term}%",),
    ).fetchall()
    conn.close()
    print(f"\nSearch '{term}' — {len(rows)} results:")
    for r in rows:
        icon = "\U0001f6ab" if r["verdict"] == "BLOCK" else "✅"
        print(f"  {icon} {r['ts'][:19]}  {r['verdict']:<5}  {r['domain']}  [{r['source']}]")
    print()


def cmd_add(domain: str, list_type: str):
    conn = db.connect()
    db.init_db(conn)
    db.add_domain(conn, domain, list_type, source="manual")
    conn.close()
    print(f"Added {domain} to {list_type}list")


def usage():
    print(__doc__)
    sys.exit(1)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        usage()
    cmd = args[0]
    if cmd == "lookup" and len(args) == 2:
        cmd_lookup(args[1])
    elif cmd == "stats":
        cmd_stats()
    elif cmd == "top-blocked":
        cmd_top_blocked(int(args[1]) if len(args) > 1 else 20)
    elif cmd == "search" and len(args) == 2:
        cmd_search(args[1])
    elif cmd == "allow" and len(args) == 2:
        cmd_add(args[1], "allow")
    elif cmd == "block" and len(args) == 2:
        cmd_add(args[1], "block")
    else:
        usage()
