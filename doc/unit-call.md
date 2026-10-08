# Unit calls

A unit call is a private voice transmission from one subscriber to another.
It shipped in **1.6.0**. Delivery on this master is always available. A call
leaves the server only when fleet routing is configured.

Dial-a-tg, parrot, SMS, and GPS are separate paths. See
[ipsc-roadmap.md](ipsc-roadmap.md) for the data phases that are still deferred.

## Which IDs are unit destinations

| Length | Example | Unit call? |
|--------|---------|------------|
| ≤ 5 digits | `2350`, `4000`, `5000`, `9990` | No — talkgroup, dial-a-tg, or parrot |
| 6 digits | `235287` | No — repeater radio ID |
| 7 digits | `2345678` | Yes — the subscriber to call |
| 8 or 9 digits | hotspot ESSID | Peer identity used to find that subscriber |

Private-call **7-digit** radio ID. RYSEN treats any destination at or
above `1000000` as a subscriber. Talkgroup ACLs do not apply. The subscriber
ACL still does apply.

`4000`, `5000`, link talkgroups, and parrot `9990` stay on their own paths.

## Local delivery

No extra configuration. An empty `[ALIASES] UNIT_SUB_MAP_URL` does no
topology HTTP and does not relay private voice over OpenBridge.

RYSEN looks for the callee on this master:

1. **`SUB_MAP`** — the slot where that subscriber was last heard, when it is
   another `MASTER`, `IPSC`, or `HYTERA` system and the peer is still
   connected.
2. **Hotspot ESSID** — a connected hotspot whose peer ID starts with that
   7-digit radio ID, when `SUB_MAP` has no row.

Hotspot delivery uses **TS2**. An IPSC or Hytera callee uses the slot they
were last heard on. A busy target slot drops the call. The system that
received the call is not a delivery target.

`SUB_MAP` stores `(system, timeslot, talkgroup, timestamp, peer)`. It is
location history from the subscriber's own transmissions.

## Fleet hop

Set both of these to participate. Leave the URL empty for local-only.

```ini
[ALIASES]
UNIT_SUB_MAP_URL: https://example.invalid/v2/internal/sub-map
UNIT_SUB_MAP_TOKEN_FILE: /etc/rysen/unit-sub-map.token
```

The token file holds the bearer credential. It is sent only as an
`Authorization` header. Health reports do not include passphrases,
credentials, or the token.

`[GLOBAL] SERVER_ID` must be a net ID in the downloaded server registry
(`SERVER_ID_URL` / `SERVER_ID_FILE`). A master whose ID is absent from that
registry does not publish topology. Scotland stays local-only until its
current `2355` identity is reconciled with registry ID `2354` and it has a
reciprocal link to a participating fleet master. Its CQ-UK link is not a
transit hop.

Fleet links are configured proto-v5 enhanced OpenBridge peers whose
`NETWORK_ID` is also in that registry. Other OpenBridge stanzas are not
transit.

### What this master publishes

On startup, then again every 45–75 seconds, RYSEN posts a health snapshot
to the api hub. If `UNIT_SUB_MAP_URL` ends in `/sub-map`, that suffix is
replaced with `/topology/report`. Each peer in the snapshot is one fleet
OpenBridge link and whether its last keepalive is within 60 seconds.

### How a call is routed

The hub is asked for `{UNIT_SUB_MAP_URL}/{radio_id}` with this master's net
ID as `from_net_id`. A usable reply names the home, the immediate next hop,
a loop-free path, a topology version, and an expiry. The path starts here,
ends at the home, stays inside the registry, and is at most 10 hops.

Voice goes to that one healthy next hop. The start of the call is buffered
while the lookup runs. A failed hop is looked up again a limited number of
times, then dropped. A third-party OpenBridge stanza is not a candidate.

A callee with a recent RF hear on this master is delivered locally. A
subscriber that is only logged in here is not stolen from another fleet
home: inbound and transit frames follow the learned or hub route, and a
matching local peer is used only when this master is the home or the hub
has none. Inbound and transit calls do not record a local listen for the
caller, so the call is not sent back the way it arrived. DMRE v5 origin
server, origin repeater, and the existing hop count travel with the voice.
The hop limit remains 10.

`UNIT_OBP_FLOOD` stays `False`. It is a legacy flag and does not relay unit voice. A network without the discovery hub stays local-only. 

## Monitor events

Unit voice keeps the existing comma-separated report layout:

`UNIT VOICE,action,RX|TX,system,stream,peer,subscriber,slot,destination[,duration]`

The terminal duration remains field 9 (zero-based). Existing caller-side
`START` and `END` events are unchanged, including their original stream ID.
Additional actions describe the route without pretending that transit audio
was heard locally:

- `TO START` / `TO END` use `TX` and identify the exact local destination
  system, peer, and delivery slot. Hotspots therefore show TS2, while IPSC and
  Hytera show the slot on which the callee was heard.
- `VIA START` / `VIA END` identify each fleet OpenBridge leg. An inbound leg
  uses `RX`; an outbound next-hop leg uses `TX`. The peer field is that
  OpenBridge stanza's network ID.

Each leg emits at most one start and one end for a stream. Duplicate or stale
terminators are ignored. A transit or final-home master emits `VIA` (and, at
the final home, `TO`) events but does not emit a false caller-side `START` or
`END`.

## Configuration

Local-only, as shipped in the samples:

```ini
[GLOBAL]
SERVER_ID: 0
UNIT_OBP_FLOOD: False

[ALIASES]
UNIT_SUB_MAP_URL:
UNIT_SUB_MAP_TOKEN_FILE:
```

`SERVER_ID: 0` is fine while the URL is empty. Set a real registry net ID
before enabling the fleet hop.

## Related docs

| Doc | Purpose |
|-----|---------|
| [features.md](features.md) | 1.6.0 summary |
| [ipsc-roadmap.md](ipsc-roadmap.md) | Phase 4 status and later data phases |
| [ipsc.md](ipsc.md) | Motorola private-voice wire path |
| [hytera.md](hytera.md) | Hytera private calls on this master |
