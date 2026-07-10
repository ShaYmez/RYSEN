# RYSEN unit-to-unit private voice — roadmap

This document tracks **Phase 4** work on branch **`unit2`**, branched from **`master`** @ **1.5.1**.

**Goal:** route private voice between subscriber DMR IDs (7-digit unit calls) — first on a **single RYSEN instance** (local routing), then **server-to-server** via **extended OpenBridge (OBP)**.

Parent context: [ipsc-roadmap.md](ipsc-roadmap.md) (Phase 3 wire layer is **done**; Phase 4 routing policy on **`unit2`**).

Field-test reference: [ipsc.md](ipsc.md).

---

## Feature policy

**Unit-to-unit private voice is a default platform feature** — same posture as dial-a-tg:

- **No config switch** — no `UNIT_VOICE_ROUTING` or “disable private calls” global flag.
- **Always active** on enabled `MASTER` / `IPSC` systems once Phase 4 merges; routing runs whenever the classifier identifies a subscriber destination (6/7-digit), not a dial-a-tg target.
- **Dial-a-tg unchanged** — 4000, 5000, link TGs, and TG 9 remain on the reflector path regardless.
- **Operational control** (if needed) stays indirect: `SUB_ACL`, per-system `ENABLED`, or not registering peers — not a dedicated unit-call toggle.

Field test validates behaviour on real hardware before merge to **`master`**; rollback is branch/deploy level, not a cfg knob.

---

## Milestone summary

| Area | Status |
|------|--------|
| IPSC `PRIVATE_VOICE` encode/decode (0x81) | **Done** — Phase 3 on `master` |
| Dial-a-tg reflector on IPSC (4000/5000/link TG) | **Done** |
| `_forward_unit_voice()` hook (SUB_MAP, peer, IPSC) | **Done** |
| Dial-a-tg vs unit-to-unit classifier | **Done** — `bridge_helpers.py` (4A.1) |
| Local unit-to-unit routing (same server) | **Code done** — field test (4A.4) |
| OBP unit **data** server-to-server (`VER > 1`) | **Done** — `sendDataToOBP()` |
| OBP unit **voice** server-to-server | **Planned** — Phase 4B |
| EOBP (KF7EEL extended variant) | **Not supported** — logged and discarded |

---

## Current schedule

| Window | Work | Outcome |
|--------|------|---------|
| **Now — Phase 4A.4** | Field test (single server) | GB7NR + hotspot unit-call matrix |
| **Next — Phase 4B** | Extended OBP private voice | RYSEN ↔ RYSEN when destination is on a remote server |
| **Later** | Monitor + ops polish | Private-call events, soak, cross-server field matrix |

---

## DMR ID numbering (routing decisions)

