# Native Hytera IP Multi-site Connect

**Status (1.6.3, October 2026):** RD985 and RD625 support remains
production-ready. HR1065 firmware `A3.0.20.010` adds field-validated HMTP
group voice, private voice, and SMS.

Native Hytera support shipped in **1.6.0** and was brought to production
quality on GB7NR in **1.6.1**: private and
group SMS (including cross-server OpenBridge), stable downlink pacing on busy
static talkgroups, selfcare, and cross-client SMS with hotspots and Z3DMR
(where the client decodes the radio dialect). It is a separate protocol stack
from Motorola IPSC, field-validated on RD985 repeaters running firmware
`A9.02.03.009` and `A8.00.09.001`, and on an RD625 running
`A8.05.07.001`.

This is an independent interoperability implementation. It is not affiliated
with, endorsed by, or derived from Hytera source code. Hytera's published
application notes describe IP Multi-site Connect as Hytera's application-layer
protocol and as a paid repeater feature. RYSEN does not enable that feature.
The repeater owner must already have it authorised in CPS. The wire behaviour
here was observed from an operator-owned RD985 connected to an operator-owned
master. Hytera names are used only to identify the protocol and tested
hardware.

Sample config: [HYTERA-SAMPLE.cfg](../HYTERA-SAMPLE.cfg). Selfcare:
[selfcare.md](selfcare.md). Motorola IPSC: [ipsc.md](ipsc.md).

## What works

| Path | Result |
|------|--------|
| P2P / DMR / RDAC registration, redirects and keepalives | OK |
| Inbound and outbound group voice | OK |
| ChatterBox 2350 downlink on GB7NR (no stacked delay / mid-over holes) | OK |
| GB7NR busy static TG downlink (326 TS1, OpenBridge jitter without steady dropouts) | OK |
| Dial-a-TG private activation and TG9 group announcement | OK |
| Group and private/unit parrot on `9990` | OK |
| Private voice both ways, including UK ↔ Europe via OpenBridge | OK |
| Private SMS both ways, including confirmed read (MD380 ↔ hotspot via RD985) | OK |
| Anytone H-SMS ↔ Z3DMR beta both ways, including UK ↔ Europe (1/2-rate, 2348831 ↔ 2345875) | OK |
| PD685G H-SMS ↔ Z3DMR beta both ways (GB7NR 2348831 ↔ UK 2345875; PD confirmed SAP 3 / 3/4-rate, Z3DMR 1.9.9) | OK |
| Same-repeater private SMS both ways (MD380 ↔ Anytone 868, 1/2-rate, same slot) | OK |
| Anytone GPS to D-APRS `9059` (1/2-rate unit data via RD985 → APRS-IS) | OK |
| PD685G H-SMS 3/4-rate blocks (`6666`) inbound to Homebrew | OK |
| PD685G 3/4-rate replies encoded back to Hytera as `6666` | OK |
| Group SMS routed on the talkgroup (not treated as group voice) | OK |
| Mid-stream group join (late entry) | OK |
| Delayed static admission keeps the original Voice LC Header | OK |
| NAT-aware multi-repeater proxy | OK |
| Two physical RD985s on one master (A8 and A9) | OK |
| RD625 group voice both ways (Europe, 26 Oct 2026, TG 91 TS1, 0% loss) | OK |
| Live RSSI on Linked Systems | OK |
| Repeater selfcare (claim, statics, disconnect) | OK |
| Read-only RDAC firmware, model, serial, callsign, TX/RX | OK |

## Tested repeaters

| Repeater | Firmware | Result |
|----------|----------|--------|
| RD985 | `A9.02.03.009` | OK. GB7NR: voice, SMS, GPS, parrot, selfcare, OpenBridge |
| RD985 | `A8.00.09.001` | OK. Registered beside an A9 repeater and passed voice |
| RD625 | `A8.05.07.001` | OK. Europe, 26 Oct 2026. Repeater 262326, callsign DO1KBL, hardware `RD625-00000000-001000-U1-0-B`. RDAC identity, then group voice both ways on TG 91 TS1 with subscriber 2623266. Same 72-byte media as the RD985, about 16.7 packets/s, 0% loss. SMS and GPS were not part of this contact |
| HR1065 | `A3.0.20.010` | OK. Radio data `D3.2.01.002`, model `HRA65-00000000-0000DC-U1-0-A`, 400–470 MHz. Europe, 27 Oct 2026: bidirectional TG91 group voice, private voice and SMS over LTE using HMTP on the P2P socket. Inbound group/private voice on both timeslots was also capture-validated on 26 Oct. The company unit under test has no RDAC |

