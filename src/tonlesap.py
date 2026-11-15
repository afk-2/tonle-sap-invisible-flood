#!/usr/bin/env python3
"""
tonlesap.py - does NISAR show the Tonle Sap flood pulse spreading into the flooded forest (Jun-Nov 2026)?

Run from the repo root, steps in this order (each step caches its inputs; see docs/PROCESS.md):

    python src/tonlesap.py inventory   -> outputs/tables/inventory.csv       which NISAR GCOV granules cover the window
    python src/tonlesap.py wc_preview  -> outputs/figures/wc_preview.png     WorldCover map used to place the window
    python src/tonlesap.py fetchloop   -> data/raw/nisar/<date>.npz          HH/HV/mask windows, all dates (streamed,
                                                                             auto-restarts if a download hangs)
    python src/tonlesap.py analyze     -> 3-date first look: hh_3dates, context_wc_s2, change_flooded_forest
                                          (also caches WorldCover + Sentinel-2 on the radar grid)
    python src/tonlesap.py series      -> outputs/tables/series.csv + figures/areas_over_time.png (all dates, 100 m)
    python src/tonlesap.py compare     -> figures/compare_s2_s1_nisar.png    Sentinel-2 / Sentinel-1 / NISAR, Aug 30
    python src/tonlesap.py export      -> outputs/web/                       PNG per date, dates.json, bounds.json
    python src/tonlesap.py site        -> site/data/, site/img/               copy the web export into the story map
    python src/tonlesap.py older      -> outputs/tables/older_inventory.csv any NISAR before Jun 17 2026? (none)
"""
import csv
import datetime as dt
import json
import os
import sys
import time

import numpy as np

from nisar_common import GCOV_GRID, login, with_retry, parse_gcov_name, granule_info

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # repo root (works from any folder)
CACHE = os.path.join(ROOT, "data", "raw", "nisar")       # HH/HV/mask windows, one .npz per date (20 m)
INTERIM = os.path.join(ROOT, "data", "interim")          # WorldCover on the radar grid, 100 m HH per date
FIG = os.path.join(ROOT, "outputs", "figures")
TAB = os.path.join(ROOT, "outputs", "tables")
for _d in (CACHE, INTERIM, FIG, TAB):
    os.makedirs(_d, exist_ok=True)
BBOX0 = (103.5, 12.9, 103.9, 13.3)           # first guess (used for inventory): almost no rice fields in it
BBOX = (103.55, 13.0, 103.95, 13.4)          # shifted NE after WorldCover preview: lake + forest ring + rice belt
START = "2026-06-17"
COLLECTIONS = ["NISAR_L2_GCOV_PROVISIONAL_V1", "NISAR_L2_GCOV_V1"]


def inventory():
    ea = login()
    today = dt.date.today().isoformat()
    rows = []
    for sn in COLLECTIONS:
        res = with_retry(f"search {sn}", ea.search_data, short_name=sn, bounding_box=BBOX, temporal=(START, today))
        for g in res:
            name, begin = granule_info(g)
            if "QA" in name:
                continue
            r = dict(collection=sn, granule=name, begin=begin)
            r.update(parse_gcov_name(name))
            rows.append(r)
    # dedupe: same track/frame/mode/date -> keep latest name
    best = {}
    for r in rows:
        k = (r["collection"], r.get("track"), r.get("direction"), r.get("frame"), r.get("mode"), r["begin"][:10])
        if k not in best or r["granule"] > best[k]["granule"]:
            best[k] = r
    rows = sorted(best.values(), key=lambda r: (r.get("track"), r.get("frame"), r.get("mode"), r["begin"]))
    with open(f"{TAB}/inventory.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["collection", "granule", "begin", "cycle", "track", "direction", "frame",
                                          "mode", "pols", "source", "coverage"], extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    series = {}
    for r in rows:
        k = (r["collection"][-14:], r.get("track"), r.get("direction"), r.get("frame"), r.get("mode"),
             r.get("pols"), r.get("coverage"))
        series.setdefault(k, []).append(r["begin"][:10])
    for k, d in sorted(series.items(), key=lambda t: -len(t[1])):
        print(f"{k}: {len(d)} dates: {' '.join(x[5:] for x in sorted(d))}")
    print(f"{len(rows)} granules -> {TAB}/inventory.csv")


TRACK_KEY = "_083_A_008_2005_DHDH_"
DATES = ["20260619", "20260830", "20261017"]       # low water, mid, latest (near peak)
EPSG = 32648


def retry(what, fn, tries=6):
    for i in range(1, tries + 1):
        try:
            return fn()
        except Exception as e:
            print(f"  {what}: attempt {i}/{tries} failed: {type(e).__name__}: {str(e)[:100]}", flush=True)
            if i == tries:
                raise
            time.sleep(min(5 * i, 30))


def wc_preview():
    """WorldCover 2021 at 100 m over a wider box to place the 40 x 40 km window."""
    import planetary_computer
    import pystac_client
    import odc.stac
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap, BoundaryNorm
    cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1",
                                    modifier=planetary_computer.sign_inplace)
    box = (103.2, 12.6, 104.2, 13.6)
    items = retry("WC search", lambda: list(cat.search(collections=["esa-worldcover"], bbox=box).items()))
    items = [i for i in items if "2021" in str(i.datetime or i.properties.get("start_datetime"))] or items
    ds = retry("WC load", lambda: odc.stac.load(items, bands=["map"], bbox=box, crs="EPSG:4326",
                                                resolution=0.001, resampling="mode").compute())
    wc = ds["map"].isel(time=0).values
    np.savez_compressed(f"{INTERIM}/wc_preview.npz", wc=wc, lon=ds.longitude.values, lat=ds.latitude.values)
    plot_wc(wc, box, f"{FIG}/wc_preview.png", BBOX)


