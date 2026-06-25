# IPSC branch — roadmap & merge criteria

This document tracks work remaining before **`ipsc`** is merged to **`master`**. Voice bridging is field-tested; merge is blocked on operational polish and monitoring.

## Merge criteria

| Area | Status | Notes |
|------|--------|-------|
| Repeater registration + keepalive | Done | GB7NR on 56002 + auth |
| Inbound voice (repeater → network) | Done | TG 2350 TS2 |
| Outbound voice (network → repeater) | Done | Extended GROUP_VOICE + 60 ms jitter buffer |
| Hotspot → IPSC UA bridge | Done | Peer-leg auto-activate (`4384b6c`) |
| Docker / `ipsc-proxy` install | Done | See [install.md](install.md) |
| Unit tests (protocol / voice / bridge helpers) | Done | `tests/test_ipsc_*.py` |
| Soak test (multi-day field use) | In progress | — |
| Monitor dashboard IPSC peers | **Not started** | See below |
| Selfcare proxy | **Not planned for merge** | Optional later |
| Production auth defaults | **Ops** | Rotate `AUTH_KEY` off sample |
| CHANGELOG / version on merge | Pending | Add 1.5.0 entry when merging |

## Monitor / dashboard gap

RYSEN reports system state to TCP clients (FDMR-Monitor and similar) via `reportFactory.send_config()`, which pickles `CONFIG['SYSTEMS']`.

**HBP masters:** live peers are stored in `CONFIG['SYSTEMS'][name]['PEERS']` and appear on dashboards.

**IPSC masters:** registered repeaters live in runtime `_ipsc_peers` on each `routerIPSC` instance (`ipsc_master.py`). The config `PEERS` dict stays empty. Periodic reporting counts only systems with non-empty `PEERS`:

```python
# bridge_master.py — config_reports reporting_loop
if 'PEERS' in CONFIG['SYSTEMS'][system] and CONFIG['SYSTEMS'][system]['PEERS']:
    i = i + 1
```

**Required for merge (proposed):**

1. Mirror `_ipsc_peers` into report payload (either sync to `CONFIG['SYSTEMS'][slot]['PEERS']` on reg/dereg, or extend `send_config()` to include IPSC peer state).
2. Use a shape monitors already understand (radio ID, IP, port, last keepalive) or document a new `MODE: IPSC` panel.
3. Bridge reports already include IPSC slots via `augment_bridges_for_masters()` — verify dashboard renders `IPSC-N` legs on conference bridges.
4. Optional: IPSC-specific report events (`IPSC peer registered` / `timed out`).

**Files likely touched:** `ipsc_master.py`, `hblink.py` (`send_config`), `bridge_master.py` (`config_reports`), monitor UI (external repo if applicable).

## Nice-to-have (post-merge or low priority)

- Voice stream timeout watchdog (`check_call_timeouts` in ipsc2hbp) for calls ending without TERM
- Integration test for jitter-buffer timer (Twisted `callLater`)
- Unit test for `_activate_bridge_peer_masters`
- `TS_PREFER_CALL_INFO` field-test note if using DMRlink confbridge
- Duplicate `VOICE_HEAD` soak validation

## Not in scope

- XCMP/XNL repeater management
- `ipsc_proxy_v2_sc` selfcare (listed in [ipsc-phase1.md](ipsc-phase1.md))

## Soak-test log prompts

```bash
# Bridge + peer-leg activation
docker logs systemx -f 2>&1 | grep -E 'peer leg|Bridge 2350|IPSC peer'

# Hotspot → repeater path
docker logs systemx -f 2>&1 | grep -E 'SYSTEM-[0-9]+.*CALL START|IPSC-[0-9]+.*CALL'

# Outbound to repeater
tcpdump -ni any -c 30 'host <repeater-ip> and udp port 56002'
```

## Branch hygiene (docs-only tidy)

- [ipsc-phase1.md](ipsc-phase1.md) — feature + field-test reference
- [install.md](install.md) — docker install for `ipsc` branch
- [CHANGELOG.md](../CHANGELOG.md) — unreleased `ipsc` section until merge
