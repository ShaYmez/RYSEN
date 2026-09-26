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
voice routing are field-validated. Outbound and private voice remain gated.

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
stream ID, suppresses duplicate wire sequences, and rejects private calls and
unknown packet lengths until their implementation gates are opened.

## Implementation gates

1. **Complete:** validate the native master against an RD985 cold boot.
2. **Complete:** convert captured inbound 72-byte group voice into DMRD.
3. **Complete:** field-test inbound bridge audio and enable Hytera routing.
4. Add paced outbound group voice with the captured 60 ms cadence.
5. Add private voice.
6. Add RDAC identity and optional SNMP discovery.
7. Add the three-port, NAT-aware multi-repeater proxy.
8. Complete monitor and selfcare integration.

Unknown packet variants, including reported 103-byte media packets, must be
rejected or traced until capture-validated.