def load_wc_on(x, y):
    """WorldCover 2021 resampled (mode) onto the radar window grid (pixel centres x, y in EPSG)."""
    import planetary_computer
    import pystac_client
    import odc.stac
    from affine import Affine
    from odc.geo.geobox import GeoBox
    cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1",
                                    modifier=planetary_computer.sign_inplace)
    dx, dy = x[1] - x[0], y[1] - y[0]
    gb = GeoBox((y.size, x.size), Affine(dx, 0, x[0] - dx / 2, 0, dy, y[0] - dy / 2), f"EPSG:{EPSG}")
    items = retry("WC search", lambda: list(cat.search(collections=["esa-worldcover"], bbox=BBOX).items()))
    items = [i for i in items if "2021" in str(i.datetime or i.properties.get("start_datetime"))] or items
    wc = retry("WC load", lambda: odc.stac.load(items, bands=["map"], geobox=gb, resampling="mode").compute())
    wc = wc["map"].isel(time=0).values
    # Sentinel-2 dry season (Jan-Mar 2026) true colour at 4x coarser pixels for the context map
    gb4 = GeoBox((y.size // 4, x.size // 4), Affine(dx * 4, 0, x[0] - dx / 2, 0, dy * 4, y[0] - dy / 2),
                 f"EPSG:{EPSG}")
    s2 = retry("S2 search", lambda: list(cat.search(collections=["sentinel-2-l2a"], bbox=BBOX,
                                                     datetime="2026-01-01/2026-03-31",
                                                     query={"eo:cloud_cover": {"lt": 5}}).items()))
    ds = retry("S2 load", lambda: odc.stac.load(s2, bands=["B02", "B03", "B04"], geobox=gb4, groupby="solar_day",
                                                chunks={}, resampling="average").median("time").compute())
    rgb = np.dstack([ds[b].values for b in ("B04", "B03", "B02")]).astype("float32")
    return wc, rgb, len({i.datetime.date() for i in s2})


WC_CLS = [10, 20, 30, 40, 50, 60, 80, 90, 95]
WC_COL = ["#006400", "#ffbb22", "#ffff4c", "#f096ff", "#fa0000", "#b4b4b4", "#0064c8", "#0096a0", "#00cf75"]
WC_NAME = ["tree", "shrub", "grass", "crop", "built", "bare", "water", "herb. wetland", "mangrove"]


def wc_rgb(wc):
    lut = np.zeros((256, 3))
    for c, h in zip(WC_CLS, WC_COL):
        lut[c] = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    return lut[wc]


def plot_wc(wc, box, path, rect=None):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch, Rectangle
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.imshow(wc_rgb(wc), extent=(box[0], box[2], box[1], box[3]))
    if rect:
        ax.add_patch(Rectangle((rect[0], rect[1]), rect[2] - rect[0], rect[3] - rect[1], fill=False, ec="k", lw=2))
    ax.legend(handles=[Patch(color=c, label=n) for c, n in zip(WC_COL, WC_NAME)], loc="lower left", fontsize=8)
    ax.set_title("ESA WorldCover 2021")
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


# ---------------------------------------------------------------------------------------- radar
def fetch():
    import h5py
    from pyproj import Transformer
    os.makedirs(CACHE, exist_ok=True)
    ea = login()
    gs = {}
    for sn, pre in ((COLLECTIONS[0], ""), (COLLECTIONS[1], "V")):     # validated files get prefix V
        res = with_retry("search", ea.search_data, short_name=sn, bounding_box=BBOX,
                         temporal=(START, dt.date.today().isoformat()))
        for g in res:
            name, begin = granule_info(g)
            if TRACK_KEY in name and "QA" not in name:
                d = pre + begin[:10].replace("-", "")
                if d not in gs or name > granule_info(gs[d])[0]:
                    gs[d] = g
    DATES[:] = sorted(gs, key=lambda k: (k.lstrip("V"), k))
    print("dates:", DATES)
    if os.path.exists(f"{CACHE}/{DATES[0]}.npz"):           # reference grid: snap every date to the same window
        with np.load(f"{CACHE}/{DATES[0]}.npz") as z:
            xr, yr = z["x"], z["y"]
    tr = Transformer.from_crs(4326, EPSG, always_xy=True).transform
    pts = [tr(x, y) for x in (BBOX[0], BBOX[2]) for y in (BBOX[1], BBOX[3])]
    xmin, xmax = min(p[0] for p in pts), max(p[0] for p in pts)
    ymin, ymax = min(p[1] for p in pts), max(p[1] for p in pts)
    if "xr" in locals():
        xmin, xmax, ymin, ymax = xr.min() - 1, xr.max() + 1, yr.min() - 1, yr.max() + 1
    names = {}
    for d in DATES:
        out = f"{CACHE}/{d}.npz"
        names[d] = granule_info(gs[d])[0]
        if os.path.exists(out):
            print(d, "cached"); continue
        urls = [u for u in gs[d].data_links() if u.endswith(".h5") and "QA_STATS" not in u]
        kw = {"block_size": 32 * 2**20, "cache_type": "readahead"}
        t0 = time.time()
        for attempt in range(1, 7):
            try:
                f = with_retry(f"open {d}", lambda: ea.open(urls, open_kwargs=kw)[0])
                grp = h5py.File(f, "r")[GCOV_GRID]
                epsg = int(grp["projection"][()])
                assert epsg == EPSG, f"projection {epsg} != {EPSG}"
                x, y = grp["xCoordinates"][:], grp["yCoordinates"][:]
                xi = np.nonzero((x >= xmin) & (x <= xmax))[0]
                yi = np.nonzero((y >= ymin) & (y <= ymax))[0]
                sl = (slice(yi.min(), yi.max() + 1), slice(xi.min(), xi.max() + 1))
                hh = grp["HHHH"][sl].astype("float32")
                hv = grp["HVHV"][sl].astype("float32")
                mk = grp["mask"][sl]
                break
            except Exception as e:
                print(f"  {d}: attempt {attempt} failed: {type(e).__name__}: {str(e)[:120]}", flush=True)
                if attempt == 6:
                    raise
                time.sleep(5 * attempt)
        if "xr" in locals() and not (np.array_equal(x[sl[1]], xr) and np.array_equal(y[sl[0]], yr)):
            print(f"  ! {d}: grid differs from reference (x0 {x[sl[1]][0]} vs {xr[0]}, n {sl[1].stop - sl[1].start})")
        np.savez_compressed(out, hh=hh, hv=hv, mask=mk, x=x[sl[1]], y=y[sl[0]])
        print(f"{d}: {hh.shape} px, {(time.time() - t0) / 60:.1f} min, valid {np.mean((mk > 0) & (mk < 255)):.2f}",
              flush=True)
    json.dump(names, open(f"{TAB}/granules.json", "w"), indent=1)


BRIGHT = -3.0            # dB, HH (5x5 smoothed): double bounce = flooded forest. Sensitivity at -5 and -1 dB
DARK = -15.0             # dB, HH (5x5 smoothed): open water (smooth surface reflects away from the radar)
SMOOTH = 5               # boxcar, pixels (20 m -> ~100 m) to beat speckle before thresholding


def boxcar(lin, valid, n):
    from scipy.ndimage import uniform_filter
    a = uniform_filter(np.where(valid, lin, 0).astype("float64"), n)
    w = uniform_filter(valid.astype("float64"), n)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(w > 0.5, a / w, np.nan)


def dbv(a):
    with np.errstate(invalid="ignore", divide="ignore"):
        return 10 * np.log10(a)


def analyze():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch
    from scipy.ndimage import median_filter
    zs = [dict(np.load(f"{CACHE}/{d}.npz")) for d in DATES]
    x, y = zs[0]["x"], zs[0]["y"]
    assert all(np.array_equal(z["x"], x) and np.array_equal(z["y"], y) for z in zs), "grids differ"
    px_km2 = abs((x[1] - x[0]) * (y[1] - y[0])) / 1e6
    valid = np.ones(zs[0]["hh"].shape, bool)
    for z in zs:
        valid &= (z["mask"] > 0) & (z["mask"] < 255) & (z["hh"] > 0) & (z["hv"] > 0) & np.isfinite(z["hh"])
    wcf = f"{INTERIM}/wc_grid.npz"
    if not os.path.exists(wcf):
        wc, rgb, n_s2 = load_wc_on(x, y)
        np.savez_compressed(wcf, wc=wc, rgb=rgb, n_s2=n_s2)
    w = np.load(wcf)
    wc, rgb = w["wc"], w["rgb"]
    tree = (wc == 10) & valid
    ring = np.isin(wc, [10, 20, 90]) & valid           # tree + shrub + herbaceous wetland
    names = {10: "tree", 20: "shrub", 30: "grass", 40: "crop", 50: "built", 80: "water", 90: "herb. wetland"}
    nice = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in DATES]
    hh = [dbv(np.where(valid, z["hh"], np.nan)) for z in zs]
    hv = [dbv(np.where(valid, z["hv"], np.nan)) for z in zs]
    hhs = [dbv(boxcar(z["hh"], valid, SMOOTH)) for z in zs]
    hvs = [dbv(boxcar(z["hv"], valid, SMOOTH)) for z in zs]
    res = dict(dates=nice, pixel_m=float(abs(x[1] - x[0])), window_px=list(valid.shape),
               valid_frac=float(valid.mean()), wc_km2={names.get(k, str(k)): round(float(((wc == k) & valid).sum()
                                                                                            * px_km2), 1)
                                                       for k in np.unique(wc) if ((wc == k) & valid).sum()})

    # ---- per class medians per date (whole-scene jump check: built/crop should not all move together)
    res["median_HH_dB"], res["median_HV_dB"] = {}, {}
    for k, n in names.items():
        m = (wc == k) & valid
        if m.sum() > 1000:
            res["median_HH_dB"][n] = [round(float(dbv(np.nanmedian(10 ** (h[m] / 10)))), 2) for h in hh]
            res["median_HV_dB"][n] = [round(float(dbv(np.nanmedian(10 ** (h[m] / 10)))), 2) for h in hv]

    # ---- flooded forest (bright) and open water (dark) areas per date
    res["tree_km2"] = round(float(tree.sum() * px_km2), 1)
    res["bright_tree_km2"] = {f"{t:+.0f} dB": [round(float(((h >= t) & tree).sum() * px_km2), 1) for h in hhs]
                              for t in (BRIGHT - 2, BRIGHT, BRIGHT + 2)}
    res["bright_ring_km2"] = [round(float(((h >= BRIGHT) & ring).sum() * px_km2), 1) for h in hhs]
    res["dark_tree_km2"] = [round(float(((h < DARK) & tree).sum() * px_km2), 1) for h in hhs]
    res["dark_all_km2"] = [round(float(((h < DARK) & valid).sum() * px_km2), 1) for h in hhs]
    res["tree_HH_pct_dB"] = {p: [round(float(np.nanpercentile(h[tree], p)), 2) for h in hhs] for p in (10, 50, 90)}
    res["tree_HHminusHV_median_dB"] = [round(float(np.nanmedian((a - b)[tree])), 2) for a, b in zip(hhs, hvs)]

    # ---- stripes: profiles of the date-to-date HH difference along rows/columns, 400 m bins, detrended
    stripes = {}
    for i in (1, 2):
        dd = hhs[i] - hhs[0]
        for ax_, nm in ((1, "rows"), (0, "cols")):
            prof = np.nanmedian(dd, axis=ax_)
            nb = prof.size // 20
            b = np.nanmedian(prof[:nb * 20].reshape(nb, 20), axis=1)
            trend = median_filter(np.nan_to_num(b, nan=np.nanmedian(b)), 15, mode="nearest")
            dev = b - trend
            stripes[f"{nice[i]}-{nice[0]} {nm}"] = dict(max_abs_dev_dB=round(float(np.nanmax(np.abs(dev))), 2),
                                                        bins_over_1dB=int(np.sum(np.abs(dev) > 1)))
    res["stripes_400m_bins"] = stripes
    json.dump(res, open(f"{TAB}/summary.json", "w"), indent=1)
    print(json.dumps(res, indent=1))

    ext = (x[0] / 1e3, x[-1] / 1e3, y[-1] / 1e3, y[0] / 1e3)
    # ---- 1. three-panel HH (native 20 m)
    fig, axs = plt.subplots(1, 3, figsize=(18, 6.6), sharex=True, sharey=True)
    for a, h, n in zip(axs, hh, nice):
        im = a.imshow(h, cmap="gray", vmin=-22, vmax=2, extent=ext, interpolation="nearest")
        a.set_title(f"HH {n}")
    for a in axs:
        a.set_xlabel("UTM 48N x (km)")
    axs[0].set_ylabel("y (km)")
    fig.colorbar(im, ax=axs, shrink=0.8, label="HH (dB), native 20 m")
    fig.suptitle("NISAR GCOV 083A/008 mode 2005 - bright = double bounce (flooded trees, buildings), dark = open water")
    fig.savefig(f"{FIG}/hh_3dates.png", dpi=110); plt.close(fig)

    # ---- 2. context: WorldCover + Sentinel-2
    fig, axs = plt.subplots(1, 2, figsize=(14, 7), sharex=True, sharey=True)
    axs[0].imshow(wc_rgb(wc), extent=ext, interpolation="nearest")
    axs[0].legend(handles=[Patch(color=c, label=n) for c, n in zip(WC_COL, WC_NAME)], loc="lower left", fontsize=8)
    axs[0].set_title("ESA WorldCover 2021")
    lo, hi = np.nanpercentile(rgb, 2), np.nanpercentile(rgb, 98)
    axs[1].imshow(np.clip((rgb - lo) / (hi - lo), 0, 1), extent=ext)
    axs[1].set_title(f"Sentinel-2 Jan-Mar 2026 median ({int(w['n_s2'])} dates, dry season)")
    fig.tight_layout(); fig.savefig(f"{FIG}/context_wc_s2.png", dpi=110); plt.close(fig)

    # ---- 3. change map + flooded-forest growth + tree histograms
    fig, axs = plt.subplots(1, 3, figsize=(20, 6.8))
    d = hhs[2] - hhs[0]
    im = axs[0].imshow(d, cmap="RdBu_r", vmin=-8, vmax=8, extent=ext, interpolation="nearest")
    fig.colorbar(im, ax=axs[0], shrink=0.8, label="dB")
    axs[0].set_title(f"HH change {nice[2]} minus {nice[0]} (100 m)")
    cls = np.zeros(valid.shape, np.uint8)
    cls[tree] = 1
    cls[tree & (hhs[0] >= BRIGHT)] = 2
    cls[tree & (hhs[0] < BRIGHT) & (hhs[1] >= BRIGHT)] = 3
    cls[tree & (hhs[0] < BRIGHT) & (hhs[1] < BRIGHT) & (hhs[2] >= BRIGHT)] = 4
    cls[(hhs[2] < DARK) & valid] = 5
    cols = ["white", "#c8e6c8", "#1b5e20", "#ff9800", "#d50000", "#1565c0"]
    axs[1].imshow(cls, cmap=ListedColormap(cols), vmin=0, vmax=5, extent=ext, interpolation="nearest")
    axs[1].legend(handles=[Patch(color=c, label=l) for c, l in zip(cols[1:], [
        "tree, not bright", f"bright already {nice[0]}", f"newly bright by {nice[1]}",
        f"newly bright by {nice[2]}", f"open water {nice[2]} (HH < {DARK:.0f} dB)"])], loc="lower left", fontsize=8)
    axs[1].set_title(f"Flooded forest = tree pixels with HH >= {BRIGHT:.0f} dB")
    for h, n in zip(hhs, nice):
        axs[2].hist(h[tree], bins=np.arange(-25, 8, 0.25), histtype="step", lw=1.5, label=n, density=True)
    axs[2].axvline(BRIGHT, color="k", ls="--"); axs[2].axvline(DARK, color="k", ls=":")
    axs[2].set_xlabel("HH (dB, 100 m)"); axs[2].set_title("HH of WorldCover tree pixels"); axs[2].legend()
    fig.tight_layout(); fig.savefig(f"{FIG}/change_flooded_forest.png", dpi=110); plt.close(fig)
    print("figures in", FIG)


