# 🛡️ Ad-Block DNS Proxy — Project Overview

**SQLite-backed, Pi-hole-style DNS ad/tracker blocker with a live web dashboard**

[![Repo](https://img.shields.io/badge/GitHub-dns--pihole--adblock-181717?logo=github)](https://github.com/Pegasus-01/dns-pihole-adblock)
![Python](https://img.shields.io/badge/Python-43%25-3776AB?logo=python&logoColor=white)
![HTML](https://img.shields.io/badge/HTML-54%25-E34F26?logo=html5&logoColor=white)
![Shell](https://img.shields.io/badge/Shell-3%25-4EAA25?logo=gnubash&logoColor=white)

> Repository: **[github.com/Pegasus-01/dns-pihole-adblock](https://github.com/Pegasus-01/dns-pihole-adblock)**

A local DNS proxy that blocks ad and tracker domains for a machine's
outgoing traffic, backed by a SQLite database of domain lists — the same
architectural model as **Pi-hole** or **AdGuard Home**. It ships with a
Flask-based web dashboard for live stats, decision-log search, and manual
allow/block overrides that take effect without a restart.

This document is the full narrative description of the project: what it
does, why it's built the way it is, how it's laid out internally, and how
to operate it end-to-end — install, run, stop, and fully roll back. For a
terser step-by-step checklist, see the repository's
[README.md](https://github.com/Pegasus-01/dns-pihole-adblock/blob/main/README.md).

---

## Table of contents

1. [What this project does](#1-what-this-project-does)
2. [Background — why this exists in this form](#2-background--why-this-exists-in-this-form)
3. [Architecture](#3-architecture)
4. [Project layout](#4-project-layout)
5. [Installing](#5-installing)
6. [Populating the database](#6-populating-the-database-the-pi-hole-style-part)
7. [Running](#7-running)
8. [Full rollback — in order, with reasoning](#8-full-rollback--in-order-with-reasoning)
9. [Known environmental quirks](#9-known-environmental-quirks)
10. [Differences from the original Ollama-based version](#10-differences-from-the-original-ollama-based-version)
11. [Quick reference — CLI (`ctl.py`)](#11-quick-reference--cli-ctlpy)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. What this project does

It sits between your machine and the network at the DNS layer:

```text
Your app / browser
      │  DNS query for e.g. ads.doubleclick.net  (port 53)
      ▼
iptables NAT (OUTPUT chain)
      │  redirected → 127.0.0.1:5335
      ▼
proxy.py  ──  looks up the domain (and its parent suffixes) in a SQLite table
      │
      ├── found on the blocklist  → respond NXDOMAIN (the ad/tracker never loads)
      └── not blocked             → forward the query to a real upstream resolver (8.8.8.8)
```

Every decision is logged to the same database, and a Flask dashboard at
`http://localhost:8080` reads that log and lets you search it, view
aggregate stats, and add manual allow/block entries that the proxy picks
up within seconds — no restart required.

---

## 2. Background — why this exists in this form

The project started as an **LLM-based** ad blocker: DNS queries for unknown
domains were sent to a local Ollama model, which decided BLOCK or ALLOW
per query. That version had real, structural problems:

- **Latency.** Every new domain cost 2–5 seconds waiting on the LLM, which
  browsers tolerate poorly — pages with dozens of first-seen subdomains
  would stall or time out mid-load.
- **Non-determinism.** The same domain could get different verdicts
  between runs or model versions, with no ground truth to audit against.
- **A dead manual-override path.** The web dashboard wrote allow/block
  edits to JSON files that the proxy never actually read, so every manual
  correction made through the UI silently did nothing.
- **Two disconnected dashboards.** A terminal UI and a separate Flask app,
  sharing no code or data model, glued together only by a hardcoded path
  assumption (`/opt/ai-adblock`).
- **A port conflict baked into the defaults.** The proxy listened on UDP
  5353, which is also where `avahi-daemon` (mDNS) binds by default on most
  desktop Linux installs — confirmed live during development, `bind()`
  failed with `Address already in use` until the port was changed.
- **An exposed control plane.** The dashboard bound to `0.0.0.0:8080` with
  wide-open CORS and no authentication, meaning anyone on the LAN could
  read DNS/browsing history and rewrite the block/allow lists.
- **Unbounded log growth.** The decision log (`decisions.jsonl`) had no
  rotation or cap.

The current version replaces the LLM decision engine with a **SQLite
database of known domains**, imported from public blocklists — the same
kind Pi-hole, AdGuard Home, and uBlock Origin use — and fixes each of the
issues above directly in the redesign. See [§10](#10-differences-from-the-original-ollama-based-version)
for the full list of what changed.

---

## 3. Architecture

### 3.1 Components

| File | Role |
|---|---|
| `proxy.py` | asyncio UDP DNS server. Parses each query, decides BLOCK/ALLOW via `db.py`, forwards allowed queries upstream or returns NXDOMAIN for blocked ones. Keeps a short-TTL in-memory cache so hot lookups don't hit SQLite every time. |
| `db.py` | Shared SQLite access layer used by both the proxy and the dashboard. Owns the schema, the domain-suffix lookup algorithm, and decision-log writes/trimming. |
| `schema.sql` | Two tables: `domains` (block/allow list, one row per domain) and `decisions` (query log). |
| `import_blocklist.py` | CLI that parses a locally-downloaded blocklist file (hosts-file, plain-domain, or basic AdBlock syntax) and bulk-inserts it into `domains`. |
| `dashboard_server.py` | Flask app serving the web dashboard and a JSON API over the same database — stats, paginated decision search, allow/block list CRUD. |
| `static/index.html` | The dashboard's single-page UI (vanilla JS + Chart.js, dark theme). |
| `ctl.py` | Command-line companion to the dashboard: `lookup`, `stats`, `top-blocked`, `search`, `allow`, `block` — useful when you don't want to open a browser. |
| `config/setup-iptables.sh` | Adds/removes the NAT redirect that sends port-53 traffic into the proxy. |
| `systemd/*.service` | Unit files to run the proxy and dashboard as background services. |

### 3.2 Data model

```sql
domains (
  domain     TEXT PRIMARY KEY,   -- e.g. 'doubleclick.net'
  list_type  TEXT,               -- 'block' or 'allow'
  source     TEXT,               -- 'manual', or whatever --source label you
                                  -- gave import_blocklist.py, e.g. 'stevenblack'
  added_at   TEXT
)

decisions (
  id             INTEGER PRIMARY KEY,
  ts             TEXT,
  domain         TEXT,           -- the domain actually queried
  verdict        TEXT,           -- 'BLOCK' or 'ALLOW'
  matched_domain TEXT,           -- which entry in `domains` matched, if any
  source         TEXT            -- source of that entry, or 'default' if none matched
)
```

`domain` is a primary key, so **a domain can only be on one list at a
time** — adding it to the allowlist implicitly removes any blocklist
entry for it, and vice versa. This is what makes manual overrides
unambiguous: there's never a conflicting block-and-allow pair to resolve.

### 3.3 Lookup algorithm — suffix matching

For a query like `ads.doubleclick.net`, the proxy checks, in order:

```text
ads.doubleclick.net
doubleclick.net
net
```

against the `domains` table, stopping at the first match. This means a
single blocklist entry for `doubleclick.net` also blocks every subdomain
of it, without needing a separate row per subdomain — the same approach
Pi-hole's gravity database and AdGuard Home use. If nothing matches at any
level, the default verdict is ALLOW.

### 3.4 Why SQLite specifically

- **Single file, no server process to manage** — appropriate for a
  workload that's read-heavy (one lookup per DNS query) with occasional
  bulk writes (list imports) and rare small writes (manual overrides).
- **WAL mode** lets the proxy and dashboard — two separate OS processes —
  read and write concurrently without lock contention.
- **Indexed exact-match lookups** on `domain` (the primary key) are
  effectively O(log n), so the suffix-walk in [§3.3](#33-lookup-algorithm--suffix-matching)
  costs at most a handful of indexed lookups per query, never a table
  scan.
- Pi-hole itself moved to SQLite (`gravity.db`) for the same reasons.

---

## 4. Project layout

```text
adblock/
  proxy.py               DNS proxy (asyncio UDP server, SQLite lookups)
  dashboard_server.py    Flask web dashboard — http://localhost:8080
  db.py                  Shared SQLite access layer
  schema.sql             domains + decisions tables
  import_blocklist.py    CLI to import a local blocklist file into the DB
  ctl.py                 CLI: lookup / stats / search / allow / block
  static/index.html      Dashboard UI
  config/setup-iptables.sh
  systemd/
    adblock-proxy.service
    adblock-dashboard.service
  start_dashboard.sh
  requirements.txt
  data/adblock.db        Created at first run — not tracked in git
  logs/proxy.log         Created at first run — not tracked in git
```

---

## 5. Installing

```bash
cd adblock
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
chmod +x config/setup-iptables.sh
```

On Debian/Kali-family systems that enforce PEP 668 ("externally managed
environment"), the venv above is required — don't `pip install` outside
it.

---

## 6. Populating the database (the Pi-hole-style part)

This project does not download anything on its own — you fetch a
blocklist file yourself, then import it:

```bash
wget -O ~/hosts.txt https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts
python3 import_blocklist.py --source stevenblack ~/hosts.txt
```

Good source lists:

| List | Notes |
|---|---|
| [StevenBlack/hosts](https://github.com/StevenBlack/hosts) | Most widely used aggregate; good balance of coverage vs. false positives. Also what Pi-hole's own default list is derived from. |
| [hagezi/dns-blocklists](https://github.com/hagezi/dns-blocklists) | Actively maintained, tiered (light/normal/pro/ultimate), plus separate tracker/malware lists. |
| [OISD](https://oisd.nl/) | Aggregate specifically tested to minimize false positives. |

You can import multiple lists, each tagged with its own `--source` label:

```bash
python3 import_blocklist.py --source hagezi-normal ~/hagezi-normal.txt
python3 import_blocklist.py --source oisd ~/oisd-big.txt
```

Re-importing an updated copy of a list is always safe — the importer uses
`INSERT OR IGNORE`, so it never overwrites a domain you've already placed
on a list manually (via the dashboard or `ctl.py`).

To add a personal allowlist file:

```bash
python3 import_blocklist.py --source my-allowlist --list-type allow ~/allow.txt
```

---

## 7. Running

### 7.1 Foreground (manual / testing)

```bash
source venv/bin/activate
python3 proxy.py &            # DNS proxy on 127.0.0.1:5335
python3 dashboard_server.py & # or ./start_dashboard.sh — dashboard on :8080
```

Test the proxy directly before touching system DNS:

```bash
dig @127.0.0.1 -p 5335 google.com          # ALLOW → returns an IP
dig @127.0.0.1 -p 5335 doubleclick.net     # BLOCK → NXDOMAIN (once imported)
python3 ctl.py lookup ads.doubleclick.net
```

**Stop:**

```bash
pkill -f 'adblock/proxy.py'
pkill -f 'adblock/dashboard_server.py'
```

### 7.2 As systemd services (recommended for long-term use)

```bash
sudo cp systemd/adblock-proxy.service /etc/systemd/system/
sudo cp systemd/adblock-dashboard.service /etc/systemd/system/
sudo nano /etc/systemd/system/adblock-proxy.service       # set WorkingDirectory/ExecStart/User
sudo nano /etc/systemd/system/adblock-dashboard.service   # same

sudo systemctl daemon-reload
sudo systemctl enable --now adblock-proxy
sudo systemctl enable --now adblock-dashboard
sudo systemctl status adblock-proxy
sudo journalctl -u adblock-proxy -f
```

**Stop:**

```bash
sudo systemctl stop adblock-proxy adblock-dashboard
sudo systemctl disable adblock-proxy adblock-dashboard
```

`disable` matters as well as `stop` — without it the units still start
automatically on the next boot.

### 7.3 Enabling system-wide DNS redirection

The proxy only affects traffic once port 53 is actually redirected to it:

```bash
# First, make sure nothing else holds port 53:
sudo ss -ulnp | grep :53

# If systemd-resolved is listed:
sudo nano /etc/systemd/resolved.conf   # set DNSStubListener=no
sudo systemctl restart systemd-resolved

# If dnsmasq is listed instead:
sudo systemctl stop dnsmasq && sudo systemctl disable dnsmasq

# Then:
sudo bash config/setup-iptables.sh add
sudo iptables -t nat -L OUTPUT -n --line-numbers | grep REDIRECT
dig google.com   # now transparently goes through the proxy
```

This uses the `OUTPUT` chain, so it filters DNS **originating from this
machine only** — it does not filter other devices on the network unless
the proxy is separately configured to listen on a LAN interface and other
devices' DNS is pointed at it.

To persist the iptables rule across reboots:

```bash
sudo apt install -y iptables-persistent
sudo netfilter-persistent save
```

---

## 8. Full rollback — in order, with reasoning

Rollback is the reverse order of setup, because each step depends on
state the previous one created.

**1. Remove the iptables DNS redirect — first, always**

```bash
sudo bash config/setup-iptables.sh remove
sudo iptables -t nat -L OUTPUT -n --line-numbers | grep REDIRECT   # confirm empty
```

*Why first:* while this rule exists, every DNS query on the machine is
sent to 127.0.0.1:5335. Stopping the proxy process *before* removing this
rule breaks all DNS resolution on the machine — queries would be
redirected to a target that's no longer listening. Removing the redirect
first restores normal DNS immediately, independent of whether the proxy
process is still up.

**2. Re-save (or remove) the persisted iptables rule**

```bash
sudo netfilter-persistent save
# or, if you don't want it managed at all:
sudo apt remove iptables-persistent
```

*Why:* `netfilter-persistent save` snapshots whatever rules exist *right
now*. If skipped, the just-removed REDIRECT rule would silently reappear
on the next reboot from the old saved snapshot.

**3. Stop and disable the services**

```bash
sudo systemctl stop adblock-proxy adblock-dashboard
sudo systemctl disable adblock-proxy adblock-dashboard
```

*Why after step 1:* now that traffic isn't being routed to the proxy,
it's safe to stop it — nothing depends on it being up anymore. `disable`
prevents it silently returning on next boot.

**4. Remove the systemd unit files**

```bash
sudo rm /etc/systemd/system/adblock-proxy.service
sudo rm /etc/systemd/system/adblock-dashboard.service
sudo systemctl daemon-reload
```

*Why:* `disable` only removes the boot-time symlink — the unit file is
still on disk and `systemctl start adblock-proxy` would still work by
accident. Deleting the files plus `daemon-reload` makes systemd forget the
units existed.

**5. Restore whatever was disabled to free port 53**

```bash
sudo nano /etc/systemd/resolved.conf   # set DNSStubListener=yes (or delete the line)
sudo systemctl restart systemd-resolved
# or, if dnsmasq was stopped instead:
sudo systemctl enable --now dnsmasq
```

*Why:* this reverses the one system-level (not project-specific) change
made during setup. Skipping it leaves the OS's own resolver stub disabled
even after the ad-block proxy itself is gone.

**6. Confirm DNS is fully normal again**

```bash
dig google.com
resolvectl status   # or: cat /etc/resolv.conf
```

Should resolve with no involvement of 127.0.0.1:5335 anywhere.

**7. (Optional) remove the project files and database**

```bash
rm -rf ~/adblock
```

*Why last, and optional:* by this point `data/adblock.db` and
`logs/proxy.log` are just inert local files — nothing else on the system
references them. Only do this if you're sure you don't want the imported
domain lists or decision history anymore; otherwise leave the folder in
place and you can restart everything later from the same state.

**Sanity check:** `sudo ss -ulnp | grep :53` should show your normal
system resolver (or nothing, if the stub was already off before you
started) — not the ad-block proxy, and not "nothing is listening at all"
(which would mean DNS is still broken).

---

## 9. Known environmental quirks

- **Port 5335, not 5353** — the proxy listens on **5335** on purpose.
  `avahi-daemon` (mDNS) binds `0.0.0.0:5353` by default on most desktop
  Linux installs, which would make the proxy's `bind()` fail.
- **Local/ISP-gateway domains (e.g. `*.attlocal.net`)** — some
  ISP-provided routers (AT&T gateways in particular) use a local-only
  domain suffix for on-network device names. These don't exist in public
  DNS, so forwarding them to `8.8.8.8` will always fail even though
  they're not ads. This needs conditional forwarding to the router's own
  DNS instead of the public upstream — **not yet implemented in
  `proxy.py`** as of this writing; flag it if you want it added.
- **iptables `OUTPUT` chain is per-machine only** — it does not make this
  a network-wide ad-blocker for other devices without further changes
  (proxy listening on a LAN interface, other devices' DNS pointed at it).

---

## 10. Differences from the original Ollama-based version

- **No LLM dependency** — decisions come from indexed SQLite lookups
  (sub-millisecond, not 2–5 seconds per new domain).
- **Deterministic and auditable** — the same domain always gets the same
  verdict until the list changes, and every verdict traces back to a
  specific list entry (`matched_domain` + `source`).
- **Manual overrides actually work now** — the dashboard's allow/block
  edits write to the same database the proxy reads, and land within the
  proxy's 30-second cache TTL with no restart needed. (Previously written
  to JSON files the proxy never read at all.)
- **One merged web dashboard** instead of two disconnected ones (a
  terminal UI and a separate, differently-pathed Flask app).
- **Bounded logging** — the decision log is capped at 50,000 rows and
  self-trims, instead of an ever-growing `decisions.jsonl`.
- **Locked-down control plane** — the dashboard binds to `127.0.0.1` only
  and drops the previous wide-open CORS policy. The old setup exposed DNS
  and browsing history plus full list control to anyone on the LAN, with
  no authentication.

---

## 11. Quick reference — CLI (`ctl.py`)

```bash
python3 ctl.py lookup ads.example.com   # what would the proxy decide right now
python3 ctl.py stats
python3 ctl.py top-blocked 20
python3 ctl.py search amazon
python3 ctl.py allow example.com
python3 ctl.py block ads.example.com
```

---

## 12. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Proxy starts but DNS stops working entirely | `dig @127.0.0.1 -p 5335 google.com` — if that works, the iptables rule is the problem. Run `sudo bash config/setup-iptables.sh remove` to restore, then re-check. |
| A site is wrongly blocked | `python3 ctl.py lookup thesite.com` to see which list entry matched, then `python3 ctl.py allow thesite.com` to override it. |
| Port 5335 already in use | `sudo ss -ulnp \| grep 5335` to find what's holding it. |
| Proxy fails to bind with "Address already in use" on port 5353 | This is why the proxy uses 5335 by default — `avahi-daemon` binds `0.0.0.0:5353` on most desktop Linux installs. If `LISTEN_PORT` is changed back to 5353, either stop avahi-daemon or pick a different port. |
| Database locked errors | Both the proxy and dashboard use WAL mode so they can read/write concurrently; this shouldn't happen under normal use. If it does, check nothing else is holding an open transaction on `data/adblock.db`. |

---

*For the shorter, checklist-style version of installation, see
[README.md](https://github.com/Pegasus-01/dns-pihole-adblock/blob/main/README.md)
in the repository.*
