# data/

Static input data fetched by a human before the event. These are data files only, not code.
App code must read these files and must never call CelesTrak or JPL itself (CLAUDE.md §2).

| File | Source | Fetched (UTC) | Notes |
|---|---|---|---|
| `omm/fleet.json` | https://celestrak.org/NORAD/elements/gp.php?GROUP=planet&FORMAT=json | 2026-11-07T16:49:01Z | CelesTrak GP data (OMM, JSON). One request, HTTP 200. JSON array of 131 objects. EPOCH range 2026-11-05T13:35:56 to 2026-11-07T07:37:42 (UTC). sha256 `9a862f259c4b9ea5e285b8d356675107c47eb272e06eee21116709d377430766` |
| `ephem/de421.bsp` | https://ssd.jpl.nasa.gov/ftp/eph/planets/bsp/de421.bsp | 2026-11-07T16:49:21Z | JPL DE421 planetary ephemeris, downloaded with Skyfield `Loader('data/ephem')('de421.bsp')`. 16,788,480 bytes. sha256 `a20a7139da04cbc462454634918e9a9ca69127044e2cc9d4f9c16e238d2deedc` |
| `stations.json` | Written by hand | 2026-11-07 | 3 ground stations: `SVALBARD`, `TROLL`, `FAIRBANKS`. **Coordinates are approximate** (each entry has `"approximate": true`). Fields: `station_id`, `name`, `lat_deg`, `lon_deg`, `alt_m`. |

## Satellite names

`omm/fleet.json` holds a full CelesTrak group with real satellite names. The app picks 24 satellites from it and
replaces the real names with `SAT-01`..`SAT-24`. Real operator and satellite names never appear in the UI,
logs, narration or API output.

## Refreshing

CelesTrak asks clients to download each URL only once per update, and blocks clients that ignore this. If a refresh is
ever needed, a human fetches each URL once, by hand. Never retry after a 403.
