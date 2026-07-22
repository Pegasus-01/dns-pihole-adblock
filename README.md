# 🛡️ Ad-Block DNS Proxy (SQLite-backed)

A local DNS proxy that blocks ad/tracker domains using a SQLite database,
instead of asking an LLM. Same DNS-interception architecture as before
(iptables redirects port 53 → the proxy), but decisions are now instant,
deterministic, and auditable — same model as Pi-hole / AdGuard Home.

```
Your app → DNS query (port 53)
         → iptables redirects to port 5335
         → proxy.py looks up domain + parent suffixes in domains table
         → BLOCK (NXDOMAIN) or ALLOW (forward to 8.8.8.8)
```

Manual allow/block edits made in the dashboard write straight to the same
database the proxy reads — no restart needed, changes land within 30s (the
proxy's in-memory cache TTL).

---

## Project layout

```
adblock/
  proxy.py             DNS proxy (asyncio UDP server, SQLite lookups)
  dashboard_server.py  Flask web dashboard — http://localhost:8080
  db.py                shared SQLite access layer
  schema.sql           domains + decisions tables
  import_blocklist.py  CLI to import a local blocklist file into the DB
  ctl.py               CLI: lookup / stats / search / allow / block
  static/index.html    dashboard UI
  config/setup-iptables.sh
  systemd/*.service
  data/adblock.db      created at first run (gitignored)
  logs/proxy.log       created at first run (gitignored)
```

---

## Step 1 — Install dependencies

```bash
cd adblock
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
chmod +x config/setup-iptables.sh
```

---

## Step 2 — Get a blocklist file and import it

This project does **not** download anything for you. Grab a list yourself
and import it locally:

- [StevenBlack/hosts](https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts) (hosts-file format, recommended default)
- [hagezi/dns-blocklists](https://github.com/hagezi/dns-blocklists) (multi-tier, more aggressive)
- [OISD](https://oisd.nl/) (false-positive tested)

Save the file locally, then:

```bash
python3 import_blocklist.py --source stevenblack /path/to/hosts.txt
```

Re-running the importer with an updated copy of the same list is safe —
`import_blocklist.py` uses `INSERT OR IGNORE`, so it never overwrites a
domain you (or the dashboard) have manually placed on the allow/block list.

You can import as many separate lists as you want, each tagged with its own
`--source` label, e.g.:

```bash
python3 import_blocklist.py --source hagezi-tif /path/to/tif.txt
python3 import_blocklist.py --source oisd /path/to/oisd.txt
```

To add a personal allowlist file (domains that should always resolve):

```bash
python3 import_blocklist.py --source my-allowlist --list-type allow /path/to/allow.txt
```

---

## Step 3 — Disable systemd-resolved's stub listener

Ubuntu's `systemd-resolved` already listens on port 53.

```bash
sudo nano /etc/systemd/resolved.conf
```

Set:
```ini
[Resolve]
DNSStubListener=no
```

```bash
sudo systemctl restart systemd-resolved
sudo ss -ulnp | grep :53   # should show nothing
```

---

## Step 4 — Test the proxy without iptables

```bash
python3 proxy.py
```

In another terminal:

```bash
dig @127.0.0.1 -p 5335 google.com         # ALLOW → returns an IP
dig @127.0.0.1 -p 5335 doubleclick.net    # BLOCK → NXDOMAIN (once imported)

python3 ctl.py lookup ads.doubleclick.net
python3 ctl.py lookup github.com
```

---

## Step 5 — Redirect system DNS through the proxy

```bash
sudo bash config/setup-iptables.sh add
sudo iptables -t nat -L OUTPUT -n --line-numbers | grep REDIRECT
```

---

## Step 6 — (Optional) Run as systemd services

```bash
sudo cp systemd/adblock-proxy.service /etc/systemd/system/
sudo cp systemd/adblock-dashboard.service /etc/systemd/system/
# Edit both: set WorkingDirectory / ExecStart to your actual install path,
# and change User=%i to your username.
sudo nano /etc/systemd/system/adblock-proxy.service
sudo nano /etc/systemd/system/adblock-dashboard.service

sudo systemctl daemon-reload
sudo systemctl enable --now adblock-proxy
sudo systemctl enable --now adblock-dashboard
```

To persist iptables rules across reboots:
```bash
sudo apt install iptables-persistent
sudo netfilter-persistent save
```

---

## Step 7 — Dashboard

```bash
./start_dashboard.sh
# → http://localhost:8080
```

Binds to `127.0.0.1` only (not `0.0.0.0`) — it's not reachable from the LAN
by default. If you deliberately want LAN access, edit the `app.run(...)`
host in `dashboard_server.py`, but note there is no authentication.

The **Allow/Block** page writes directly to the database; the proxy picks
up changes within 30 seconds.

---

## CLI (`ctl.py`)

```bash
python3 ctl.py lookup ads.example.com   # what would the proxy decide right now
python3 ctl.py stats
python3 ctl.py top-blocked 20
python3 ctl.py search amazon
python3 ctl.py allow example.com
python3 ctl.py block ads.example.com
```

---

## How matching works

For a query like `ads.doubleclick.net`, the proxy checks, in order:

```
ads.doubleclick.net
doubleclick.net
net
```

against the `domains` table and stops at the first match. A blocklist entry
for `doubleclick.net` therefore also blocks any of its subdomains. Manual
overrides (added via the dashboard or `ctl.py`) share the same table, so
they take priority automatically — a domain can only be in one list at a
time; adding it to the allowlist removes any existing blocklist entry and
vice versa.

---

## Differences from the old Ollama-based version

- No Ollama/LLM dependency — decisions come from indexed SQLite lookups (sub-millisecond, not 2-5s).
- Deterministic and auditable — same domain always gets the same verdict until you change the list.
- The dashboard's allow/block edits actually take effect now (previously written to JSON files the proxy never read).
- Single web dashboard instead of two disconnected ones (terminal `dashboard.py` + separate Flask app).
- Decision log is capped at 50k rows and self-trims instead of growing `decisions.jsonl` forever.
- Dashboard binds to `127.0.0.1` instead of `0.0.0.0`, and drops the wide-open CORS policy — the old setup exposed browsing DNS history and list controls to your whole LAN with no auth.

---

## Troubleshooting

**Proxy starts but DNS stops working entirely**
→ `dig @127.0.0.1 -p 5335 google.com` — if that works, the iptables rule is the problem.
→ `sudo bash config/setup-iptables.sh remove` to restore, then re-check.

**A site is wrongly blocked**
→ `python3 ctl.py lookup thesite.com` to see which list entry matched, then
`python3 ctl.py allow thesite.com` to override it.

**Port 5335 already in use**
→ `sudo ss -ulnp | grep 5335`

**Proxy fails to bind with "Address already in use" on port 5353**
→ This is why the proxy uses 5335 by default instead — `avahi-daemon`
(mDNS) binds `0.0.0.0:5353` on most desktop Linux installs. If you change
`LISTEN_PORT` back to 5353 for any reason, either stop avahi-daemon or pick
a different port.

**Database locked errors**
→ Both the proxy and dashboard use WAL mode so they can read/write
concurrently; this should not happen under normal use. If it does, check
nothing else is holding an open transaction on `data/adblock.db`.
