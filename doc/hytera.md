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
and group parrot echo are field-validated. Mid-stream group join (late entry)
is field-validated with immediate RF audio and stable source/TG display.

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
72-byte private ingress form (`2348831 → 9990`, TS2). RYSEN's outbound
translator uses the same validated layout and changes the Hytera call marker
to `0x00` for a private DMRD unit call. Outbound private calls use the same
paced media path as group calls.

Field validation on October 1, 2026 confirmed clear, normal-speed parrot audio
for both a group call to TG `9990` and a private call to unit `9990`. The
private return is sent from `9990` to the calling subscriber exactly once; the
PARROT playback path is excluded from the generic unit relay so it cannot
duplicate and stretch the audio. Successful captures are retained outside the
repository because they contain live network metadata.

## Monitor and selfcare

Hytera peers report to RYSEN-MONITOR as `PROTOCOL: HYTERA`. Linked Systems
renders them in the repeater section with a yellow **Hytera** ID pill and black
text, matching the dashboard hotspot-count card. The peer tooltip retains the
protocol, timeslot statics, and RDAC radio information, but displays only the
short model prefix (for example, `RD985`) rather than the full hardware suffix.

Native RDAC registration also enables the existing repeater selfcare lifecycle:

- a `Clients` record is created or refreshed with database `mode = -1`;
- first-time claim, callsign/DMR-ID login, password changes, TS1/TS2 static
  talkgroups, and one-shot dynamic disconnect behave like IPSC repeaters;
- saved options are re-applied after a fresh Hytera registration;
- no CPS settings, SNMP, or device runtime controls are exposed.

The PHP selfcare UI cannot read the monitor process's in-memory peer report, so
validated RDAC metadata is copied to `HyteraMetadata` as a presentation cache.
It stores firmware, full hardware string, serial, callsign, raw mode, and
TX/RX frequency. It is best-effort only: the live Hytera peer record remains
the protocol source of truth, and the cache cannot send commands to a repeater.

## RDAC metadata

The capture-validated RDAC sequence now collects and publishes metadata in the
existing RYSEN peer report. It validates packet prefixes and minimum lengths
before decoding fixed-width UTF-16LE fields, then retains the raw channel mode
without assigning semantics that have not been independently verified.

The published fields are:

- repeater DMR ID;
- firmware (`SOFTWARE_ID`);
- hardware/model (`DESCRIPTION` and `HYTERA_HARDWARE`);
- serial number (`SERIAL` and `HYTERA_SERIAL`);
- callsign (`HYTERA_CALLSIGN`, and `CALLSIGN` where it was previously only the
  numeric DMR ID);
- raw channel mode, TX frequency and RX frequency.

Live RD985 validation on September 27, 2026 published firmware
`A9.02.03.009`, hardware `RD985-00000000-000000-U1-0-F`, callsign `GB7NR`,
and TX/RX frequencies `439437500`/`430437500` Hz for repeater `235287`.
The serial number was also populated in the peer report. SNMP is deliberately
out of scope: all implemented metadata comes from the native RDAC exchange.

## Outbound group voice

The DMRD-to-Hytera path uses the master-to-repeater forms observed in the
IPSC2 capture:

- Packet types are `01` voice, `02` call start and `03` terminator on both
  slots. The `0x40` bit belongs to repeater-to-master traffic, not TS2
  downlink.
- DMRD Voice LC Headers are currently sent as Hytera `1111` headers. This is
  the only tested form that has produced repeatable clear audio with the
  correct talkgroup and subscriber identity; the field-proven RYSEN header
  retains its zero prefix.
- Headerless streams fall back to `EEEE` with the captured interleaved 24-bit
  destination/source identity payload.
- DMR Voice A-F map in order to Hytera envelopes `7777`, `8888`, `9999`,
  `AAAA`, `BBBB`, `CCCC`.
- Voice E (`BBBB`) uses the captured `EEEE1111` prefix. Other voice bursts,
  synthesized `EEEE` call starts and terminators use `5A5A5A5A`.
- Terminators use the captured `2222` envelope and clear bytes 20-25.
- Voice and terminator packets pass through a three-slot (180 ms) jitter
  buffer and are emitted at 60 ms intervals on the negotiated DMR service
  endpoint. Late-entry retains its separate one-slot release path.

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