# ---------------------------------------------------------------------------------------- full time series
BLK = 5                                   # 5 x 5 pixels of 20 m = 100 m
H100 = os.path.join(INTERIM, "hh100")
WEB = os.path.join(ROOT, "outputs", "web")
COL = dict(water="#2a78d6", t3="#eb6834", t5="#1baf7a", ink="#0b0b0b", ink2="#52514e", grid="#e4e3df")


def cached_dates():
    import glob
    ds = [os.path.basename(p)[:-4] for p in glob.glob(f"{CACHE}/*.npz")]
    return sorted(ds, key=lambda k: (k.lstrip("V"), k))


def nice(d):
    v = d.lstrip("V")
    return f"{v[:4]}-{v[4:6]}-{v[6:]}" + (" (validated)" if d.startswith("V") else "")


def align(z, xr, yr):
    """Put a date's arrays on the reference window grid (same 20 m posting assumed, integer shift)."""
    x, y = z["x"], z["y"]
    if np.array_equal(x, xr) and np.array_equal(y, yr):
        return z["hh"], z["hv"], z["mask"]
    dx, dy = (x[0] - xr[0]) / (xr[1] - xr[0]), (y[0] - yr[0]) / (yr[1] - yr[0])
    assert abs(dx - round(dx)) < 0.01 and abs(dy - round(dy)) < 0.01, f"sub-pixel grid shift {dx} {dy}"
    out = []
    for a, fill in ((z["hh"], np.nan), (z["hv"], np.nan), (z["mask"], 0)):
        b = np.full((yr.size, xr.size), fill, a.dtype)
        r0, c0 = int(round(dy)), int(round(dx))
        rs, cs = max(0, r0), max(0, c0)
        re, ce = min(yr.size, r0 + a.shape[0]), min(xr.size, c0 + a.shape[1])
        b[rs:re, cs:ce] = a[rs - r0:re - r0, cs - c0:ce - c0]
        out.append(b)
    print(f"  aligned with shift rows {dy:+.0f} cols {dx:+.0f}")
    return out


