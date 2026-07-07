# OPTIONS String Reference

RYSEN applies runtime settings from semicolon-separated `KEY=value;` **OPTIONS** strings. Hotspots send these via HBP **RPTO** on login; selfcare pushes the same format through MariaDB. System-level OPTIONS apply to `MODE: MASTER` and `MODE: IPSC` slots.

Parser: `options_config()` in `bridge_master.py`. Per-peer keys (STICKY, LINK_IPSC) are parsed from each hotspot peer's RPTO blob.

## Format

```
KEY=value;KEY2=value2;
```

- Separator: semicolon (`;`)
- No spaces required around `=`
- Pi-Star example (`DMRGateway.ini`): `Options=TS1_1=23426;TIMER=10;STICKY=1`
- Selfcare example: `TS1=235,23426;TS2=2350;RelinkTime=15;`

## Canonical keys (after parsing)

These are the internal names RYSEN stores on the system stanza.

| Key | Legacy aliases | Meaning | Values |
|-----|----------------|---------|--------|
| `TS1_STATIC` | `TS1`, `TS1_1`…`TS1_9` | Comma-separated static talkgroups on timeslot 1 | e.g. `235,23426,116` |
| `TS2_STATIC` | `TS2`, `TS2_1`…`TS2_9` | Comma-separated static talkgroups on timeslot 2 | e.g. `2350,2351` |
| `DEFAULT_UA_TIMER` | `TIMER`, `RelinkTime` | Bridge / relink timeout | Minutes (integer) |
| `DEFAULT_REFLECTOR` | `DIAL`, `StartRef` | Startup reflector talkgroup | TG number; `0` = disabled |
| `OVERRIDE_IDENT_TG` | `IDENTTG`, `VOICETG` | Talkgroup used for voice ID announcements | TG number |
| `VOICE_IDENT` | `IDENT` | Enable voice ident announcements | `0` / `1` |
| `ANNOUNCEMENT_LANGUAGE` | `LANG` | Voice prompt language | e.g. `en_GB` (must match installed `Audio/` set) |
| `SINGLE_MODE` | `SINGLE` | Single-mode bridge behaviour | `0` / `1` |
| `LINK_IPSC` | `IPSC` | Link UA bridges to an IPSC slot | e.g. `IPSC-198` |

### DMR+ / Pi-Star multi-TG aliases

DMR+ style uses separate keys per TG position. RYSEN joins them into a comma-separated static list:

| Legacy keys | Becomes |
|-------------|---------|
| `TS1_1=235` | `TS1_STATIC=235` |
| `TS1_1=235;TS1_2=23426;TS1_3=116` | `TS1_STATIC=235,23426,116` |
| `TS2_1=2350;TS2_2=2351` | `TS2_STATIC=2350,2351` |

Up to `TS1_9` / `TS2_9` are supported.

### Timer aliases

| Alias | Maps to | Notes |
|-------|---------|-------|
| `TIMER=` | `DEFAULT_UA_TIMER` | Legacy HBlink / Pi-Star name |
| `RelinkTime=` | `DEFAULT_UA_TIMER` | IPSC2 / DMR+ selfcare convention (minutes) |

`RelinkTime` is preferred for IPSC repeater selfcare. Changing the timer rebuilds bridge timeout entries for that system.

### Reflector aliases

| Alias | Maps to |
|-------|---------|
| `DIAL=` | `DEFAULT_REFLECTOR` |
| `StartRef=` | `DEFAULT_REFLECTOR` |

`DEFAULT_REFLECTOR=9` is invalid (dial-a-tg channel) and is treated as `0`.

## Per-peer keys (hotspot only)

Parsed from each peer's RPTO OPTIONS, not the system stanza.

| Key | Meaning | Values |
|-----|---------|--------|
| `STICKY` | Brandmeister-style sticky talkgroup per user | `1`/`0`, `true`/`false`, `yes`/`no` |
| `IPSC` / `LINK_IPSC` | Wake linked IPSC leg on UA bridge activation | Valid `IPSC-N` slot name, e.g. `IPSC-198` |

Priority for sticky TGs: **static TGs** (`TS1_STATIC`/`TS2_STATIC`) > **peer STICKY** > **system `STICKY_TG`** > default timeout.

## Selfcare keys

| Key | Meaning |
|-----|---------|
| `DISC=1` | One-shot remote disconnect request from dashboard; stripped after apply |

See [selfcare.md](selfcare.md).

## Ignored keys

| Key | Notes |
|-----|-------|
| `UserLink` | DMR+ legacy; stripped and ignored |
| `DISC` | Handled separately before main parse (not stored as a system option) |

## Where OPTIONS are set

| Source | Example |
|--------|---------|
| Pi-Star `DMRGateway.ini` | `Options=TS1_1=23426;TIMER=10;STICKY=1` |
| `rysen.cfg` system stanza | `OPTIONS: TS2=2350;` |
| RYSEN-MONITOR selfcare (MariaDB) | `TS1=235,23426;TS2=2350;RelinkTime=15;` |
| Hotspot RPTO at registration | Sent automatically by MMDVM/Pi-Star firmware |

## Examples

**Pi-Star hotspot with sticky TG and static TS1 TG:**
```
TS1_1=23426;TIMER=10;STICKY=1
```

**Hotspot linked to IPSC repeater on TS2:**
```
TS2=2350;IPSC=IPSC-198;
```

**Selfcare multi-static + relink timer:**
```
TS1=235,23426,116;TS2=2350,2351,2352;RelinkTime=15;
```

**Remote disconnect from dashboard:**
```
TS2=2350;DISC=1;
```

## Related docs

- [features.md](features.md) — sticky TGs, static TGs, dial-a-tg
- [selfcare.md](selfcare.md) — MariaDB selfcare workflow
- [ipsc.md](ipsc.md) — `LINK_IPSC`, IPSC repeater CPS and selfcare
