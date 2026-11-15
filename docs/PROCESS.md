# How the Tonle Sap analysis was done, start to finish

**Question:** does NISAR show the Tonle Sap flood pulse spreading into the flooded forest, June to November 2026?

**Short answer:** yes.
- Open water in a 40 × 40 km window grows from about 610 km² (June) to about 1050 km² (mid-October).
- "Flooded forest" (trees with very bright radar return) grows from about 40  km² to a peak of about 310 km² on Aug 30, then falls to about 140 km². The water first spreads *under* the trees, then rises *over* the low canopy.
- Sentinel-2 can't see this through cloud. Sentinel-1 sees it only faintly.

Everything below is one script, [`src/tonlesap.py`](../src/tonlesap.py), run step by step. Each step caches what it reads, so later steps run offline.

---

## Radar in five sentences (just what this project needs)

1. NISAR is a radar satellite. It sends microwaves (L-band, ~24 cm wavelength) and measures how much comes back, which is called **backscatter**, in dB. It works through cloud and at night.
2. **Calm open water is dark** (below about −15 dB). It is a mirror, so the signal bounces away from the satellite.
3. **Flooded forest is very bright in HH.** The signal hits the water, bounces off a trunk, and comes straight back. This is called **double bounce**.
4. **HH** means sent horizontal, received horizontal. **HV** means sent horizontal, received vertical. Double bounce raises HH much more than HV, so a rising **HH − HV** is a second sign of flooding under trees.
5. Radar images are grainy (**speckle**). We average pixels in linear power, then convert to dB, before applying any threshold.

L-band matters here. Its long wavelength gets through the leaves to the water underneath. Sentinel-1 (C-band, ~5.6 cm) mostly bounces off the leaves.

---

## Step by step

| # | What and why | Command / code | Output |
|---|---|---|---|
| 1 | **Inventory.** Which NISAR GCOV images (calibrated backscatter on a map grid) cover the window from June 17? Pick the series with the most dates in *one* mode, because mixing 20 m and 10 m modes breaks a time series. | `inventory()` | [`outputs/tables/inventory.csv`](../outputs/tables/inventory.csv) |
| 2 | **Place the window.** The first guess (103.5–103.9E, 12.9–13.3N) had almost no rice. The ESA WorldCover map showed that, so the window moved NE to **103.55–103.95E, 13.0–13.4N**: lake + forest/wetland ring + rice belt (+ Siem Reap edge). | `wc_preview()` | [`wc_preview.png`](../outputs/figures/wc_preview.png) |
| 3 | **Choose the series.** Track 083, ascending, frame 008, mode 2005 (20 m, HH+HV), full frame: **10 provisional dates (Jun 19 – Oct 17), 12 days apart**, with an instrument gap of Jul 26 – Aug 17. Plus 1 validated date, Oct 29 (newer processing version, kept but flagged). | `TRACK_KEY` | – |
| 4 | **Read only the window.** Each NISAR file is ~3 GB. We *stream* it over HTTPS (`earthaccess.open` + `h5py`) and cut out only our 2223 × 2179 pixel window of HHHH, HVHV and the validity mask. That's about 1 minute per date. A watchdog restarts the download if it hangs. | `fetch()`, `fetchloop()` | `data/raw/nisar/<date>.npz` |
| 5 | **Land cover on the radar grid.** WorldCover 2021 (tree, wetland, crop, water…) is resampled onto the exact radar pixels, plus a dry-season Sentinel-2 photo for context. Both come from Microsoft Planetary Computer. | `load_wc_on()` (called by `analyze`) | [`context_wc_s2.png`](../outputs/figures/context_wc_s2.png) |
| 6 | **First look (go/no-go)** on 3 dates: low water, middle, near peak. Thresholds are **open water HH < −15 dB**, **flooded forest = tree pixels with HH ≥ −3 dB** (the −5 dB looser threshold is shown too). We also checked for artifacts: whole-scene jumps (built-up and water should not move), and stripes in row/column profiles of the date-to-date difference. | `analyze()` | [`hh_3dates.png`](../outputs/figures/hh_3dates.png), [`change_flooded_forest.png`](../outputs/figures/change_flooded_forest.png), [`summary.json`](../outputs/tables/summary.json) |
| 7 | **Full time series**, all 11 dates. Pixels are averaged into **100 m cells** (5 × 5, linear power). A cell counts as "tree" if ≥ 50% of it is WorldCover tree (540 km²). Per date we record open water, flooded forest at both thresholds, and median tree HH−HV, wetland HH and crop HH. | `series()` | [`series.csv`](../outputs/tables/series.csv), [`areas_over_time.png`](../outputs/figures/areas_over_time.png) |
| 8 | **"Only NISAR?" check** on Aug 30. Least-cloudy Sentinel-2 within ±10 days (Aug 27: 52% cloud in the window). Same-day Sentinel-1 RTC (C-band VV/VH). Compare how much brighter the flooded-forest ring is than other trees. The ring is picked from NISAR's *Aug 18* image, so Aug 30 noise can't favour NISAR. Result: NISAR +2.3 dB, Sentinel-1 +0.8 to +1.0 dB. | `compare()` | [`compare_s2_s1_nisar.png`](../outputs/figures/compare_s2_s1_nisar.png), [`compare_s1_s2.json`](../outputs/tables/compare_s1_s2.json) |
| 9 | **Web export.** One greyscale PNG per date (100 m, fixed scale −22 to +2 dB, transparent = no data), plus `dates.json` and `bounds.json` (four lon/lat corners for a MapLibre image layer). | `export()` | [`outputs/web/`](../outputs/web/) |
| 9b | **Colour layers for the story map.** A per-date **flood map** (same thresholds as `series.csv`, so its areas match exactly). A **NISAR radar colour** composite (red = HH, green = HV, blue = HH÷HV, one fixed stretch). A **time-colour** image (red = 30 Aug, green = 19 Jun, blue = 17 Oct). A **"time under water"** map: the count of the 10 images in which a cell was open water or flooded forest, 3 × 3 median-smoothed. | `webcolor()` | `outputs/web/flood_*.png`, `radar_*.png`, `timecolor.png`, `duration.png`, `classes.csv`, `layers.json` |
| 10 | **Older data?** All GCOV collections (BETA, provisional, validated) from Aug 2025 – Jun 16 2026 have **nothing** over this window, or anywhere in Cambodia. So there's no 2025 vs 2026 comparison. | `older()` | [`older_inventory.csv`](../outputs/tables/older_inventory.csv) (empty) |