One RF timeslot carries one call. Other subscribed statics stay linked but
are blocked for that call and its hangtime. They are not queued.
`SINGLE_MODE` defaults to False on Hytera so a second static is not
unsubscribed when the first is keyed.

Unknown media lengths, including reported 103-byte packets, are rejected
until they are capture-validated. The master logs each rejected DMR packet
with hex and appends it to `CAPTURE_FILE` (default
`/opt/rysen/log/hytera-unknown.jsonl`, host `/var/log/rysen`). Set
`TRACE_PACKETS: True` on one Hytera stanza only when collecting SMS/GPS/UDT
fixtures. Routine voice bursts are not hex-dumped; data and unknown slots
still are. Do not leave `LOG_LEVEL: DEBUG` on a live master — the router
timer used to log every inactive bridge each tick and that delayed the
60 ms Hytera pacer. SNMP is out of scope.

## Limits

- UA still drops the talkgroup 10 minutes after the last local PTT.
  Listening does not refresh that timer. Kerchunking during a network
  over steals the RF slot for group hangtime (~5 s on GB7NR).
- Same-repeater private SMS must be on the matching timeslot. The RD985
  does not locally repeat unit data when IP multi-site is linked; the
  master hairpins it.
- Anytone H-SMS RX is 1/2-rate only. PD685G 3/4-rate (`6666`) is mapped
  correctly but will not inbox on an Anytone. There is no SMS dialect
  transcoder; each client must decode what the radio sends (for example
  Z3DMR for PD685G confirmed SAP 3).
- PD685G / Hytera GPS location reports are not decoded into D-APRS. Only
  the Anytone 1/2-rate unit-data path to `9059` is supported today.
- Motorola IPSC SMS/GPS remains deferred. See [ipsc-roadmap.md](ipsc-roadmap.md).

## Out of scope (not planned)

- SNMP repeater management, Hytera RCP/RNDIS handheld programming, or a
  general Hytera data gateway (TMS-style services).
- Transcoding SMS between Hytera 3/4-rate SAP 3 and other vendors' formats.
- Enabling Hytera's paid IP Multi-site feature in CPS (operator responsibility).

## CPS programming

Repeater type is `Slave`. Network Authentication is blank. Enable Voice and
Data and RDAC.

An HR1065 without RDAC should use a direct (no proxy) `[HYTERA]` stanza with
`HYTERA_REPEATER_ID` set to the repeater's configured identity and
`HMTP_MODE: True` so network-originated traffic can use the registered P2P
endpoint before the repeater transmits, plus `RDAC_DISCOVERY: False`. The
captured test unit used ID `1000`. Keep `HMTP_MODE: False` for RD625/RD985.
Do not use the dynamic proxy for an RDAC-less unit because P2P registration
does not carry an identity.

Every repeater uses the same public ports:

| Service | UDP |
|---------|-----|
| P2P / master | `50000` |
| Voice and Data | `50001` |
| RDAC | `50002` |

The proxy assigns an internal slot after registration and redirects that
repeater onto it. Do not program a second repeater to `50003/50004/50005`.

Two repeaters behind the same public address cannot both register on
`50000`. Sites with different public addresses keep the normal three-port
programming.

## Architecture

```
Hytera RD985 ──UDP 50000/50001/50002──► hytera_proxy ──► HYTERA-N
                                                        │
                                                        ▼
                                              bridge_master / rules.py
```

A `[HYTERA]` stanza with `GENERATOR: N` creates backends `HYTERA-0` …
`HYTERA-(N-1)`. Each slot consumes three consecutive backend ports. Direct
(no proxy) deployments bind the public triple on the master itself.

| Component | Role |
|-----------|------|
| `hytera_master.py` | P2P/DMR/RDAC registration, dispatch, live RSSI poll |
| `hytera_voice.py` | Voice translation, 60 ms pacing, late entry |
| `hytera_rdac_meta.py` | Identity and RSSI helpers |
| `hytera_proxy.py` | Public port triples and NAT session binding |
| `hytera_const.py` | Ports and media constants |