The late-entry output is held through the three-header activation sequence,
then released on the next available burst from the active OpenBridge stream.
This avoids transmitting while the RD985 is still acquiring the call and
preserves the source, destination, sequence and current voice phase.

### IPSC2 mid-stream oracle

The September 28, 2026, two-way captures on `ipsc2.freestar.network` prove
the same behaviour on both RF slots:

- The RD985 sends three Voice LC Headers (`1111`) to announce its activation.
  Those packets have type `0x41`, source `2348831`, and group call marker
  `0x01`.
- IPSC2 resumes at the active stream's next RF burst boundary. The observed
  delay varies with phase: TS1/TG235 resumed 200 ms after the third activation
  header (`9999`), while TS2/TG2352 resumed after 64 ms (`9999`) and 85 ms
  (`AAAA`) in separate captures.
- It sends ordinary master voice type `0x01` on both slots, retaining the
  timeslot marker (`1111` or `2222`) at bytes `16:18`.
- Neither exchange used a new `1111` header, `EEEE` call start, or `DDDD`
  wakeup. The following packets continued at the normal 60 ms cadence.

RYSEN's late-entry encoder therefore waits for the RD985's full 180 ms
activation interval, then emits the next current voice burst with packet type
`0x01` on both slots. It preserves the source, destination, sequence, current
voice phase, `5A5A5A5A` prefix (or `EEEE1111` for Voice E), and the active
timeslot. The RD985's TS2 activation headers remain repeater-to-master type
`0x41`; RYSEN's resumed master-to-repeater voice is type `0x01`.

### Earlier late-entry field result

The September 28 test-server comparison initially deferred this milestone.
During a TS1/TG235 activation, the RD985 sent its three `1111` headers at 0,
58 and 118 ms. RYSEN began the running stream 176–220 ms after the third
header and continued with 60 ms cadence. The backend packets carried the
expected type `0x01`, TS1 marker, current voice phase, source, destination,
call type and prefixes. The Hytera proxy forwarded each packet byte-for-byte
to the RD985 in about 0.3 ms.

Despite that observable parity with the IPSC2 captures, the RD985 did not
decode the late-entry audio after de-key. The outstanding work is a controlled
IPSC2-oracle replay through the test-server/proxy path to distinguish an
unobserved native packet semantic from repeater session-state behaviour. Do
not make further framing, timing or sequence changes without that evidence.

Compatibility with an A8.09.00.001 RD985 remains unverified until that unit is
available. Its validation starts with the existing A9 cold-boot and normal
call matrix, followed by the same controlled IPSC2 late-entry replay. Firmware
compatibility must be recorded from capture results, not inferred from the
shared IP Multi-site Connect configuration.

### Matched IPSC2 late-entry oracle

On September 29 a timestamped, bidirectional IPSC2 capture recorded successful
late entry on both slots. On TS2/TG2352 the RD985 sent activation Voice LC
Headers at `19:48:56.277910`, `.335696` and `.397190` UTC. IPSC2 sent the
running stream's current Voice D burst at `.496385`: 99.195 ms after the third
header and while the repeater was still sending its activation call. No new
downlink header, sync or wakeup preceded that burst. RF audio and the remote
TG/source display appeared immediately after de-key. TS1/TG235 also completed
late entry correctly in the RF test, but its first captured downlink burst was
239.837 ms after the third activation header and therefore did not have TS2's
same timing or initial-burst retention.

The failed RYSEN comparison exposed two measurable differences:

- Its first running-stream burst was 159.612 ms after the third activation
  header, one 60 ms RF slot later than the IPSC2 oracle.
- When a Voice C burst was absent, IPSC2 continued from Voice B to Voice D in
  119.623 ms. RYSEN's output pacer drained and applied its 180 ms startup
  buffer again, extending the equivalent gap to 226.271 ms. Rebuffering in the
  middle of a live call breaks audio acquisition and embedded-LC continuity.

The candidate fix arms routing when the third activation header is due at
120 ms, leaving the existing one-slot pacer acquisition to match IPSC2. The
pacer now applies its three-burst jitter buffer only at initial call
acquisition. After playout starts, an underflow does not reapply that buffer;
the next burst is sent at the first valid 60 ms boundary after the preceding
transmission. Missing DMRD sequence positions remain missing in the Hytera
wire sequence instead of being compressed. A terminator restores startup
buffering for the next call. Late-entry authorization is Hytera-only, is bound
to one stream, and is applied to both OpenBridge- and Homebrew-originated
traffic. This candidate then proceeded to the RF matrix documented below.

