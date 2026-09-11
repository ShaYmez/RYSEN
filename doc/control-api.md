# FreeSTAR control boundary

The listener binds to loopback by default and requires the bearer token in
`/etc/rysen/freestar-control.token`. It is a host adapter for
`freestar-api-v2`; it is not an end-user API.

## Operator actions

Root paths are operator-only and are forwarded from `/control/dmr/*`.

- `POST /disconnect`, `/drop-dynamic`: clear only the target peer's RF and
  dial-a-talkgroup state, and stop that radio receiving those TGs. The HBP/IPSC
  session remains connected. Other radios on the stanza keep the shared UA leg.
- `POST /drop-call`: mute the current over toward this peer only (TX or incoming
  RX) until that `stream_id` ends. The next over is forwarded again.
- `POST /talkgroup`: raise a shared MASTER talkgroup leg.
- `DELETE /talkgroup`: force the shared user-activated MASTER leg and its
  stanza-configured IPSC link down. This affects every radio in the stanza
  because MASTER audio is shared; static OPTIONS legs remain configured.
- `POST /kick`: close and remove the target session.
- `POST /ban`: persist a 1–86400 second Radio-ID ban, kick every connected
  ESSID in that seven-digit family, and reject HBP/IPSC registration.
- `POST /unban`: remove a ban. The radio does not need to be connected.

## Owner device actions

Only the companion API's owner-authenticated `/device/{radio_id}/*` routes
forward to these namespaced paths:

- `GET /device/peer/{radio_id}`
- `POST /device/static-talkgroup`
- `DELETE /device/static-talkgroup/{slot}/{talkgroup}`
- `POST /device/drop-dynamic`
- `POST /device/drop-call`
- `POST /device/disconnect`

Static membership and unlink state are per radio. Audio is not: all peers in a
MASTER stanza hear any live leg on that stanza. Device `disconnect` is the
SystemX TG Manager compatibility action (dynamic unlink plus one-shot
`DISC=1`), not a network logout.

A seven-digit owner ID may locate its sole connected ESSID. When two or more
ESSIDs are online, the listener returns `409` and the caller must target the
exact connected nine-digit ID; it never chooses an arbitrary hotspot.

Hotspot protocol login passwords remain outside this API. Device API keys are
HTTP owner credentials and are never sent to HBP/IPSC hotspots.

## Legacy and third-party compatibility

The control listener is optional. HBP login, RF user activation, reflector
dialling, selfcare `DISC=1`, OpenBridge/IPSC routing, cfg statics and RPTO
OPTIONS continue to work without an HTTP client.

A MASTER with one connected hotspot applies that peer's legacy stanza-wide
RPTO settings over the cfg defaults, including `TIMER`/`RelinkTime`,
`DIAL`/`StartRef`, `SINGLE`, voice/ident and language. On a shared MASTER these
settings remain at the cfg baseline because they cannot safely differ by
destination; `STICKY` and `LINK_IPSC` remain per peer.

Static ownership is always per peer. Standard `TS1=`/`TS2=` and DMR+
`TS1_1=`…`TS2_9=` forms contribute to the live union with cfg defaults, even
when the MASTER stanza had no pre-existing `OPTIONS` value. HBP and IPSC
fanout both enforce per-destination drop-call and drop-dynamic mutes.
