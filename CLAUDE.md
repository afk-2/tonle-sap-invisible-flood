# NASA Space Apps 2026 - "Dancing with SARs" (NISAR): Tonle Sap flood pulse

Team: Afk + teammates. Challenge: build an interactive, open-source app/report/story that uses NISAR radar data
to track and visualize surface change over time and hypothesize the physical drivers.
Afk is a data scientist with no radar background: explain radar choices briefly, in plain words.

## Story
Does NISAR show the Tonle Sap (Cambodia) flood pulse spreading into the flooded forest, Jun-Nov 2026?
Answer so far: yes - see docs/PROCESS.md (full walkthrough) and outputs/tables/series.csv.

## Repo layout
- src/tonlesap.py (pipeline, sub-commands: inventory, wc_preview, fetch/fetchloop, analyze, series, compare,
  export, older), src/nisar_common.py (login, with_retry, granule_info, parse_gcov_name, GCOV_GRID).
- data/raw/nisar/<date>.npz (HH/HV/mask window, 20 m; V prefix = validated), data/interim/ (wc_grid.npz,
  wc_preview.npz, hh100/<date>.npy). Not for git.
- outputs/figures, outputs/tables, outputs/web (PNG per date, dates.json, bounds.json, series.csv), outputs/logs.
- Earlier UAE / Al Ain work (code, outputs, full decisions log) archived OUTSIDE the repo: C:\NASA_archive_uae\.

## Environment
- Windows, PowerShell, folder C:\NASA, Python 3.13. Packages: requirements.txt.
- Earthdata login is saved in C:\Users\USER\_netrc (earthaccess persist). Do NOT print or log credentials.
- Home router DNS is flaky (getaddrinfo failed): with_retry + caching; network commands need the sandbox disabled.
  If it recurs badly: set Windows DNS to 1.1.1.1 / 8.8.8.8.
- NISAR is read over HTTPS with earthaccess.open (S3 only inside AWS us-west-2). Never download whole ~3 GB files;
  read windows. HTTPS reads can hang without timeout -> use `fetchloop` (watchdog restarts after 240 s idle).
- GCOV path science/LSAR/GCOV/grids/frequencyA/{HHHH,HVHV,mask,xCoordinates,yCoordinates,projection};
  mask 0/255 = invalid. Average in LINEAR power, then dB.
- GCOV naming: NISAR_L2_PR_GCOV_<cycle>_<track>_<A|D>_<frame>_<mode>_<pols>_<M|A>_<start>_<end>_<CRID>_<acc>_<F|P>_...
  mode 2005 = 20 MHz (~20 m), 4005 = 40 MHz (~10 m); DHDH = HH+HV, SHSH = HH only. Use ONE mode per series.
- Planetary Computer (pystac-client + planetary_computer + odc-stac): esa-worldcover, sentinel-2-l2a, sentinel-1-rtc.

## Working agreement
Claude Code owns the code and runs it on real data. Bring figures and numbers back to the chat for interpretation
and storytelling. Log decisions below.

## Decisions log (Tonle Sap)
- 2026-11-14: Side exploration started (UAE was main line then). Window moved after WorldCover preview from
  103.5-103.9E/12.9-13.3N (no rice) to 103.55-103.95E/13.0-13.4N (lake + forest/wetland ring + rice belt + Siem Reap
  edge). Inventory: best = GCOV PROV 083 A frame 008 mode 2005 DHDH full, 10 dates Jun 19 - Oct 17 (+ validated
  Oct 29). Also 076D/082 (4005 partial 10 dates; 2005 split DHDH/SHSH), 148D/082.
  3-date go/no-go (Jun 19, Aug 30, Oct 17): open water (HH < -15 dB) 614 -> 807 -> 1055 km2. Artifacts: no scene
  jump (built HV +-0.25 dB, water HV +-0.4 dB); row profiles < 1 dB; column profiles 4 bins > 1 dB, probably the
  N-S flood front on the west shore, not checked. VERDICT: GO.
- 2026-11-14: DECISION (user): TONLE SAP IS OUR STORY. Thresholds: open water HH < -15 dB; flooded forest
  HH >= -3 / >= -5 dB on WorldCover tree. Cells 100 m (5x5 of 20 m, linear power), class = WorldCover >= 50% of cell.
- 2026-11-15: Series (11 dates; validated Oct 29 on the same grid). Open water 611 (Jun 19) -> 1050 km2 (Oct 17).
  Flooded forest (>= -5) 39 -> peak 311 km2 Aug 30 -> 139 Oct 17; (>= -3) peak 57 km2 Sep 11. Tree HH-HV
  4.9 -> 7.7 dB (Sep 23). Wetland HH -8.2 -> -17.7 dB. Interpretation: flood goes under the trees first (bright,
  Aug-Sep), then over the low canopy/shrubs (dark, Oct). Validated Oct 29 agrees with Oct 17.
- 2026-11-15: "Only NISAR" check, Aug 30: S2 least-cloudy Aug 27 = 52% cloud in window (Aug 30 100%). S1C RTC same
  day (asc, 74% of window): ring picked from NISAR Aug 18 vs other trees = NISAR HH +2.3 dB, S1 VV +0.8, VH +1.0 dB.
  Claim "NISAR sees it much more strongly", NOT "only NISAR".
- 2026-11-15: Older data: NO NISAR GCOV (BETA/PROVISIONAL/V1) over the window or anywhere in Cambodia before
  2026-06-19 (searched from 2025-08-01). No 2025 vs 2026 comparison with NISAR.
