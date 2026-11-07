# data/

Static input data. These are data files only, not code. `omm/`, `ephem/` and `stations.json` were fetched or written
by the setup session on 2026-11-07, during the hacking period (after 13:00 ET). `pcoe/` and `birds/` were added by
human A for H0-DATA (see [NASA PCoE battery data](#nasa-pcoe-battery-data-pcoe) and
[BIRDS CubeSat EPS data](#birds-cubesat-eps-data-birds)).
App code must read these files and must never call CelesTrak, JPL, NASA or Mendeley itself (CLAUDE.md §2).

| File | Source | Fetched (UTC) | Notes |
|---|---|---|---|
| `omm/fleet.json` | https://celestrak.org/NORAD/elements/gp.php?GROUP=planet&FORMAT=json | 2026-11-07T19:26:23Z | **Real.** CelesTrak GP data (OMM, JSON). One request, HTTP 200, 55,109 bytes. JSON array of 131 objects. EPOCH range 2026-11-05T13:35:56 to 2026-11-07T07:37:42 (UTC). sha256 `9a862f259c4b9ea5e285b8d356675107c47eb272e06eee21116709d377430766` |
| `ephem/de421.bsp` | https://ssd.jpl.nasa.gov/ftp/eph/planets/bsp/de421.bsp | 2026-11-07T19:26:29Z | **Real.** JPL DE421 planetary ephemeris, downloaded with Skyfield `Loader('data/ephem')('de421.bsp')`. 16,788,480 bytes. sha256 `a20a7139da04cbc462454634918e9a9ca69127044e2cc9d4f9c16e238d2deedc` |
| `stations.json` | Written by hand | 2026-11-07 | **Real sites, approximate coordinates** (each entry has `"approximate": true`). 3 ground stations: `SVALBARD`, `TROLL`, `FAIRBANKS`. Fields: `station_id`, `name`, `lat_deg`, `lon_deg`, `alt_m`. |
| `pcoe/B0005.mat`, `pcoe/B0006.mat`, `pcoe/B0007.mat`, `pcoe/B0018.mat`, `pcoe/README.txt` | https://phm-datasets.s3.amazonaws.com/NASA/5.+Battery+Data+Set.zip (inner archive `1. BatteryAgingARC-FY08Q4.zip`) | 2026-11-07T20:18:52Z | **Real.** NASA Ames PCoE Li-ion battery aging data, 4 cells (18650, 2 Ah rated) cycled to end of life at 24 °C. Files are byte-identical to the archive contents. Details, license and checksums in [NASA PCoE battery data](#nasa-pcoe-battery-data-pcoe). |
| `birds/UGUISU.xlsx`, `birds/RAAVANA.xlsx`, `birds/NEPALISAT.xlsx`, `birds/TSURU.xlsx` | Mendeley Data, doi:10.17632/8kp25ycf63.1 (https://data.mendeley.com/datasets/8kp25ycf63/1) | 2026-11-07T20:21:35Z | **Real.** On-orbit EPS telemetry (solar panels and battery) of 4 BIRDS 1U CubeSats, ISS orbit. CC BY 4.0. Files unchanged; sha256 match the values Mendeley publishes. Details in [BIRDS CubeSat EPS data](#birds-cubesat-eps-data-birds). |

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

## BIRDS CubeSat EPS data (`birds/`)

**What it is.** "On-orbit Electrical Power System Dataset of 1U CubeSat constellation for Machine Learning Models",
by Adolfo Jara and Pooja Lepcha (Kyushu Institute of Technology). On-orbit telemetry from 4 BIRDS-bus 1U CubeSats
released from the ISS (about 400 km, 51.6° inclination): voltage, current and temperature of the solar panels on 5
faces (+Y, +X, −Z, −X, +Z) and of the battery. The authors state that UGUISU and RAAVANA each show a failure in one of
their panels.

**Source and fetch.**
- Mendeley Data, version 1, published 2022-05-27, doi:10.17632/8kp25ycf63.1. The authors' GitHub copy
  (https://github.com/BIRDSOpenSource/EPS_dataset, MIT) holds the same 4 files (git blob hashes checked: byte-identical) plus the paper.
- Fetched by human A at 2026-11-07T20:21:35Z: one request per file to the `download_url` listed in the Mendeley
  public API (`https://data.mendeley.com/public-api/datasets/8kp25ycf63`). All 4 returned HTTP 200, and every sha256
  matches the `sha256_hash` Mendeley publishes.

**License.** CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), as stated on Mendeley. Give credit and say if
anything was changed. The files here are unchanged. Cite:
- Dataset: A. Jara and P. Lepcha (2022). "On-orbit Electrical Power System Dataset of 1U CubeSat constellation for
  Machine Learning Models", Mendeley Data, V1, doi:10.17632/8kp25ycf63.1.
- Paper: A. Jara, P. Lepcha, S. Kim, H. Masui, T. Yamauchi, G. Maeda and M. Cho (2022). "On-orbit electrical power
  system dataset of 1U CubeSat constellation", Data in Brief 45, 108697, doi:10.1016/j.dib.2022.108697.

**Files.**

| File | Bytes | sha256 | Sheets | Observed in the data |
|---|---|---|---|---|
| `birds/UGUISU.xlsx` | 737,659 | `4eac4d50e584ac96ba94936b6cd6fe6140b3ab862153b87408bd6911ca88c140` | 3 (2020-10-23, 2021-02-13, 2021-04-10) | **+Y panel degraded**: peak about 3.1 V and ≤ 65 mA, against about 5.4 V and 500 mA on the other faces |
| `birds/RAAVANA.xlsx` | 485,809 | `439edd8a824ff4829d7d2fddd44eb7559fc553cde8726c4eb2860d9b11543035` | 2 (2021-02-13, 2021-03-11) | **+Y panel failed**: voltage about 5 mV (near zero) in every sheet |
| `birds/NEPALISAT.xlsx` | 745,668 | `89ff16ba152712073537ab0bd6a8d653387bfdaf6f38aa19af9a30e2f2699d59` | 3 (2020-11-09, 2020-11-25, 2021-04-10) | All 5 panels healthy (reference) |
| `birds/TSURU.xlsx` | 6,059,029 | `a92442de22185ebeb7964e82ae77a45a9cd59e23606833e8caaf5b81818e353a` | 42 (2021-03-28 to 2022-01-24, plus `Test1 w batt`, `Test2 wo batt`) | All 5 panels healthy (reference) |

The "Observed" column is our own reading of per-sheet maxima, done once while checking the files. It is not a
statement from the authors, who do not say which panel failed.

**Reading the files (notes for T30).**
- Each sheet is one telemetry download, named by its date. TSURU sheet names have no year: March to December are
  2021, and the last two say 2022.
- 37 columns. Column 1 is `Time Stamp` (or `Time stamp`): **seconds since the first sample of that sheet**, not an
  absolute time. No sheet has UTC timestamps. Sampling is about 5 s (UGUISU, RAAVANA, NEPALISAT) or 10–11 s (TSURU).
- Then a `(raw)` hex column and an engineering-unit column for each channel: panel temperatures `T<face> (°C)`, panel
  voltages `V<face> (mV)`, panel currents `I<face> (mA)` for faces `py px mz mx pz`, then `Vbat (V)`, `Ibatt(mA)` and
  `Tbatt (℃)`. Use the engineering-unit columns and convert to SI (V, A).
- There are glitches, for example a `Vbat` of 6.44 V in UGUISU 2021-02-13 and 0.00 V in TSURU March 31, and a few
  rows with an empty time stamp. Filter them out.
- Reading `.xlsx` needs **openpyxl** (pandas uses it), which is not in `backend/requirements.txt` yet.
- TSURU's `Test1 w batt` and `Test2 wo batt` sheets are not described by the authors. Only the +X panel is lit and the
  others read about 1.3 V, so they look like a test rather than on-orbit operation. Leave them out.

## Satellite names

`omm/fleet.json` holds a full CelesTrak group with real satellite names. The app picks 24 satellites from it and
replaces the real names with `SAT-01`..`SAT-24`. Real operator and satellite names never appear in the UI,
logs, narration or API output.

`birds/` file and sheet names are real satellite names too (UGUISU, RAAVANA, NEPALISAT, TSURU). They stay unchanged
here for attribution. Results and the UI refer to them generically, for example "BIRDS 1U CubeSat with a failed
panel", never by name.

## Refreshing

CelesTrak asks clients to download each URL only once per update, and blocks clients that ignore this. If a refresh is
ever needed, a human fetches each URL once, by hand. Never retry after a 403.
