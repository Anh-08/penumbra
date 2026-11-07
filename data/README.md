# data/

Static input data. These are data files only, not code. `omm/`, `ephem/` and `stations.json` were fetched or written
by the setup session on 2026-11-07, during the hacking period (after 13:00 ET). `pcoe/` was added by human A for
H0-DATA (see [NASA PCoE battery data](#nasa-pcoe-battery-data-pcoe)).
App code must read these files and must never call CelesTrak, JPL or NASA itself (CLAUDE.md §2).

| File | Source | Fetched (UTC) | Notes |
|---|---|---|---|
| `omm/fleet.json` | https://celestrak.org/NORAD/elements/gp.php?GROUP=planet&FORMAT=json | 2026-11-07T19:26:23Z | **Real.** CelesTrak GP data (OMM, JSON). One request, HTTP 200, 55,109 bytes. JSON array of 131 objects. EPOCH range 2026-11-05T13:35:56 to 2026-11-07T07:37:42 (UTC). sha256 `9a862f259c4b9ea5e285b8d356675107c47eb272e06eee21116709d377430766` |
| `ephem/de421.bsp` | https://ssd.jpl.nasa.gov/ftp/eph/planets/bsp/de421.bsp | 2026-11-07T19:26:29Z | **Real.** JPL DE421 planetary ephemeris, downloaded with Skyfield `Loader('data/ephem')('de421.bsp')`. 16,788,480 bytes. sha256 `a20a7139da04cbc462454634918e9a9ca69127044e2cc9d4f9c16e238d2deedc` |
| `stations.json` | Written by hand | 2026-11-07 | **Real sites, approximate coordinates** (each entry has `"approximate": true`). 3 ground stations: `SVALBARD`, `TROLL`, `FAIRBANKS`. Fields: `station_id`, `name`, `lat_deg`, `lon_deg`, `alt_m`. |
| `pcoe/B0005.mat`, `pcoe/B0006.mat`, `pcoe/B0007.mat`, `pcoe/B0018.mat`, `pcoe/README.txt` | https://phm-datasets.s3.amazonaws.com/NASA/5.+Battery+Data+Set.zip (inner archive `1. BatteryAgingARC-FY08Q4.zip`) | 2026-11-07T20:18:52Z | **Real.** NASA Ames PCoE Li-ion battery aging data, 4 cells (18650, 2 Ah rated) cycled to end of life at 24 °C. Files are byte-identical to the archive contents. Details, license and checksums in [NASA PCoE battery data](#nasa-pcoe-battery-data-pcoe). |

The CelesTrak and JPL downloads are byte-identical (same sha256) to the copies fetched at 2026-11-07T16:49Z, before
the event, which are in the earlier local commit `f460a34`. CelesTrak had not published newer elements for this group
in between.

## NASA PCoE battery data (`pcoe/`)

**What it is.** The "Battery Data Set" from the NASA Ames Prognostics Center of Excellence (PCoE), Li-ion battery aging
experiments. Batteries #5, #6, #7 and #18 went through repeated charge (CC 1.5 A to 4.2 V, then CV until the current
dropped to 20 mA), discharge (CC 2 A down to 2.7 / 2.5 / 2.2 / 2.5 V) and EIS impedance cycles at room temperature,
until they reached 30 % capacity fade (2 Ah to 1.4 Ah). `pcoe/README.txt` is NASA's original description, unchanged.

**Source and fetch.**
- Listed on the NASA PCoE Data Set Repository
  (https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/),
  item 5, and on data.nasa.gov as "Li-ion Battery Aging Datasets" (https://data.nasa.gov/dataset/li-ion-battery-aging-datasets).
- Fetched once at 2026-11-07T20:18:52Z by human A: one `curl` request to
  https://phm-datasets.s3.amazonaws.com/NASA/5.+Battery+Data+Set.zip, HTTP 200, 209,708,670 bytes, S3 Last-Modified
  2022-09-18T03:42:06Z, sha256 `82302a7db4fc1b34e0b6676326610438d43b816bdf11a69d1d012a464ef2f92e`.
- That zip holds 6 inner zips. Only `1. BatteryAgingARC-FY08Q4.zip` (56,517,371 bytes, sha256
  `f3a4fcd7c3434e06906f4222778445ce6ad42f49c5c78a785bdd676b7c223a2e`) is committed, extracted as is. It is the
  constant-current, room-temperature set that most published work uses, and it is enough to fit an OCV curve and a
  capacity-fade rate. The other 5 inner zips (batteries #25–#56: different temperatures and load profiles, about
  153 MB) are not in the repo. Fetch them by hand if ever needed.

**License and terms.**
- data.nasa.gov shows "License not specified" for this dataset.
- It is a work of NASA, a U.S. federal agency. Such works are generally not subject to U.S. copyright
  (17 U.S.C. §105). This is our reading, not a license statement from NASA.
- The PCoE repository asks publications that use its data to acknowledge the repository and the data donors, and says
  users employ the data at their own risk.
- Required citation: B. Saha and K. Goebel (2007). "Battery Data Set", NASA Prognostics Data Repository, NASA Ames
  Research Center, Moffett Field, CA.

**Files.**

| File | Bytes | sha256 | Contents |
|---|---|---|---|
| `pcoe/B0005.mat` | 15,956,874 | `0eae4585baf3f200c09fe24c5ab884f1889679fc75206ca1aa19da704104f0b0` | 616 cycles (170 charge, 168 discharge, 278 impedance), 2008-04-02 to 2008-05-28. Capacity 1.8565 Ah → 1.3251 Ah |
| `pcoe/B0006.mat` | 16,028,159 | `fa818ab4db5db8ab21e910b6dd6c3e20d3761bb9672089e1a4de8f96074616c5` | 616 cycles (170 / 168 / 278), 2008-04-02 to 2008-05-28. Capacity 2.0353 Ah → 1.1857 Ah |
| `pcoe/B0007.mat` | 16,049,460 | `d022afa086efaf54ab5b63f05220f5be178c8027e2fa8d68589db0e85a441a3b` | 616 cycles (170 / 168 / 278), 2008-04-02 to 2008-05-28. Capacity 1.8911 Ah → 1.4325 Ah |
| `pcoe/B0018.mat` | 8,502,218 | `d1e6c923a43ea1c9666b3a90bbb521757a067fd60b4d17dbfaa49c50b179da69` | 319 cycles (134 / 132 / 53), 2008-07-07 to 2008-08-20. Capacity 1.8550 Ah → 1.3411 Ah |
| `pcoe/README.txt` | 2,834 | `2b0614ccfdde3ca9c0466ad3e0c03903a1dbe98aec22dbe7ea410219cde51f90` | NASA's original description (CRLF line endings) |

All cycles have `ambient_temperature` 24 °C. "Capacity" is the first and last value of the per-discharge `Capacity`
field.

**Reading the files (notes for T07 and T30).**
- MATLAB v5 `.mat`. Read with `scipy.io.loadmat(path, simplify_cells=True)`; the top-level key is the file name, e.g.
  `m["B0005"]["cycle"]`, a list of dicts with `type`, `ambient_temperature`, `time` (MATLAB date vector) and `data`.
  scipy is not in `backend/requirements.txt` or `docs/pinned-versions.txt` yet; the session that needs it posts a REQUEST
  to B1.
- The discharge fields in the files are `Voltage_measured`, `Current_measured`, `Temperature_measured`,
  `Current_load`, `Voltage_load`, `Time`, `Capacity`. NASA's README calls the load fields `Current_charge` and
  `Voltage_charge`, which do not exist in the discharge records.
- `Time` is seconds from the start of each cycle. `time` (cycle start) has no timezone in the source; treat it as
  local lab time, not UTC.
- These are laboratory 18650 cells, not flight hardware. Using them for Penumbra's EPS model is an approximation, and
  any fitted parameter must cite this dataset (ISSUES.md T07).

## Satellite names

`omm/fleet.json` holds a full CelesTrak group with real satellite names. The app picks 24 satellites from it and
replaces the real names with `SAT-01`..`SAT-24`. Real operator and satellite names never appear in the UI,
logs, narration or API output.

## Refreshing

CelesTrak asks clients to download each URL only once per update, and blocks clients that ignore this. If a refresh is
ever needed, a human fetches each URL once, by hand. Never retry after a 403.
