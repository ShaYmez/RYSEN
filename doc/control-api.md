# FreeSTAR control boundary

The listener binds to loopback by default and requires the bearer token in
`/etc/rysen/freestar-control.token`. It is a host adapter for
`freestar-api-v2`; it is not an end-user API.

## Operator actions

Root paths are operator-only and are forwarded from `/control/dmr/*`.

- `POST /disconnect`, `/drop-dynamic`: clear only the target peer's RF and
  dial-a-talkgroup state. The HBP/IPSC session remains connected.
- `POST /drop-call`: immediately suppress the target peer's current HBP stream
  and clear its RF route.
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