`hytera_proxy.py` is the Hytera equivalent of the IPSC proxy. The first
repeater stays on `50000/50001/50002`. A later repeater, still programmed to
those ports, is redirected to the next public DMR/RDAC pair. P2P
registration remains on `50000`.

P2P registration has no repeater ID. The slot stays provisional until RDAC
identity arrives. The proxy binds that 24-bit ID to the session. A duplicate
or blacklisted ID is released. A fresh P2P registration clears stale
DMR/RDAC endpoints before renegotiation.

Run the proxy with `python3 hytera_proxy.py -c hytera-proxy.cfg`, using
[hytera-proxy-SAMPLE.cfg](../hytera-proxy-SAMPLE.cfg). From a source checkout,
`docker compose --profile hytera -f docker-configs/docker-compose-stack.yml`
builds the local image `rysen-hytera-proxy:1.6.3`. That image is not on
Docker Hub.

Generated backends set `PROXY_CONTROL: True` and `PROXY_CONTROL_IP` to the
sidecar's trusted address. Control from any other source is ignored.

## Voice

RD985 and RD625 carry the native 72-byte media on the redirected DMR service
socket. HR1065 carries that same body on the P2P socket, either as a 76-byte
`HMTP` frame or an 86-byte `HMTPP2P1` routed frame. RYSEN unwraps both
inbound forms into the shared translator; outbound HR1065 media uses the
plain `HMTP` envelope and the learned P2P endpoint. Hytera-only sync packets
are ignored. Duplicate wire sequences are suppressed.

Outbound voice follows ETSI one-burst-per-60 ms slot cadence on a fixed
schedule grid with minimum spacing between RF bursts (no back-to-back
catch-up). A three-burst (180 ms) jitter buffer is used at call start,
aligned with Hytera's documented 60 ms IP jitter-buffer steps. When
depth exceeds three frames, the scheduler shortens steps to 50 ms until
the buffer recovers. If depth exceeds eight frames (480 ms), oldest voice
is trimmed only enough to retain that eight-frame ceiling. This prevents a
single transient overflow from manufacturing a six-burst audio hole while
still bounding delay. A replacement OpenBridge stream ID or extra VHEAD does
not dump that queue while RF playout is already active. Late entry waits for the
repeater's activation headers, then joins the running stream without
synthesizing a new call start. After playout has started, a missing burst
stays missing instead of re-buffering the call.

Private calls use the Hytera private marker and the existing Dial-a-TG path:
private-call a reflector ID to link, `4000` to disconnect, and `5000` for
status. The announcement returns as group voice to TG9. A subscriber
private call is delivered on this master when the callee has been heard
here. An optional hub can send that call to another master; see
[unit-call.md](unit-call.md).

TG `9990` group and unit parrot return once, at normal speed. PARROT
playback is excluded from the generic unit relay so it cannot double.

## Signal strength

Direct Homebrew and enhanced OpenBridge v4/v5 already carry DMRD bytes 53
(BER) and 54 (RSSI). Classic OpenBridge v1–v3 and Motorola IPSC have no
RSSI field.

An RD985 does not put a usable level in the voice frame. While the repeater
is transmitting, RYSEN polls RDAC, writes the busy-slot sample into
Homebrew byte 54, and reports it on `GROUP VOICE,START` plus a throttled
`GROUP VOICE,RSSI` event. Linked Systems shows `-N dBm` beside the
subscriber once a sample arrives. The first second of a call can still
show no badge.

## Monitor and selfcare

Hytera peers report as `PROTOCOL: HYTERA`. Linked Systems shows them in the
repeater section. The tooltip keeps protocol, statics and RDAC radio
information, but displays only the short model prefix (for example
`RD985`).

Selfcare uses MariaDB `Clients` with `mode = -1` and the same ownership /
static-talkgroup lifecycle as IPSC. No CPS settings, SNMP, or device
runtime controls are exposed. RDAC firmware, hardware string, serial,
callsign, raw mode and TX/RX frequency are cached in `HyteraMetadata` for
the PHP view only.