### Final late-entry field validation

On October 1, 2026 the final capture-derived encoder was deployed to
`hytera.freestar.network` and tested on the A9 RD985. While TG `67498` was
already active, the repeater keyed to join it and network audio appeared on RF
immediately after de-key. The display showed the correct talkgroup and
subscriber identity from acquisition onward.

The successful implementation preserves the IPSC2 downlink burst phase,
master packet types, envelope prefixes and wire sequence. It releases the
current running-stream burst after the repeater's three-header activation,
does not synthesize another call start, and does not reapply the 180 ms startup
buffer after a mid-call underflow. This closes the A9 mid-stream group-join
milestone. The Hytera proxy was capture-checked byte-for-byte and did not
mutate, omit or duplicate media.

### Normal call identity oracle

The same IPSC2 capture includes a clean TS1/TG235 normal call start. Its
`EEEE` packet is type `0x02`, carries the group marker and source/destination
in the outer fields, and contains the same IDs interleaved in its 34-byte
payload. Its prefix is `5A5A5A5A`, not zeroes. RYSEN now uses that prefix for
the headerless call-start fallback so an RD985 cannot retain stale display
identity before the following voice bursts arrive.

## Audio and static hardening

Stress testing on September 27, 2026, with several busy TS1 statics, showed
intermittent missing audio and incorrect talkgroup or subscriber displays.
One RF timeslot can transmit only one call. Every configured static stays
subscribed, but the first admitted call owns that slot. Other simultaneous
talkgroups are blocked for the call and its group hangtime. They are not
queued or replayed.

OpenBridge ingress on UDP `62039` was correlated with RD985 egress on the
private DMR backend UDP `50004`:

- Reliable calls, including TS2/TG2350 and TG67498, began with a Hytera
  `1111` Voice LC Header and continued on the normal voice cycle at about
  60 ms.
- Incomplete TG31777 calls began with the headerless `EEEE` fallback, packet
  type `0x02` and sequence 0, even though the OpenBridge stream contained a
  real Voice LC Header (`flags 0x21`) before its voice bursts.
- Target-slot contention dropped that header. A later voice burst was then
  admitted. The encoder had no output stream, so it emitted `EEEE` and
  discarded the triggering 60 ms frame. The RD985 therefore started without
  valid call context, which matches the missing audio and stale or random
  talkgroup and DMR ID.
- ZL2BEZ traffic that originated on XLXD and arrived over OpenBridge used a
  normal `1111` header and 60 ms cadence in the same capture. No separate
  XLXD pacing defect was proven.

The router now keeps the original Voice LC Header while a Hytera slot rejects
a new stream, for both OpenBridge and Homebrew ingress. When a later frame
from that same stream is admitted, and the header is still inside the stream
timeout plus one 60 ms burst boundary, RYSEN sends that captured header before
the voice burst. It does not synthesize a replacement LC, and this
delayed-admission path does not use the `EEEE` fallback. The headerless
`EEEE` path remains only for a stream that never presented a Voice LC Header.

The retained-header change originated in `3250e3c`. Final field validation on
October 1 confirmed clean normal-call audio and stable source/TG identity
after slot release. The completed outbound phase, identity, late-entry and
private-parrot fixes are in `feature/HYTERA` commit `c5fc322` and are deployed
on `hytera.freestar.network`.

### Reliability regression coverage

The automated suite includes a sanitized, capture-derived TS1 native call
fixture, validates its 72-byte Hytera framing, and tests the contention
release boundary used to preserve a delayed Voice LC Header. It also protects
the RDAC exchange from harmless `0x00` service polls while identity discovery
is in progress. `RDAC_DISCOVERY` is parsed for direct Hytera masters; it was
previously documented but omitted by the configuration loader.

These checks protect packet construction and routing decisions. The matching
RF run confirmed clean audio and stable source/TG display after a contended
static becomes the admitted call.

### Completed A9 RD985 validation matrix

1. **Complete:** cold boot, negotiated DMR endpoint, RDAC identity and
   keepalive stability.