---

## Results (from `series.csv`)

| Date | Open water km² | Flooded forest ≥ −5 dB | ≥ −3 dB | Tree HH−HV dB |
|---|---|---|---|---|
| Jun 19 | 611 | 39 | 1.5 | 4.9 |
| Jul 25 | 664 | 112 | 3.6 | 5.1 |
| Aug 18 | 748 | 294 | 12 | 6.5 |
| **Aug 30** | 804 | **311** | 41 | 7.3 |
| Sep 11 | 892 | 252 | **57** | 7.6 |
| Oct 17 | **1050** | 139 | 14 | 7.5 |
| Oct 29 (validated) | 1018 | 143 | 7 | 7.1 |

**How to read it:**
- As the lake rises, water first spreads under the trees, which become bright (double bounce). That peaks around Aug 30 – Sep 11.
- Then the water rises over the low trees and shrubs. Those pixels turn dark, like open water, so "flooded forest" shrinks while open water keeps growing.
- Herbaceous wetland goes from −8 to −18 dB: fully submerged.

---

## Choices worth knowing (and their limits)

- **Thresholds (−15, −3, −5 dB) are textbook values**, not tuned to this site. That's why both forest thresholds are reported. The −5 dB numbers are ~5–20× the −3 dB numbers, so the *trend* is solid but the exact km² depends on the threshold.
- **WorldCover 2021 "tree"** stands in for the flooded forest. Much of the Tonle Sap "flooded forest" is shrubland, which WorldCover partly calls herbaceous wetland.
- **Averaging in linear power, not dB.** Averaging dB values biases the result low.
- **One mode, one track.** Other tracks (076D, 148D) exist, but they split dates between HH+HV and HH-only, or cover only part of the frame.
- **Provisional data.** No whole-scene jumps were found, and row profiles were clean. Column profiles show 4 bins off by > 1 dB. These are most likely the real north–south flood front on the west shore (not confirmed).
- **Sentinel-1 covers only 74% of the window** on Aug 30 (it misses the western edge). The comparison uses only the overlap.

---

## Which files to read, in order

1. **This file.** It explains the why.
2. [`outputs/figures/areas_over_time.png`](../outputs/figures/areas_over_time.png) and [`outputs/figures/compare_s2_s1_nisar.png`](../outputs/figures/compare_s2_s1_nisar.png). These are the story in two pictures.
3. [`src/tonlesap.py`](../src/tonlesap.py), read in this order:
   - the docstring at the top (all steps)
   - `fetch()`: how a 3 GB file is read without downloading it
   - `series()`: the core metrics
   - `compare()`: the Sentinel check
4. [`src/nisar_common.py`](../src/nisar_common.py): login, retries, how NISAR file names are decoded.
5. [`CLAUDE.md`](../CLAUDE.md): the decisions log, with dates.
