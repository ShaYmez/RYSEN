# IPSC Phase 1 & 2a

Motorola IP Site Connect (IPSC) support in RYSEN.

## What works

### Phase 1

- `MODE: IPSC` system stanza (example: `[IPSC]`)
- IPSC master UDP listener per generated slot
- Registration (`MASTER_REG_REQ`), keepalives, peer list, de-register
- Inbound group voice → RYSEN `dmrd_received()` routing (same bridge/rules as HBP masters)
- Optional IPSC HMAC auth (`AUTH_ENABLED` / `AUTH_KEY`)

### Phase 2a (ipsc branch docker install)

- **`ipsc_proxy.py`** — public UDP **56001** (CPS Master port) → backend slots `IPSC-0` … `IPSC-199`
- **`GENERATOR: 200`** on `[IPSC]` with `PORT: 56003` (backends `56003`–`56202` on compose network)
- Proxy routes by repeater radio ID; master replies by backend source port
- Sample proxy config: `docker-configs/config/ipsc-proxy-SAMPLE.cfg`

### Field test (2026-06)

Verified on a Debian VM: repeater registration and ~10s re-registration with autonomous repeater radio ID from CPS (`ALLOWED_PEER_IDS` empty).

### Phase 2b (bridge parity)

- `make_stat_bridge` / `make_single_reflector` include **IPSC-N** slots (same as `SYSTEM-N`)
- `augment_bridges_for_masters()` already augments IPSC after `GENERATOR` split
- UA bridge activation and sticky-TG logic apply to IPSC sources

## Not yet implemented

- Outbound voice to Motorola repeaters (bridged TX back over IPSC) — Phase 2c
- Selfcare / `ipsc_proxy_v2_sc`

## Configuration

Docker install ships:

| File | Role |
|------|------|
| `rysen.cfg` `[IPSC]` | Backend masters (`PORT` = first backend, `GENERATOR` = slot count) |
| `ipsc-proxy.cfg` | Public listen port **56001**, `DESTPORTSTART`/`END` = backend range |

| Setting | Purpose |
|---------|---------|
| `PORT` | First backend port (`IPSC-0`); `IPSC-N` uses `PORT + N` |
| `GENERATOR` | Number of backend slots (200 on docker install) |
| `IPSC_MASTER_ID` | Virtual master ID (not the repeater radio ID) |
| `MAX_PEERS` | Peers per slot (`1` recommended) |
| `ALLOWED_PEER_IDS` | Optional whitelist; empty = allow any |
| `PROXY_CONTROL` | Enable `PRIN`/`PRCL` logging and proxy disconnect handling |

## Motorola CPS (e.g. DR3000 peer)

- Link type: **Peer**
- Master IP: your server public IP
- **Master UDP port: 56001** (must match `ipsc-proxy` / firewall / docker-compose)
- **Peer UDP port: 56002** (repeater local bind port — set in CPS only, not on the server)
- Repeater radio ID: configured in CPS; optional `ALLOWED_PEER_IDS` whitelist

## Architecture

```
Motorola repeater ──UDP 56001──► ipsc-proxy ──UDP 56003+N──► [IPSC-N] routerIPSC
                                                                    │
                                                                    ▼
                                                          bridge_master / rules.py
```

Run proxy manually: `python3 ipsc_proxy.py -c ipsc-proxy.cfg`

Protocol constants and voice translation are derived from [ipsc2hbp](https://github.com/n0mjs710/ipsc2hbp) (GPLv3).

## Branch

Development is on the `ipsc` branch; not merged to master.

## Phase 2 (remaining)

1. Outbound IPSC voice (DMRD → `GROUP_VOICE`)