2. **Complete:** normal TS1/TS2 calls with clear audio and stable source/TG
   identity.
3. **Complete:** delayed admission retains the original `1111` header; field
   audio and identity are stable after slot release.
4. **Complete:** proxy power-cycle recovery replaces the old session and
   renegotiates DMR/RDAC service redirects.
5. **Complete:** group and private/unit calls to `9990` return clear,
   normal-speed parrot audio.
6. **Complete:** mid-stream join on active TG `67498` produces immediate RF
   audio and correct display identity.

Keep binary captures outside Git and analyse copies with
`tools/analyze_hytera_capture.py`.

## Implementation gates

1. **Complete:** validate the native master against an RD985 cold boot.
2. **Complete:** convert captured inbound 72-byte group voice into DMRD.
3. **Complete:** field-test inbound bridge audio and enable Hytera routing.
4. **Complete:** mid-stream group join on the A9 RD985, with immediate audio
   and stable source/TG display on active TG `67498`.
5. **Complete:** field-test Dial-a-TG private-call ingress and group TG9
   announcement return.
6. **Complete:** field-test the three-port, NAT-aware multi-repeater proxy.
7. **Complete:** monitor and selfcare integration, including read-only RDAC
   metadata display and IPSC-parity repeater selfcare lifecycle.
8. **Complete:** collect and publish RDAC firmware, hardware/model, serial,
   callsign, raw mode and TX/RX frequency metadata. SNMP remains out of scope.
9. **Complete:** replay the original Voice LC Header when slot contention
   delays a Hytera static. Field testing confirmed clean audio and stable
   identity after release. One RF slot still carries only one call at a time.

Unknown packet variants, including reported 103-byte media packets, must be
rejected or traced until capture-validated.

## Multi-repeater proxy

`hytera_proxy.py` is the Hytera equivalent of the IPSC proxy. Its first public
session preserves the BrandMeister CPS ports: P2P `50000`, DMR `50001`, RDAC
`50002`. Nine further public triples continue from `50003–50029`, while RYSEN
runs ten private generated backend triples from `50003–50032` with
`PROXY_CONTROL: True`. Each generated master must also set
`PROXY_CONTROL_IP` to the sidecar's trusted source address (the supplied
Compose network uses `172.16.238.31`); lifecycle control from any other source
is ignored.

The P2P registration has no repeater ID, so a slot is provisional until the
proxy observes the RDAC identity response. In proxy mode RYSEN runs the
capture-validated RDAC exchange to request that response, and the proxy binds
the extracted 24-bit ID to the session. A duplicate or blacklisted ID releases
the session. The proxy separately tracks the NAT source endpoint of P2P, DMR
and RDAC traffic; service redirects are rewritten from backend to public
ports. A fresh P2P registration clears stale DMR/RDAC endpoints and identity
before service renegotiation. Repeaters behind the same NAT can use separate
public triples (`50000`, `50003`, and so on) without sharing a session.

Run it with `python3 hytera_proxy.py -c hytera-proxy.cfg`, using
`hytera-proxy-SAMPLE.cfg` as the template.

### Proxy field validation

The proxy is deployed on `hytera.freestar.network` from the local
`feature/HYTERA` build. The RD985 CPS uses the BrandMeister-compatible public
ports `50000/50001/50002`. RYSEN's generated backends stay private, starting at
`50003/50004/50005`.

Validated on September 27, 2026:

- The RD985 registers as `HYTERA-0`. Service redirects leave RYSEN on the
  private backend ports and the proxy rewrites them to public `50001/50002`.
- RDAC identity binding learns repeater `235287`.
- Group voice, Dial-a-TG, the TG9 status announcement, and private parrot echo
  all pass through the proxy.
- A quick power cycle replaces the existing `HYTERA-0` session and restores
  both service redirects without waiting for the idle timeout.
- A second dummy client, from a different source address, is allocated
  `HYTERA-1` and public DMR/RDAC ports `50004/50005` while the RD985 remains
  on the first slot. A second physical repeater has not been tested.
- Startup requests originate from the repeater's DMR and RDAC source ports,
  but redirect replies must remain on the registered P2P socket.

The proxy process stayed near zero CPU and about 3 MB. The only reactor-lag
warning was the one-time alias load during a full stack restart. RDAC metadata
is collected by the RYSEN backend; SNMP collection remains out of scope.
