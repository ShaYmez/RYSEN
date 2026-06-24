# IPSC Phase 1

Motorola IP Site Connect (IPSC) support in RYSEN — initial implementation.

## What works (Phase 1)

- `MODE: IPSC` system stanza (example: `[IPSC]`)
- IPSC master on a single UDP port (default **50001**)
- One repeater per instance (`MAX_PEERS: 1`, `GENERATOR: 1`)
- Registration (`MASTER_REG_REQ`), keepalives, peer list, de-register
- Inbound group voice → RYSEN `dmrd_received()` routing (same bridge/rules as HBP masters)
- Optional IPSC HMAC auth (`AUTH_ENABLED` / `AUTH_KEY`)

## Not yet implemented

- `ipsc_proxy` (single public port → many backend slots)
- `GENERATOR: 200` scaling
- Outbound voice to Motorola repeaters (bridged TX back over IPSC)
- Selfcare / `ipsc_proxy_v2_sc`

## Configuration

The docker install ships `docker-configs/config/rysen.cfg` with an enabled `[IPSC]` stanza on port **50001**. Standalone reference: `docker-configs/config/IPSC-SAMPLE.cfg` (same fields).

Edit before connecting a repeater:

| Setting | Purpose |
|---------|---------|
| `PORT` | UDP port repeaters connect to (direct, no proxy yet) |
| `IPSC_MASTER_ID` | Virtual master ID presented to repeaters |
| `MAX_PEERS` | Peers allowed on this instance (use `1` for Phase 1) |
| `ALLOWED_PEER_IDS` | Optional comma-separated repeater radio IDs |

## Motorola CPS

- Link type: **Peer** (registers to your RYSEN server as IPSC master)
- Master IP: your server
- Master port: `PORT` from config (e.g. 50001)
- Repeater radio ID: must pass `REG_ACL` / `ALLOWED_PEER_IDS` if configured

## Architecture

```
Motorola repeater ──IPSC UDP──► [IPSC] routerIPSC ──► bridge_master / rules.py
```

Protocol constants and voice translation are derived from [ipsc2hbp](https://github.com/n0mjs710/ipsc2hbp) (GPLv3).

## Branch

Development is on the `ipsc` branch; not merged to master.
