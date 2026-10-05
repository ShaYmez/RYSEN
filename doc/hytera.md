# Native Hytera IP Multi-site Connect

Native Hytera support is a separate protocol stack from Motorola IPSC. It is
field-validated on RD985 repeaters running firmware `A9.02.03.009` and
`A8.00.09.001`.

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
| Dial-a-TG private activation and TG9 group announcement | OK |
| Group and private/unit parrot on `9990` | OK |
| Mid-stream group join (late entry) | OK |
| Delayed static admission keeps the original Voice LC Header | OK |
| NAT-aware multi-repeater proxy | OK |
| Two physical RD985s on one master (A8 and A9) | OK |
| Live RSSI on Linked Systems | OK |
| Repeater selfcare (claim, statics, disconnect) | OK |
| Read-only RDAC firmware, model, serial, callsign, TX/RX | OK |

One RF timeslot carries one call. Other subscribed statics stay linked but
are blocked for that call and its hangtime. They are not queued.
`SINGLE_MODE` defaults to False on Hytera so a second static is not
unsubscribed when the first is keyed.

Unknown media lengths, including reported 103-byte packets, are rejected
until they are capture-validated. SNMP is out of scope.

## CPS programming

Repeater type is `Slave`. Network Authentication is blank. Enable Voice and
Data and RDAC.

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
[hytera-proxy-SAMPLE.cfg](../hytera-proxy-SAMPLE.cfg). Generated backends
set `PROXY_CONTROL: True` and `PROXY_CONTROL_IP` to the sidecar's trusted
address. Control from any other source is ignored.

## Voice

Inbound 72-byte media is translated to Homebrew DMRD and enters the same
bridge path as other masters. Hytera-only sync packets are ignored.
Duplicate wire sequences are suppressed.

Outbound voice is paced at 60 ms. A three-burst jitter buffer is used at
call start. Late entry waits for the repeater's activation headers, then
joins the running stream without synthesizing a new call start. After
playout has started, a missing burst stays missing instead of re-buffering
the call.

Private calls use the Hytera private marker and the existing Dial-a-TG path:
private-call a reflector ID to link, `4000` to disconnect, and `5000` for
status. The announcement returns as group voice to TG9. A subscriber
private call is delivered on this master when the callee has been heard
here. An optional hub can send that call to another master; see
[ipsc-roadmap.md](ipsc-roadmap.md).

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

## Captures

Binary captures stay outside Git. They contain live network and subscriber
metadata. Analyse local copies with `tools/analyze_hytera_capture.py`.
The field diary is not published.