Same convention as [ipsc-roadmap.md](ipsc-roadmap.md#dmr-id-numbering-systemx-convention):

| Length | Typical use | Route as |
|--------|-------------|----------|
| **≤ 5 digits** | Talkgroups, dial-a-tg link targets | Reflector / dial-a-tg (not unit forward) |
| **6 digits** | Repeater radio IDs | Peer identity; match by prefix for IPSC |
| **7 digits** | Individual subscribers | **Unit-to-unit destination** |
| **9 digits** | Hotspots with SSID suffix | Peer match (7-digit prefix) |

**Service codes (always local reflector, never forward):** 4000, 5000, 9991–9999.

---

## Problem (resolved on `unit2`)

Phase 3 delivered the **transport** (IPSC `0x81` ↔ unit DMRD, jitter buffer, tests). On **`master`**, routing policy still treated almost every private-call destination as dial-a-tg. **`unit2` fix (4A.1):** `is_reflector_private_destination()` in `bridge_helpers.py` now classifies dial-a-tg only; `_forward_unit_voice()` runs for 6/7-digit subscriber destinations.

**OBP gap (4B):** unit **data** already bridges server-to-server via `sendDataToOBP()` when `PROTO_VER > 1`. Unit **voice** has no equivalent path yet.

---

## Design principles

Inherited from IPSC phases ([ipsc-roadmap.md](ipsc-roadmap.md#design-principles-maintainability)):

1. **One media path** — unit DMRD in/out; IPSC uses existing `IpscVoiceTranslator`. No parallel “unit bridge engine”.
2. **Classifier before forward** — dial-a-tg and unit-to-unit must not share the same gate.
3. **SUB_MAP is the fast path** — `(system, slot, tg, timestamp, peer_id)` for last-known subscriber location.
4. **Peer prefix fallback** — 7-digit prefix match on registered MASTER/IPSC peers when SUB_MAP misses.
5. **OBP parity** — private voice over OBP should mirror unit-data semantics (destination ID preserved, loop control, ENHANCED_OBP keepalive).
6. **Tests per path** — classifier, forward matrix, OBP voice encode; field matrix on GB7NR + hotspot.
7. **Default feature, no switch** — unit routing is always on (see [Feature policy](#feature-policy)); do not add opt-out cfg keys.

---

## Architecture

### Phase 4A — local (single RYSEN)

```
┌──────────────┐     unit DMRD      ┌─────────────────────────────────┐
│ Hotspot OBP  │ ─────────────────► │ routerHBP / routerMASTER        │
│ or IPSC rptr │                    │  • classify: dial-a-tg vs unit  │
└──────────────┘                    │  • reflector OR _forward_unit_  │
                                    │    voice()                      │
┌──────────────┐     unit DMRD      │  • SUB_MAP → peer → IPSC        │
│ Hotspot OBP  │ ◄───────────────── │                                 │
│ or IPSC rptr │                    └─────────────────────────────────┘
└──────────────┘
         ▲
         │ PRIVATE_VOICE 0x81 (IPSC leg)
         ▼
┌──────────────┐
│ ipsc_master  │
└──────────────┘
```

### Phase 4B — server-to-server (extended OBP)

```
RYSEN-A                              RYSEN-B
┌─────────────┐   unit voice DMRD   ┌─────────────┐
│ routerHBP   │ ────── OBP ────────► │ routerOBP   │
│ _forward_   │   ENHANCED_OBP       │ → SUB_MAP / │
│ unit_voice  │   BCKA / BCVE        │   peer /    │
│ → OBP peer  │ ◄──── OBP ──────── │   local HBP │
└─────────────┘                      └─────────────┘
```

**Extended OBP** today (`ENHANCED_OBP: True`):

| Packet | Role |
|--------|------|
| **BCKA** | Bridge keepalive; dynamic TARGET_IP/PORT update |
| **BCVE** | Protocol version negotiation |
| **BCSQ** | Source quench (loop prevention) |
| **BCST** | STUN |

Unit **data** already uses DMRD over OBP with `SERVER_ID` / repeater attribution. Phase 4B adds the same for **voice** bursts (VHEAD, A–F, VTERM), reusing loop-control patterns from group OBP and unit-data OBP.

**EOBP** (`const.EOBP`) — KF7EEL variant; explicitly unsupported. Out of scope unless a peer requires it.

---

## Phase overview

| Phase | Goal | Status |
|-------|------|--------|
| **4A** | Local unit-to-unit routing | **Code done** — field test (4A.4) |
| **4B** | Server-to-server unit voice via extended OBP | **Planned** |
| **4C** | Field matrix + monitor events | **Planned** |

```
master @ 1.5.1
  └── unit2 branch
        ├── 4A  local unit routing      ← field test
        ├── 4B  OBP unit voice bridge
        └── 4C  field test + monitor
```

---

## Phase 4A — Local unit-to-unit routing

**Goal:** User A private-calls user B (7-digit ID). Call routes across hotspot ↔ IPSC ↔ hotspot on **one** RYSEN instance without treating B as a dial-a-tg target.

### 4A.1 — Narrow dial-a-tg classifier

Replace broad `is_reflector_private_destination()` with dial-a-tg-only logic:

| Destination | Action |
|-------------|--------|
| 4000, 5000 | Reflector (disconnect / status) |
| 9991–9999 | Reflector service codes |
| Existing `#NNNN` reflector bridges, ≤5-digit link TGs | Reflector / UA |
| 6-digit repeater ID | Peer identity only — **do not** create reflector |
| 7-digit subscriber ID | **`_forward_unit_voice()`** |
| 8, 9 | Ignored (existing) |

Align private-call reflector creation in `dmrd_received` (lines ~3022–3028) with the same rules so 7-digit IDs do not spawn `#2348831` reflectors.

### 4A.2 — Forward path hardening

| Step | Detail |
|------|--------|
| SUB_MAP | Use existing 5-tuple; update on every RX from subscriber |
| Peer match | 7-digit prefix on `CONFIG['SYSTEMS'][*]['PEERS']` |
| IPSC | Forward to IPSC system when destination peer registered |
| Slot rewrite | Preserve existing `_bits ^ (1<<7)` when crossing slots |
| Busy / hangtime | Consider GROUP_HANGTIME gate (unit data already checks slot idle) |

### 4A.3 — Tests

| Test | Covers |
|------|--------|
| `test_unit_destination_classifier.py` | 4000/5000/link TG vs 7-digit vs 6-digit |
| Extend `test_dial_tg9_isolation.py` | 7-digit private call does not create reflector |
| `test_forward_unit_voice.py` | SUB_MAP, peer prefix, IPSC mock send |
| Regression | Dial-a-tg on IPSC still works (4000/5000/link TG) |

### 4A.4 — Field test (single server)

**Automated VM install:** [docker-configs/test-deploy-install.sh](../docker-configs/test-deploy-install.sh)

```bash
# Interactive (prompts for branch, selfcare, monitor)
curl -fsSL https://raw.githubusercontent.com/ShaYmez/RYSEN/refs/heads/unit2/docker-configs/test-deploy-install.sh | bash

# Non-interactive
RYSEN_BRANCH=unit2 INSTALL_SELFCARE=yes INSTALL_MONITOR=yes MONITOR_BRANCH=master NONINTERACTIVE=1 \
  curl -fsSL https://raw.githubusercontent.com/ShaYmez/RYSEN/refs/heads/unit2/docker-configs/test-deploy-install.sh | bash
```

Builds **RYSEN from `RYSEN_BRANCH`** (`rysen-local:latest`). Pulls **ipsc-proxy** and **selfcare proxy** from Hub (no proxy build — not required for unit voice). Optional **monitor** (Hub `master` or local build from `MONITOR_BRANCH`).

| From | To | Expected |
|------|-----|----------|
| Hotspot | Hotspot (same system) | Private voice both ways |
| Hotspot | IPSC repeater user | PRIVATE_VOICE on IPSC leg |
| IPSC repeater | Hotspot | Reverse path |
| Hotspot | 7-digit not registered | No reflector; log “no route” |
| Private to 2350 (link TG) | — | Still dial-a-tg, not unit forward |
| Private to 4000/5000 | — | Still reflector announcements |

### Tasks

- [x] **4A.1** Replace `is_reflector_private_destination()` with dial-a-tg-only classifier
- [x] **4A.2** Align reflector auto-create gate with classifier
- [x] **4A.3** Unit tests for classifier + `_forward_unit_voice()`
- [ ] **4A.4** Field test: GB7NR + SYSTEM-XTEST hotspot
- [ ] **4A.5** Document IPSC2 private-call behaviour vs BM hotspot TG 9 model

---

## Phase 4B — Server-to-server via extended OBP

**Goal:** When subscriber B is on **RYSEN-B**, a private call from A on **RYSEN-A** crosses an OBP link between servers.

### Preconditions

- Both servers: `ENHANCED_OBP: True`, `PROTO_VER > 1` (unit data already requires this)
- OBP peer configured with shared passphrase; BCKA keepalive healthy
- Destination ID ≥ 1_000_000 (7-digit) — same gate as unit data OBP export

### 4B.1 — Outbound: local forward → OBP

When `_forward_unit_voice()` finds no local SUB_MAP / peer match:

1. Scan configured `[OBP-*]` systems with `ENABLED` and `MODE: OPENBRIDGE`
2. Send unit voice DMRD via new **`sendVoiceToOBP()`** (or extend `sendDataToOBP` for voice dtype_vseq)
3. Preserve `_dst_id` as called subscriber (no TG rewrite — unlike group bridge)
4. Set `_source_server` / `_source_rptr` attribution (existing OBP fields)

### 4B.2 — Inbound: OBP → local delivery

In `routerOBP.dmrd_received`:

1. Handle `_call_type == 'unit'` voice (VHEAD/VTERM/bursts), not only unit data
2. Apply existing OBP loop control (first-packet wins, BCSQ on duplicate path)
3. Deliver via SUB_MAP → HBP/IPSC peer match (mirror 4A forward logic in reverse)
4. Do **not** enter group conference-bridge TG rewrite

### 4B.3 — Protocol notes

| Topic | Approach |
|-------|----------|
| LC / EMB-LC | Private call LC already in DMRD; no TG substitution |
| TS bit | Same as unit data: rewrite slot bit when crossing TS1/TS2 |
| ACL | Apply `SUB_ACL` on `_rf_src` and destination subscriber ID |
| Version | Bump `PROTO_VER` / BCVE when voice semantics ship |
| EOBP | Defer — log warning remains |

### 4B.4 — Tests

| Test | Covers |
|------|--------|
| `test_obp_unit_voice_outbound.py` | Forward to OBP peer when local miss |
| `test_obp_unit_voice_inbound.py` | OBP RX → SUB_MAP delivery |
| Loop control | Two OBP peers, same stream — BCSQ / first-wins |

### 4B.5 — Field matrix (two servers)

| Scenario | Expected |
|----------|----------|
| A@RYSEN-A → B@RYSEN-B (OBP linked) | Voice both ways |
| A@RYSEN-A → B local only | 4A path; no OBP egress |
| OBP keepalive lost | No send (existing `_bcka` gate) |
| Cross-server dial-a-tg to 4000 | Handled locally on each server — not bridged |

### Tasks

- [ ] **4B.1** `sendVoiceToOBP()` (or unified send with voice/data dispatch)
- [ ] **4B.2** `routerOBP` inbound unit voice handler
- [ ] **4B.3** Wire `_forward_unit_voice()` OBP egress on local miss
- [ ] **4B.4** Unit tests + two-server lab test
- [ ] **4B.5** BCVE version bump + sample cfg notes

---

## Phase 4C — Ops & monitor

- [ ] TCP report: `UNIT VOICE,START/END` bridge events (mirror GROUP VOICE)
- [ ] RYSEN-MONITOR: optional private-call display (separate repo)
- [ ] Voice stream timeout for long private calls
- [ ] Soak: concurrent group + unit calls; no reflector leak for 7-digit IDs

---

## Explicitly out of scope

- SMS / GPS / UDT (Phase 5 — [ipsc-roadmap.md](ipsc-roadmap.md))
- EOBP implementation
- AllStar private-call bridging changes (existing AMI path unchanged)
- XCMP / CPS / wireline

---

## Branch hygiene

| Branch | Base | Purpose |
|--------|------|---------|
| **`master`** | — | Released **1.5.0** (IPSC + dial-a-tg reflector) |
| **`unit`** | `master` @ 1.5.0 | Phase 4 unit-to-unit work (this doc) |

| Doc | Purpose |
|-----|---------|
| [unit-roadmap.md](unit-roadmap.md) | Phase 4A/4B implementation roadmap (**this file**) |
| [ipsc-roadmap.md](ipsc-roadmap.md) | IPSC phases 0–7; links here for Phase 4 detail |
| [ipsc-phase1.md](ipsc-phase1.md) | Field-test reference |
| [CHANGELOG.md](../CHANGELOG.md) | Release notes when `unit` merges |

**Merge criteria (unit → master):**

- [ ] 4A classifier + tests green
- [ ] 4A field matrix pass (GB7NR + hotspot)
- [ ] 4B two-server OBP voice pass (or documented deferral with feature flag)
- [ ] No regression: dial-a-tg reflector, group voice, IPSC soak
- [ ] CHANGELOG + version bump
