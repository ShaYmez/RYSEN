# Native Hytera IP Multi-site Connect

Native Hytera support is developed on `feature/HYTERA` and field-tested with an
RD985 running firmware `A9.02.03.009`. It is a separate protocol stack from
Motorola IPSC.

## Test master

- Hostname: `hytera.freestar.network`
- IPv4: `207.246.85.40`
- P2P/master UDP: `50000`
- Voice and Data UDP: `50001`
- RDAC UDP: `50002`

The CPS repeater type is `Slave`. Network Authentication is blank. Voice and
Data and RDAC are enabled. Registration, service negotiation and inbound group
voice routing are field-validated. Capture-derived outbound group voice is
implemented and field-validated. Dial-a-TG private activation and private
parrot echo are also field-validated.

## Capture oracle

Known-good captures were made between the RD985 and
`ipsc2.freestar.network` using P2P/DMR/RDAC ports `62005/62006/62007`.
The observed RD985 public endpoint was `90.207.110.244`, with P2P source port
`62004` and DMR/RDAC source ports `62006/62007`.

The binary captures are retained outside the repository because they contain
live network and subscriber metadata.

### A9 cold boot and negotiation

The cold-boot capture confirms this order:

1. P2P registration request and acceptance.
2. RDAC startup acceptance and redirect.
3. DMR startup acceptance and redirect.
4. P2P, DMR and RDAC keepalives.
5. RDAC identification, including firmware and repeater metadata.

Registration request:

```text
50325038010000001400000001ff11420000000010000000
```

Expected response:

```text
5032503802000000140000000101015a000000001000000001
```

DMR redirect to UDP 62006:

```text
503250500b0000001400000042ff01000000000011000000ff0136f2
```

Service negotiation responses are sent to the registered P2P endpoint, not the
source ports used by the DMR and RDAC startup requests.

### Group voice capture

The group capture contained 6 complete or partial RD985-originated calls:

- TS1, TG235: 4 calls, 713 media packets.
- TS2, TG2350: 2 calls, 856 media packets.
- Source subscriber: `2348831`.
- Nominal media cadence: 60 ms.
- Both directions were captured.
- The final TS2 call ended without a terminator when the repeater lost power.

Earlier calls include complete LC headers, A-F voice bursts, Hytera sync bursts
and terminators, so the interrupted final call does not invalidate the capture.

### Private voice and reflector capture

A second capture included a complete cold boot followed by:

- TS2 private call from `2348831` to `4400`: 32 packets, complete terminator.
- TS2 reflector announcement from `4400` to TG9: 109 packets.
- TS1 private call from `2348831` to `4400`: 46 packets, complete terminator.
- Private-call marker: `0x00`.
- Group-call marker: `0x01`.
- Nominal media cadence: 60 ms.

The reflector announcement returned as group voice from source `4400` to TG9,
not as a private call.

## Observed 72-byte media layout

- Sequence number: byte 4.
- Packet type: byte 8.
- Timeslot marker: bytes 16-17 (`1111` for TS1, `2222` for TS2).
- Slot/burst type: bytes 18-19.
- Color code: bytes 20-21.
- Frame type: bytes 22-23.
- DMR payload: 34 bytes from byte 26.
- Call type: byte 62.
- Destination ID: padded little-endian field at bytes 63-66.
- Source ID: padded little-endian field at bytes 67-70.

Observed slot types include LC header `1111`, terminator `2222`, voice bursts
`BBBB`, `CCCC`, `7777`, `8888`, `9999`, `AAAA`, wakeup `DDDD`, and sync
`EEEE`.

## Native master field validation

The RD985 completed a clean cold boot against `hytera.freestar.network` on
September 27, 2026:

- Registration request and response matched the IPSC2 oracle exactly.
- RDAC startup was accepted and redirected to UDP `50002`.
- DMR startup was accepted and redirected to UDP `50001`.
- P2P keepalives were answered with the captured 20-byte response.
- DMR and RDAC `0x00` service polls were answered with `0x41`.
- Nine consecutive keepalive cycles completed on all three services.

The captured native redirect suffixes were `ff0151c3` for DMR and `ff0152c3`
for RDAC, confirming that service ports are encoded little-endian.

## Native inbound group voice validation

The capture-derived 72-byte translator was deployed and field-tested through
the live OpenBridge:

- TS1/TG235: `2348831` via repeater `235287`, 3.06 seconds, 0% loss.
- TS2/TG2350: `2348831` via repeater `235287`, 7.74 seconds, 0% loss.
- Both calls were heard from the far side of the network.
- RYSEN reported the expected source, repeater, talkgroup and timeslot.

Inbound translation pair-swaps the 34-byte Hytera burst into the internal
33-byte DMRD payload, ignores Hytera-only sync packets, synthesizes a DMRD
stream ID, suppresses duplicate wire sequences, and rejects unknown packet
lengths until their implementation gates are opened.

## Dial-a-TG private calls