- 2026-11-15: Repo cleaned: UAE/Al Ain code + outputs + old CLAUDE.md moved to C:\NASA_archive_uae\ (not deleted).
  Standard layout src/ data/ outputs/ docs/; shared helpers split from nisar_check.py into src/nisar_common.py.
  Verified series/export/analyze regenerate identical numbers from the new paths.
- 2026-11-15: Checked for 083A/008 passes after Oct 29 (expected Nov 10): NONE published yet (provisional or
  validated). Only new granule over the window: validated 148D/082 2005 SHSH (HH only) Nov 3 - other track, not
  used. Series unchanged (last date = validated Oct 29). To add Nov 10 later: run `python src/tonlesap.py fetchloop`
  (raise its `expected` count to 12), then series and export.
- 2026-11-15: Story map built in site/ (plain HTML/CSS/JS; MapLibre 4.7.1 + Chart.js 4.4.4 from CDN; OSM raster
  basemap - CARTO raster tiles now show "API KEY REQUIRED", so not used). 7 chapters from the user's text; data copied
  by `python src/tonlesap.py site`. Swipe compare = two synced maps, top one clipped. Two charts (open water; flooded
  forest at both thresholds) instead of one dual-axis chart. Deep links #ch1..#ch7 or ?ch=N (&noscroll=1 = test hook).
  GitHub Pages via .github/workflows/pages.yml (Pages source must be "GitHub Actions"). CONFIG.repoUrl in
  site/main.js is a placeholder until the repo exists. Chapter 2 sources are the user's; not verified by Claude.
- 2026-11-15: Site v2 for public / decision-makers (repo name tonle-sap-invisible-flood). 9 chapters (user's text),
  technical details in <details> expanders. Story mode = chapters drive the map, only play + date shown; reaching
  "Explore the data" (or its button) unlocks slider, opacity, swipe, click-to-inspect. Chapter 4 plays Jun 19 ->
  Oct 17 once; chapter 5 loops. Click-to-inspect reads the per-date PNGs in the browser: export now writes them with
  PIL using an exact encoding v = round(255*(dB+22)/24) (decode error <= 0.05 dB, verified), plus worldcover100.png
  (majority class per 100 m cell, code in red channel). Repo links auto-detected on <user>.github.io, else
  CONFIG.githubUser placeholder. MIT LICENSE with [TEAM NAME] placeholder. Tested in headless Edge at 1440 px and
  500 px (Edge minimum window width): chapter states, swipe, explore unlock, inspect popup OK; real scrolling could
  not be screenshotted headless (renders blank) -> check by hand.
- 2026-11-15: Site v3 (user: "hard to understand, make it beautiful, impress judges"; chose flood map + radar toggle,
  cinematic dark, rewrite with sourced facts). Story reframed: the flood is the lake's heartbeat (good); the threat
  is a WEAKER flood. Verified facts added with sources: river reversal + lake ~2,500-3,000 -> up to 13,000 km2,
  3.6 -> >10 m (MRC Flood Report 2005); "bigger flood = more fish" (Zeb Hogan, Nat Geo 2020); reverse flow -56.5%
  1960s->2010s (Ji et al., HESS 2022); late reversal 2019-2020 (MRC). New `webcolor` step: flood_<d>.png
  (classes match series.csv areas exactly), radar_<d>.png (R=HH -17.9..-2.7, G=HV -24.2..-9.1, B=HH-HV 3.9..9.9 dB,
  2-98 pct of land over all dates), timecolor.png (R=Aug30 G=Jun19 B=Oct17 HH, -16..-1 dB), duration.png
  (wet count of 10 provisional images, 3x3 median), classes.csv, layers.json. Duration stats: 570 km2 permanent
  water, 964 km2 wet at least once, 452 of 540 km2 tree cells wet at least once (84%). compare() also writes
  clean panels cmp_s2/s1/nisar (site step shrinks them; same numbers as before). Chart colours #3a8ee6 / #199e70
  pass the dataviz validator on dark. 12 chapters + explore; full-screen map with scroll-driven fly-to.
  Testing: headless Edge (no GPU) hangs on the hero screen (big blurred text/box shadows) and is flaky; checked
  hero, explore + inspect and phone (400 px) in a real Edge window instead; chapters 1, 4-6, 8 headless. Shadows
  reduced and card backdrop blur removed for slow phones. Test hooks: ?ch=N|top|explore, &solo=1, &noplay=1,
  &noscroll=1, &noshadow=1, &inspect=lng,lat.
- 2026-11-15: Site v3.1 (user: "too zoomed out, should be clearer, explore confusing"). User saw the map at region
  zoom (~5), probably a first layout at another window size with no refit. Fixes: minZoom 8.5, maxBounds = image
  +-3x width / 2x height (tighter maxBounds clamped the side-padded framing!), refit on window resize and after
  map load, padding capped at 65% of the screen, hero frames the image in the right half, explore framing measures
  the card and dock. Sharper DISPLAY layers at 40 m (webcolor): 5x5 boxcar at 20 m sampled every 40 m ->
  flood40_<d>.png, radar40_/hh40_<d>.webp, timecolor40.webp, duration40.png, corners in layers.json display40.
  Numbers + click-to-inspect still use the 100 m data. site step no longer copies the 100 m display layers.
  Explore dock simplified: big play, labelled Date/Show rows, "Compare two dates", "Reset view", one-line hint;
  zoom buttons hidden during the story. Verified hero framing in real Edge; explore framing not re-verified
  (headless hangs; real-window capture would grab the user's screen while they work).
- 2026-11-15: User: "plain square looks weird". 40 m display layers now feathered (feather(): alpha smoothstep over
  the outer 9% of the image) and default layer opacity 0.82. Data layers for numbers/inspect unchanged.