def blockmean(a, n=BLK):
    h, w = a.shape[0] // n * n, a.shape[1] // n * n
    return a[:h, :w].reshape(h // n, n, w // n, n).mean(axis=(1, 3))


def series():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    os.makedirs(H100, exist_ok=True)
    ds = cached_dates()
    with np.load(f"{CACHE}/{ds[0]}.npz") as z:
        xr, yr = z["x"], z["y"]
    wc = np.load(f"{INTERIM}/wc_grid.npz")["wc"]
    frac = {k: blockmean((wc == k).astype("float32")) for k in (10, 40, 90)}
    tree, crop, wet = frac[10] >= 0.5, frac[40] >= 0.5, frac[90] >= 0.5
    cell_km2 = (BLK * abs(xr[1] - xr[0])) ** 2 / 1e6
    rows = []
    for d in ds:
        with np.load(f"{CACHE}/{d}.npz") as z:
            hh, hv, mk = align(dict(z), xr, yr)
        ok = (mk > 0) & (mk < 255) & (hh > 0) & (hv > 0)
        n = blockmean(ok.astype("float32"))
        with np.errstate(invalid="ignore", divide="ignore"):
            H = np.where(n >= 0.5, blockmean(np.where(ok, hh, 0)) / n, np.nan)
            V = np.where(n >= 0.5, blockmean(np.where(ok, hv, 0)) / n, np.nan)
        Hd, Vd = dbv(H), dbv(V)
        np.save(f"{H100}/{d}.npy", Hd.astype("float32"))
        v = np.isfinite(Hd)
        rows.append(dict(
            date=nice(d)[:10], validated=d.startswith("V"),
            valid_km2=round(float(v.sum() * cell_km2), 1),
            open_water_km2=round(float((Hd < DARK).sum() * cell_km2), 1),
            flooded_forest_m3dB_km2=round(float(((Hd >= -3) & tree).sum() * cell_km2), 1),
            flooded_forest_m5dB_km2=round(float(((Hd >= -5) & tree).sum() * cell_km2), 1),
            tree_HHminusHV_dB=round(float(np.nanmedian((Hd - Vd)[tree])), 2),
            tree_HH_dB=round(float(np.nanmedian(Hd[tree])), 2),
            wetland_HH_dB=round(float(np.nanmedian(Hd[wet])), 2),
            crop_HH_dB=round(float(np.nanmedian(Hd[crop])), 2)))
        print(rows[-1], flush=True)
    meta = dict(tree_km2=round(float(tree.sum() * cell_km2), 1), crop_km2=round(float(crop.sum() * cell_km2), 1),
                wetland_km2=round(float(wet.sum() * cell_km2), 1), cell_m=BLK * 20,
                class_rule="WorldCover 2021 class covers >= 50% of the 100 m cell")
    json.dump(meta, open(f"{TAB}/series_meta.json", "w"), indent=1)
    print(meta)
    with open(f"{TAB}/series.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)

    # ---- chart: two panels (different scales -> no shared axis), provisional = filled, validated = hollow
    t = [dt.date.fromisoformat(r["date"]) for r in rows]
    prov = [not r["validated"] for r in rows]
    fig, axs = plt.subplots(1, 2, figsize=(13, 4.6))
    panels = [(axs[0], [("open_water_km2", COL["water"], f"open water (HH < {DARK:.0f} dB)")],
               "Open water in the window (km²)"),
              (axs[1], [("flooded_forest_m5dB_km2", COL["t5"], "HH ≥ −5 dB"),
                        ("flooded_forest_m3dB_km2", COL["t3"], "HH ≥ −3 dB")],
               f"Flooded forest: bright tree cells (km², of {meta['tree_km2']:.0f} km² trees)")]
    for ax, keys, title in panels:
        for k, c, lab in keys:
            yv = [r[k] for r in rows]
            tp = [a for a, p in zip(t, prov) if p]
            yp = [b for b, p in zip(yv, prov) if p]
            ax.plot(tp, yp, "-o", color=c, lw=2, ms=6, mec="white", mew=1.5, label=lab)
            for a, b, p in zip(t, yv, prov):
                if not p:
                    ax.plot([a], [b], "o", mfc="white", mec=c, mew=2, ms=7)
        ax.axvspan(dt.date(2026, 7, 26), dt.date(2026, 8, 17), color=COL["grid"], alpha=0.6, lw=0)
        ax.text(dt.date(2026, 8, 6), ax.get_ylim()[1] * 0.97, "no data\n(instrument gap)", ha="center",
                va="top", fontsize=8, color=COL["ink2"])
        ax.set_title(title, loc="left", fontsize=11, color=COL["ink"])
        ax.set_ylim(bottom=0)
        ax.grid(axis="y", color=COL["grid"], lw=0.8); ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.spines["left"].set_color(COL["ink2"]); ax.spines["bottom"].set_color(COL["ink2"])
        ax.tick_params(colors=COL["ink2"], labelsize=9)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        ax.set_xlim(dt.date(2026, 6, 12), dt.date(2026, 11, 8))
    axs[1].legend(frameon=False, fontsize=9, loc="upper left")
    fig.text(0.01, 0.01, "NISAR GCOV 083A/008, 100 m cells. Filled = provisional; hollow = validated (Oct 29, "
             "different processing version). Tree = WorldCover 2021.", fontsize=8, color=COL["ink2"])
    fig.tight_layout(rect=(0, 0.04, 1, 1)); fig.savefig(f"{FIG}/areas_over_time.png", dpi=130); plt.close(fig)
    print("saved", f"{FIG}/areas_over_time.png")


# ---------------------------------------------------------------------------------------- Sentinel-1 / -2 comparison
def compare(target="2026-08-30"):
    import planetary_computer
    import pystac_client
    import odc.stac
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from affine import Affine
    from odc.geo.geobox import GeoBox
    cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1",
                                    modifier=planetary_computer.sign_inplace)
    ds = [d for d in cached_dates() if not d.startswith("V")]
    nd = min(ds, key=lambda d: abs((dt.date.fromisoformat(nice(d)[:10]) - dt.date.fromisoformat(target)).days))
    tday = dt.date.fromisoformat(nice(nd)[:10])
    win = f"{tday - dt.timedelta(days=10)}/{tday + dt.timedelta(days=10)}"
    with np.load(f"{CACHE}/{ds[0]}.npz") as z:
        xr, yr = z["x"], z["y"]
    R = 40
    gb = GeoBox((yr.size * 20 // R, xr.size * 20 // R), Affine(R, 0, xr[0] - 10, 0, -R, yr[0] + 10), f"EPSG:{EPSG}")
    res = {"nisar_date": str(tday), "search_window": win}

    # Sentinel-1 RTC: the acquisition nearest the NISAR date
    s1 = retry("S1 search", lambda: list(cat.search(collections=["sentinel-1-rtc"], bbox=BBOX, datetime=win).items()))
    res["s1_items"] = len(s1)
    vv = None
    if s1:
        day = min({i.datetime.date() for i in s1}, key=lambda a: abs((a - tday).days))
        its = [i for i in s1 if i.datetime.date() == day]
        dss = retry("S1 load", lambda: odc.stac.load(its, bands=["vv", "vh"], geobox=gb, groupby="solar_day",
                                                     resampling="average").compute())
        vv, vh = dbv(dss["vv"].isel(time=0).values.astype("float32")), dbv(dss["vh"].isel(time=0).values)
        res.update(s1_date=str(day), s1_platform=sorted({i.properties.get("platform") for i in its}),
                   s1_orbit=sorted({i.properties.get("sat:orbit_state") for i in its}),
                   s1_valid_frac=round(float(np.isfinite(vv).mean()), 2))
    # Sentinel-2: least cloudy within +-10 days, cloud measured inside the window too (SCL 3, 8, 9, 10)
    s2 = retry("S2 search", lambda: list(cat.search(collections=["sentinel-2-l2a"], bbox=BBOX, datetime=win).items()))
    res["s2_items"] = len(s2)
    rgb = None
    if s2:
        best = None
        for day in sorted({i.datetime.date() for i in s2}):
            its = [i for i in s2 if i.datetime.date() == day]
            dss = retry("S2 load", lambda: odc.stac.load(its, bands=["B04", "B03", "B02", "SCL"], geobox=gb,
                                                         groupby="solar_day", resampling="nearest").compute())
            scl = dss["SCL"].isel(time=0).values
            have = scl > 0
            cloud = float(np.isin(scl, [3, 8, 9, 10])[have].mean()) if have.any() else 1.0
            tile = float(np.mean([i.properties.get("eo:cloud_cover", np.nan) for i in its]))
            print(f"  S2 {day}: window cloud {cloud:.0%}, tile cloud {tile:.0f}%, coverage {have.mean():.0%}")
            score = cloud + (1 - have.mean())
            if best is None or score < best[0]:
                best = (score, day, cloud, tile, have.mean(),
                        np.dstack([dss[b].isel(time=0).values for b in ("B04", "B03", "B02")]).astype("float32"))
        _, day, cloud, tile, cov, rgb = best
        res.update(s2_date=str(day), s2_window_cloud_pct=round(cloud * 100), s2_tile_cloud_pct=round(tile),
                   s2_window_coverage_pct=round(cov * 100))
    # NISAR HH at 40 m
    with np.load(f"{CACHE}/{nd}.npz") as z:
        hh, _, mk = align(dict(z), xr, yr)
    ok = (mk > 0) & (mk < 255) & (hh > 0)
    n = blockmean(ok.astype("float32"), 2)
    with np.errstate(invalid="ignore", divide="ignore"):
        H = dbv(np.where(n >= 0.5, blockmean(np.where(ok, hh, 0), 2) / n, np.nan))
    H = H[:gb.shape[0], :gb.shape[1]]

    # does the bright NISAR ring show in S1? ring = tree cells with NISAR HH >= -5 dB (100 m) on this date
    wc = np.load(f"{INTERIM}/wc_grid.npz")["wc"]
    tree40 = blockmean((wc == 10).astype("float32"), 2)[:gb.shape[0], :gb.shape[1]] >= 0.5
    ring = tree40 & (H >= -5)
    other = tree40 & (H < -5) & (H > DARK)
    res["ring_km2_40m"] = round(float(ring.sum() * 0.0016), 1)
    res["nisar_HH_ring_vs_other_tree_dB"] = [round(float(np.nanmedian(H[ring])), 2),
                                             round(float(np.nanmedian(H[other])), 2)]
    if vv is not None:
        v = vv[:gb.shape[0], :gb.shape[1]]
        res["s1_VV_ring_vs_other_tree_dB"] = [round(float(np.nanmedian(v[ring])), 2),
                                              round(float(np.nanmedian(v[other])), 2)]
        m = tree40 & np.isfinite(v) & np.isfinite(H)
        res["corr_tree_VV_vs_HH"] = round(float(np.corrcoef(v[m], H[m])[0, 1]), 2)
        # unbiased version: ring picked from the PREVIOUS NISAR date, so Aug 30 speckle cannot favour NISAR
        prev = ds[ds.index(nd) - 1]
        with np.load(f"{CACHE}/{prev}.npz") as z:
            hp, _, mp = align(dict(z), xr, yr)
        okp = (mp > 0) & (mp < 255) & (hp > 0)
        n_ = blockmean(okp.astype("float32"), 2)
        with np.errstate(invalid="ignore", divide="ignore"):
            Hp = dbv(np.where(n_ >= 0.5, blockmean(np.where(okp, hp, 0), 2) / n_, np.nan))[:gb.shape[0], :gb.shape[1]]
        r2, o2 = tree40 & (Hp >= -5), tree40 & (Hp < -5) & (Hp > DARK)
        both = np.isfinite(v)
        res[f"ring_from_{nice(prev)[:10]}"] = dict(
            km2=round(float(r2.sum() * 0.0016), 1),
            nisar_HH_ring_vs_other_dB=[round(float(np.nanmedian(H[r2 & both])), 2),
                                       round(float(np.nanmedian(H[o2 & both])), 2)],
            s1_VV_ring_vs_other_dB=[round(float(np.nanmedian(v[r2])), 2), round(float(np.nanmedian(v[o2])), 2)],
            s1_VH_ring_vs_other_dB=[round(float(np.nanmedian(vh[:gb.shape[0], :gb.shape[1]][r2])), 2),
                                    round(float(np.nanmedian(vh[:gb.shape[0], :gb.shape[1]][o2])), 2)])
    json.dump(res, open(f"{TAB}/compare_s1_s2.json", "w"), indent=1)
    print(json.dumps(res, indent=1))

    # clean panels for the web app (same 40 m grid, no axes): photo | Sentinel-1 VV | NISAR HH, same grey scale
    from PIL import Image
    os.makedirs(WEB, exist_ok=True)
    def grey(a):
        ok = np.isfinite(a)
        v = np.round(255 * np.clip((np.where(ok, a, -22) + 22) / 24, 0, 1)).astype(np.uint8)
        out = np.dstack([v, v, v, (ok * 255).astype(np.uint8)]); out[~ok] = 0
        return out
    if rgb is not None:
        lo, hi = np.nanpercentile(rgb, 2), np.nanpercentile(rgb, 98)
        Image.fromarray(np.round(255 * np.clip((np.nan_to_num(rgb) - lo) / (hi - lo), 0, 1)).astype(np.uint8),
                        "RGB").save(f"{WEB}/cmp_s2.png", optimize=True)
    if vv is not None:
        Image.fromarray(grey(vv[:gb.shape[0], :gb.shape[1]]), "RGBA").save(f"{WEB}/cmp_s1.png", optimize=True)
    Image.fromarray(grey(H), "RGBA").save(f"{WEB}/cmp_nisar.png", optimize=True)

    ext = (xr[0] / 1e3, xr[-1] / 1e3, yr[-1] / 1e3, yr[0] / 1e3)
    fig, axs = plt.subplots(1, 3, figsize=(19, 6.8), sharex=True, sharey=True)
    if rgb is not None:
        lo, hi = np.nanpercentile(rgb, 2), np.nanpercentile(rgb, 98)
        axs[0].imshow(np.clip((rgb - lo) / (hi - lo), 0, 1), extent=ext)
        axs[0].set_title(f"Sentinel-2 {res['s2_date']} (window cloud {res['s2_window_cloud_pct']}%)")
    else:
        axs[0].set_title("Sentinel-2: no image within ±10 days")
    if vv is not None:
        axs[1].imshow(vv, cmap="gray", vmin=-22, vmax=2, extent=ext, interpolation="nearest")
        axs[1].set_title(f"Sentinel-1 C-band VV {res['s1_date']} (dB, 40 m)")
    else:
        axs[1].set_title("Sentinel-1 RTC: no acquisition within ±10 days")
    im = axs[2].imshow(H, cmap="gray", vmin=-22, vmax=2, extent=ext, interpolation="nearest")
    axs[2].set_title(f"NISAR L-band HH {tday} (dB, 40 m)")
    fig.colorbar(im, ax=axs[1:], shrink=0.75, label="backscatter (dB), same scale")
    for a in axs:
        a.set_xlabel("UTM 48N x (km)")
    fig.savefig(f"{FIG}/compare_s2_s1_nisar.png", dpi=110, bbox_inches="tight"); plt.close(fig)
    print("saved", f"{FIG}/compare_s2_s1_nisar.png")


# ---------------------------------------------------------------------------------------- web export
def export():
    import shutil
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from pyproj import Transformer
    from PIL import Image
    os.makedirs(WEB, exist_ok=True)
    ds = cached_dates()
    with np.load(f"{CACHE}/{ds[0]}.npz") as z:
        xr, yr = z["x"], z["y"]
    vmin, vmax = -22.0, 2.0
    out = []
    for d in ds:
        H = np.load(f"{H100}/{d}.npy")
        # exact 8-bit encoding (the web page reads values back): v = round(255 * (dB - vmin) / (vmax - vmin))
        ok = np.isfinite(H)
        v = np.round(255 * np.clip((np.where(ok, H, vmin) - vmin) / (vmax - vmin), 0, 1)).astype(np.uint8)
        rgba = np.dstack([v, v, v, (ok * 255).astype(np.uint8)])
        rgba[~ok] = 0
        fn = f"hh_{d}.png"
        Image.fromarray(rgba, "RGBA").save(f"{WEB}/{fn}", optimize=True)
        out.append(dict(date=nice(d)[:10], file=fn, validated=d.startswith("V"),
                        note="validated product (different processing version)" if d.startswith("V") else ""))
    json.dump(dict(dates=out, colour_scale=dict(
        band="HH", unit="dB", min=vmin, max=vmax, colormap="gray", transparent="no data",
        decode="dB = min + R/255 * (max - min); R = 0 means <= min, R = 255 means >= max")),
        open(f"{WEB}/dates.json", "w"), indent=1)
    shutil.copy(f"{TAB}/series.csv", f"{WEB}/series.csv")
    H = np.load(f"{H100}/{ds[0]}.npy")
    # WorldCover 2021 at 100 m (majority of 5x5 cells of 20 m), class code in the red channel, for click-to-inspect
    wc = np.load(f"{INTERIM}/wc_grid.npz")["wc"]
    classes = np.array([10, 20, 30, 40, 50, 60, 80, 90, 95])
    counts = np.stack([blockmean((wc == k).astype("float32")) for k in classes])
    wc100 = classes[counts.argmax(0)].astype(np.uint8)
    wc100[counts.max(0) == 0] = 0
    wc100 = wc100[:H.shape[0], :H.shape[1]]
    Image.fromarray(np.dstack([wc100, wc100, wc100, np.full_like(wc100, 255)]), "RGBA").save(f"{WEB}/worldcover100.png")
    x0, y1 = xr[0] - 10, yr[0] + 10                       # outer pixel edges of the 100 m image
    x1, y0 = x0 + H.shape[1] * BLK * 20, y1 - H.shape[0] * BLK * 20
    tr = Transformer.from_crs(EPSG, 4326, always_xy=True).transform
    corners = [tr(x0, y1), tr(x1, y1), tr(x1, y0), tr(x0, y0)]
    json.dump(dict(epsg=EPSG, utm_bounds=dict(left=x0, bottom=y0, right=x1, top=y1),
                   pixel_m=BLK * 20, width=H.shape[1], height=H.shape[0],
                   maplibre_image_coordinates_lonlat=[[round(a, 6), round(b, 6)] for a, b in corners],
                   note="corners in order top-left, top-right, bottom-right, bottom-left (MapLibre image source). "
                        "Image is north-up in UTM 48N, so it is very slightly rotated in lon/lat."),
              open(f"{WEB}/bounds.json", "w"), indent=1)
    print("web export:", WEB, len(out), "PNGs")


FLOOD_COLORS = {          # class code: (name, RGB) - the web app's flood map
    1: ("open water", (47, 143, 232)),
    2: ("flooded forest", (62, 230, 160)),
    3: ("forest, no flood seen", (24, 92, 56)),
    4: ("fields, grass, wetland", (184, 160, 118)),
    5: ("built-up", (150, 150, 150)),
}


def feather(rgba, frac=0.09):
    """Soft edges for web display layers: alpha fades to 0 over the outer `frac` of the image (smoothstep),
    so the radar image blends into the basemap instead of ending in a hard square."""
    h, w = rgba.shape[:2]
    r = max(2, int(frac * min(h, w)))
    def ramp(n):
        t = np.clip(np.minimum(np.arange(n), np.arange(n)[::-1]) / r, 0, 1)
        return t * t * (3 - 2 * t)
    m = np.outer(ramp(h), ramp(w))
    out = rgba.copy()
    out[..., 3] = np.round(out[..., 3] * m).astype(np.uint8)
    return out


def webcolor():
    """Colour web layers per date (100 m): flood_<d>.png (meaning-coloured classes), radar_<d>.png
    (NISAR colour composite R=HH, G=HV, B=HH/HV ratio), plus duration.png (how many 12-day images each cell
    was under water: open water or flooded forest) and classes.csv (km2 per class per date)."""
    from PIL import Image
    os.makedirs(WEB, exist_ok=True)
    ds = cached_dates()
    with np.load(f"{CACHE}/{ds[0]}.npz") as z:
        xr, yr = z["x"], z["y"]
    wc = np.load(f"{INTERIM}/wc_grid.npz")["wc"]
    tree = blockmean((wc == 10).astype("float32")) >= 0.5
    built = blockmean((wc == 50).astype("float32")) >= 0.5
    cell_km2 = (BLK * 20) ** 2 / 1e6
    lut = np.zeros((6, 4), np.uint8)
    for k, (_, rgb) in FLOOD_COLORS.items():
        lut[k] = (*rgb, 255)
    rows, wet_count, n_prov, perm = [], None, 0, None
    from scipy.ndimage import uniform_filter
    R40, C40 = yr.size // 2, xr.size // 2                   # 40 m grid, same top-left corner as the 100 m grid
    tree40 = built40 = wet40 = perm40 = None
    hh40 = {}

    def st(a, lo, hi):
        return np.round(255 * np.clip((a - lo) / (hi - lo), 0, 1)).astype(np.uint8)
    for d in ds:
        with np.load(f"{CACHE}/{d}.npz") as z:
            hh, hv, mk = align(dict(z), xr, yr)
        ok = (mk > 0) & (mk < 255) & (hh > 0) & (hv > 0)
        n = blockmean(ok.astype("float32"))
        with np.errstate(invalid="ignore", divide="ignore"):
            HH = dbv(np.where(n >= 0.5, blockmean(np.where(ok, hh, 0)) / n, np.nan))
            HV = dbv(np.where(n >= 0.5, blockmean(np.where(ok, hv, 0)) / n, np.nan))
        np.save(f"{H100}/HV_{d}.npy", HV.astype("float32"))
        valid = np.isfinite(HH)
        cls = np.zeros(HH.shape, np.uint8)
        water = valid & (HH < DARK)
        ff = valid & tree & ~water & (HH >= -5)
        cls[valid] = 4
        cls[valid & tree] = 3
        cls[valid & built & ~tree] = 5
        cls[ff] = 2
        cls[water] = 1
        Image.fromarray(lut[cls], "RGBA").save(f"{WEB}/flood_{d}.png", optimize=True)
        # stretch = 2-98th percentile of land cells over all dates (fixed, so dates are comparable)
        rgb = np.dstack([st(HH, -17.9, -2.7), st(HV, -24.2, -9.1), st(HH - HV, 3.9, 9.9), (valid * 255).astype(np.uint8)])
        rgb[~valid] = 0
        Image.fromarray(rgb, "RGBA").save(f"{WEB}/radar_{d}.png", optimize=True)
        rows.append(dict(date=nice(d)[:10], validated=d.startswith("V"),
                         **{FLOOD_COLORS[k][0].replace(", ", "_").replace(" ", "_") + "_km2":
                            round(float((cls == k).sum() * cell_km2), 1) for k in FLOOD_COLORS}))
        if not d.startswith("V"):                       # duration over the 10 provisional dates
            w = (water | ff).astype(np.uint8)
            wet_count = w if wet_count is None else wet_count + w
            perm = water if perm is None else perm & water
            n_prov += 1
        # ---- sharper DISPLAY layers at 40 m: 5x5 boxcar at 20 m (same ~100 m speckle averaging), sampled every 40 m
        if tree40 is None:
            tf = uniform_filter((wc == 10).astype("float32"), 5)
            bf = uniform_filter((wc == 50).astype("float32"), 5)
            tree40 = blockmean(tf[:R40 * 2, :C40 * 2], 2) >= 0.5
            built40 = blockmean(bf[:R40 * 2, :C40 * 2], 2) >= 0.5
        sm = lambda a: blockmean(boxcar(a, ok, 5)[:R40 * 2, :C40 * 2], 2)
        with np.errstate(invalid="ignore", divide="ignore"):
            H4, V4 = dbv(sm(hh)), dbv(sm(hv))
        v4 = np.isfinite(H4) & (blockmean(ok[:R40 * 2, :C40 * 2].astype("float32"), 2) >= 0.5)
        H4[~v4] = np.nan; V4[~v4] = np.nan
        c4 = np.zeros(H4.shape, np.uint8)
        w4 = v4 & (H4 < DARK)
        c4[v4] = 4; c4[v4 & tree40] = 3; c4[v4 & built40 & ~tree40] = 5
        c4[v4 & tree40 & ~w4 & (H4 >= -5)] = 2; c4[w4] = 1
        Image.fromarray(feather(lut[c4]), "RGBA").save(f"{WEB}/flood40_{d}.png", optimize=True)
        r4 = np.dstack([st(H4, -17.9, -2.7), st(V4, -24.2, -9.1), st(H4 - V4, 3.9, 9.9), (v4 * 255).astype(np.uint8)])
        r4[~v4] = 0
        Image.fromarray(feather(r4), "RGBA").save(f"{WEB}/radar40_{d}.webp", quality=80, method=6)
        g4 = st(H4, -22, 2)
        Image.fromarray(feather(np.dstack([g4, g4, g4, (v4 * 255).astype(np.uint8)])), "RGBA").save(f"{WEB}/hh40_{d}.webp", quality=80, method=6)
        hh40[d] = H4
        if not d.startswith("V"):
            w = (w4 | (c4 == 2)).astype(np.uint8)
            wet40 = w if wet40 is None else wet40 + w
            perm40 = w4 if perm40 is None else perm40 & w4
    with open(f"{WEB}/classes.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]))
        wr.writeheader(); wr.writerows(rows)
    # time-colour radar image: R = Aug 30 HH, G = Jun 19 HH, B = Oct 17 HH (colour shows WHEN a place was bright)
    hh_of = {d: np.load(f"{H100}/{d}.npy") for d in ("20260619", "20260830", "20261017")}
    def st2(a):
        return np.round(255 * np.clip((a + 16) / 15, 0, 1)).astype(np.uint8)
    tc = np.dstack([st2(hh_of["20260830"]), st2(hh_of["20260619"]), st2(hh_of["20261017"]),
                    (np.isfinite(hh_of["20260619"]) * 255).astype(np.uint8)])
    Image.fromarray(tc, "RGBA").save(f"{WEB}/timecolor.png", optimize=True)
    # duration map: permanent lake = blue; 1..n wet images = light yellow -> deep teal; never wet = transparent
    from scipy.ndimage import median_filter
    wet_count = median_filter(wet_count, 3)                     # remove single-cell speckle
    seq =np.array([[255, 241, 168], [199, 233, 140], [127, 205, 120], [65, 182, 140], [29, 145, 160],
                    [34, 94, 168], [37, 52, 148], [8, 29, 88]], float)
    out = np.zeros(wet_count.shape + (4,), np.uint8)
    for k in range(1, n_prov):
        t = (k - 1) / max(1, n_prov - 2) * (len(seq) - 1)
        i0 = int(np.floor(t)); i1 = min(i0 + 1, len(seq) - 1)
        c = seq[i0] + (seq[i1] - seq[i0]) * (t - i0)
        out[(wet_count == k) & ~perm] = (*np.round(c).astype(int), 235)
    out[(wet_count == n_prov) & ~perm] = (*seq[-1].astype(int), 235)
    out[perm] = (47, 143, 232, 255)
    Image.fromarray(out, "RGBA").save(f"{WEB}/duration.png", optimize=True)

    # ---- 40 m display versions of the time-colour and duration layers + their exact corners
    tc4 = np.dstack([st2(hh40["20260830"]), st2(hh40["20260619"]), st2(hh40["20261017"]),
                     (np.isfinite(hh40["20260619"]) * 255).astype(np.uint8)])
    Image.fromarray(feather(tc4), "RGBA").save(f"{WEB}/timecolor40.webp", quality=82, method=6)
    wet40 = median_filter(wet40, 3)
    o4 = np.zeros(wet40.shape + (4,), np.uint8)
    for k in range(1, n_prov):
        t = (k - 1) / max(1, n_prov - 2) * (len(seq) - 1)
        i0 = int(np.floor(t)); i1 = min(i0 + 1, len(seq) - 1)
        c = seq[i0] + (seq[i1] - seq[i0]) * (t - i0)
        o4[(wet40 == k) & ~perm40] = (*np.round(c).astype(int), 235)
    o4[(wet40 == n_prov) & ~perm40] = (*seq[-1].astype(int), 235)
    o4[perm40] = (47, 143, 232, 255)
    Image.fromarray(feather(o4), "RGBA").save(f"{WEB}/duration40.png", optimize=True)
    from pyproj import Transformer
    tr = Transformer.from_crs(EPSG, 4326, always_xy=True).transform
    x0, y1 = xr[0] - 10, yr[0] + 10
    x1, y0 = x0 + C40 * 40, y1 - R40 * 40
    corners40 = [[round(a, 6), round(b, 6)] for a, b in (tr(x0, y1), tr(x1, y1), tr(x1, y0), tr(x0, y0))]
    stats = dict(n_images=n_prov, permanent_water_km2=round(float(perm.sum() * cell_km2), 1),
                 ever_wet_not_permanent_km2=round(float(((wet_count > 0) & ~perm).sum() * cell_km2), 1),
                 wet_ge_half_km2=round(float(((wet_count >= n_prov / 2) & ~perm).sum() * cell_km2), 1),
                 tree_ever_flooded_forest_km2=round(float((tree & (wet_count > 0) & ~perm).sum() * cell_km2), 1),
                 tree_km2=round(float(tree.sum() * cell_km2), 1),
                 classes={str(k): dict(name=v[0], rgb=v[1]) for k, v in FLOOD_COLORS.items()},
                 radar_rgb="R = HH (-17.9..-2.7 dB), G = HV (-24.2..-9.1 dB), B = HH-HV (3.9..9.9 dB)",
                 display40=dict(corners=corners40, width=int(C40), height=int(R40), pixel_m=40,
                                note="flood40_<date>.png, radar40_/hh40_<date>.webp, timecolor40.webp, duration40.png: "
                                     "5x5 boxcar at 20 m (~100 m averaging) sampled every 40 m, display only; "
                                     "numbers (series.csv, classes.csv, click-to-inspect) use the 100 m layers"))
    json.dump(stats, open(f"{WEB}/layers.json", "w"), indent=1)
    print(json.dumps(stats, indent=1))
    for r in rows:
        print(r)


def site():
    """Copy the web export + the comparison figure into site/ (the static story map)."""
    import glob
    import shutil
    dst, img = os.path.join(ROOT, "site", "data"), os.path.join(ROOT, "site", "img")
    os.makedirs(dst, exist_ok=True); os.makedirs(img, exist_ok=True)
    from PIL import Image
    skip = ("flood_", "radar_", "timecolor.png", "duration.png")    # 100 m display layers: the site uses the 40 m ones
    for old in glob.glob(os.path.join(dst, "*")):
        if os.path.basename(old).startswith(skip):
            os.remove(old)
    for f in glob.glob(os.path.join(WEB, "*")):
        name = os.path.basename(f)
        if name.startswith(skip):
            continue
        if name.startswith("cmp_"):                    # comparison panels: shrink for the web
            im = Image.open(f)
            im.thumbnail((720, 720))
            if name == "cmp_s2.png":
                im.convert("RGB").save(os.path.join(dst, "cmp_s2.jpg"), quality=85)
            else:
                im.save(os.path.join(dst, name), optimize=True)
            continue
        shutil.copy(f, dst)
    shutil.copy(f"{FIG}/compare_s2_s1_nisar.png", img)
    print("site data updated:", len(os.listdir(dst)), "files in", dst)


def older():
    """All NISAR L2 GCOV collections (provisional, validated, beta), 2025-08-01 .. 2026-06-16, over BBOX."""
    ea = login()
    cols = set(COLLECTIONS)
    for kw in ("NISAR GCOV", "NISAR"):
        try:
            for c in with_retry("collections", ea.search_datasets, keyword=kw, count=300):
                sn = c["umm"].get("ShortName", "")
                if "GCOV" in sn:
                    cols.add(sn)
        except Exception as e:
            print("collection search failed:", e)
    print("collections:", sorted(cols))
    rows = []
    for sn in sorted(cols):
        try:
            res = with_retry(f"search {sn}", ea.search_data, short_name=sn, bounding_box=BBOX,
                             temporal=("2025-08-01", "2026-06-16T23:59:59"))
        except Exception as e:
            print(f"  {sn}: {type(e).__name__}: {str(e)[:100]}")
            continue
        print(f"  {sn}: {len(res)} granules")
        for g in res:
            name, begin = granule_info(g)
            if "QA" in name:
                continue
            p = name.split("_")
            r = dict(collection=sn, date=begin[:10], granule=name, **parse_gcov_name(name))
            r["crid"] = p[13] if len(p) > 13 else ""
            rows.append(r)
    rows.sort(key=lambda r: (r["date"], r.get("track", ""), r["collection"]))
    with open(f"{TAB}/older_inventory.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "collection", "track", "direction", "frame", "mode", "pols",
                                          "coverage", "crid", "granule"], extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    for r in rows:
        print(f"{r['date']} {r['collection']:32s} {r.get('track')} {r.get('direction')} {r.get('frame')} "
              f"{r.get('mode')} {r.get('pols')} {r.get('coverage')} {r['crid']}")
    print(len(rows), "granules ->", f"{TAB}/older_inventory.csv")


def fetchloop(expected=11, stall=240):
    """Run fetch in a child process; restart it if no new date is cached for `stall` seconds (hung HTTPS read)."""
    import subprocess
    while len(cached_dates()) < expected:
        p = subprocess.Popen([sys.executable, "-u", __file__, "fetch"])
        n, last = len(cached_dates()), time.time()
        while p.poll() is None:
            time.sleep(10)
            if len(cached_dates()) > n:
                n, last = len(cached_dates()), time.time()
            elif time.time() - last > stall:
                print(f"watchdog: no progress for {stall} s -> restart", flush=True)
                p.kill(); p.wait()
                break
        if p.returncode not in (0, None, -9, 1) or (p.returncode == 0 and len(cached_dates()) < expected):
            print("fetch ended with", p.returncode, "and", len(cached_dates()), "dates", flush=True)
            if p.returncode == 0:
                break
    print("cached:", cached_dates(), flush=True)


if __name__ == "__main__":
    {"inventory": inventory, "wc_preview": wc_preview, "fetch": fetch, "fetchloop": fetchloop, "older": older,
     "analyze": analyze, "series": series, "site": site, "webcolor": webcolor,
     "compare": compare, "export": export}[sys.argv[1]]()