The RD985's private-call marker `0x00` is converted to the internal DMRD unit
call flag. This enters the existing routing-master Dial-a-TG path: private-call
a reflector ID to create or select its link, `4000` to disconnect, and `5000`
for status. Private signaling is handled locally and is never forwarded as a
subscriber-to-subscriber call.

The known-good IPSC2 capture shows the resulting announcement as a *group*
call from source `4400` to TG9, not a private call. RYSEN deliberately keeps
that established group TG9 announcement behavior for HYTERA.

Field validation on September 27, 2026 confirmed a TS2 private call from
`2348831` to reflector `2350`: RYSEN activated the `#2350` link and the RD985
received the announcement on TG9. The capture is retained outside the
repository because it contains live network metadata.

The IPSC2 private-parrot capture further confirms the RD985's complete
72-byte private ingress form (`2348831 → 9990`, TS2). IPSC2 returned no
private media because of its independent fault, but OK-DMR's outbound
translator uses the same validated layout and changes the Hytera call marker
to `0x00` for a private DMRD unit call. RYSEN therefore encodes outbound
private calls with the existing paced media path and the `0x00` marker.

Field validation on the native master confirmed that a TS2 private call to
`9990` receives a clear private parrot echo from `9990` back to `2348831`.
The successful capture is retained outside the repository because it contains
live network metadata.

## Outbound group voice

The DMRD-to-Hytera path uses the master-to-repeater forms observed in the
IPSC2 capture:

- TS1 packet types are `01` voice, `02` call start and `03` terminator.
- TS2 sets bit `0x40`, producing `41`, `42` and `43`.
- DMRD Voice LC Headers are currently sent as Hytera `1111` headers. This is
  the only tested form that has produced repeatable clear audio with the
  correct talkgroup and subscriber identity.
- Headerless streams fall back to `EEEE` with the captured interleaved 24-bit
  destination/source identity payload.
- Voice bursts use `BBBB`, `CCCC`, `7777`, `8888`, `9999`, `AAAA`.
- The `BBBB` packet uses the captured `EEEE1111` prefix; other master-originated
  packets use `00000000`, matching the working bridge and reflector captures.
- Voice and terminator packets pass through a one-slot jitter buffer and are
  emitted at 60 ms intervals on the negotiated DMR service endpoint.

Outbound group and private voice are admitted. Malformed DMRD and traffic for
an unregistered repeater remain blocked.

The first live outbound test produced clear audio on TS1/TG31777. A follow-up
test using `1111` Voice LC Headers produced good audio only after delayed call
acquisition and sometimes displayed stale identity data. Packet decoding
confirmed that RYSEN's LC and metadata fields contained the correct source and
talkgroup. Two subsequent `EEEE` acquisition variants produced no audio, so
the audible `1111` build was restored. If a talkgroup is already active when
the RD985 keys to activate it, RYSEN now marks that slot for a short-lived
late join, bypasses contention from the activation transmission, and forwards
the current voice burst directly without synthesizing a new call start. The
first three late-join bursts are buffered before delivery so irregular ingress
at activation becomes a continuous 60 ms stream; ordinary calls retain the
one-slot jitter buffer.

This matches the IPSC2 oracle captured on TS1/TG23426: the RD985's third
activation header ended at `12:24:23.454`, and IPSC2 sent the running call's
`9999` voice burst (sequence 84) at `12:24:23.540`, with no preceding `1111`
or `EEEE` packet.

## Implementation gates

1. **Complete:** validate the native master against an RD985 cold boot.
2. **Complete:** convert captured inbound 72-byte group voice into DMRD.
3. **Complete:** field-test inbound bridge audio and enable Hytera routing.
4. **Implemented, field retest pending:** paced outbound group voice with
   mid-stream dynamic-TG activation.
5. **Complete:** field-test Dial-a-TG private-call ingress and group TG9
   announcement return.
6. **In progress:** add the three-port, NAT-aware multi-repeater proxy.
7. Complete monitor and selfcare integration.
8. Complete optional RDAC/SNMP metadata discovery.

Unknown packet variants, including reported 103-byte media packets, must be
rejected or traced until capture-validated.

## Multi-repeater proxy

`hytera_proxy.py` is the Hytera equivalent of the IPSC proxy. Its first public
session preserves the BrandMeister CPS ports: P2P `50000`, DMR `50001`, RDAC
`50002`. Nine further public triples continue from `50003–50029`, while RYSEN
runs ten private generated backend triples from `50003–50032` with
`PROXY_CONTROL: True`.

The P2P registration has no repeater ID, so a slot is provisional until the
proxy observes the RDAC identity response. In proxy mode RYSEN runs the
capture-validated RDAC exchange to request that response, and the proxy binds
the extracted 24-bit ID to the session. A duplicate or blacklisted ID releases
the session. The proxy separately tracks the NAT source endpoint of P2P, DMR
and RDAC traffic; service redirects are rewritten from backend to public
ports.

Run it with `python3 hytera_proxy.py -c hytera-proxy.cfg`, using
`hytera-proxy-SAMPLE.cfg` as the template. This is not yet deployed or
field-validated in proxy mode. Full RDAC metadata and SNMP collection remain
out of scope for this phase.
