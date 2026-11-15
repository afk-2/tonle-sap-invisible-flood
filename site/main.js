/* The Invisible Flood: Tonle Sap. Scrollytelling story map.
   A full-screen MapLibre map sits behind the story cards. Each .step sets the map (layer, date, camera, mode).
   At the end, "Explore the data" unlocks the dock (slider, layers, swipe, opacity) and click-to-inspect. */
"use strict";

const CONFIG = {
  repoName: "tonle-sap-invisible-flood",
  githubUser: "afk-2",                  // auto-detected when served from <user>.github.io
  branch: "main",
};

const COL = { water: "#3a8ee6", forest: "#199e70", ink: "#eef2f6", ink2: "#b6c0cc", ink3: "#7f8a98",
              grid: "rgba(255,255,255,0.08)", gap: "rgba(255,255,255,0.06)", gold: "#ffd166" };
const GAP = [Date.UTC(2026, 6, 26), Date.UTC(2026, 7, 17)];   // instrument gap, no images
const X_MIN = Date.UTC(2026, 5, 12), X_MAX = Date.UTC(2026, 10, 6);
const PLAY_MS = 1100;
const WATER_DB = -15, BRIGHT_DB = -5;
const WC_NAMES = { 10: "Tree cover", 20: "Shrubland", 30: "Grassland", 40: "Cropland (rice)", 50: "Built-up",
                   60: "Bare ground", 80: "Open water", 90: "Wetland", 95: "Mangroves" };
const LAYER_NAMES = { flood: "Flood map", radar: "NISAR radar colour", grey: "NISAR radar brightness",
                      timecolor: "Radar time-colour", duration: "Time under water" };
const DYNAMIC = new Set(["flood", "radar", "grey"]);
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const isMobile = () => window.matchMedia("(max-width: 860px)").matches;
const params = new URLSearchParams(location.search);

const BASEMAP = {
  version: 8,
  sources: {
    osm: { type: "raster", tileSize: 256, maxzoom: 19, tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
           attribution: "© <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors" },
  },
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#05080c" } },
    { id: "osm", type: "raster", source: "osm",
      paint: { "raster-saturation": -0.85, "raster-brightness-max": 0.42, "raster-contrast": 0.1 } },
  ],
};

const S = { dates: [], keys: [], rows: [], corners: null, scale: null, W: 0, H: 0, layers: null,
            layer: "flood", idx: 0, opacity: 0.82, compare: false, left: 0, right: 0, timer: null, divX: 0.5,
            step: null, exploring: false, pixels: null, wc: null };
const el = id => document.getElementById(id);
const fmt = (iso, long) => new Date(iso + "T00:00:00Z").toLocaleDateString("en-GB",
  { day: "numeric", month: "short", year: long ? "numeric" : undefined, timeZone: "UTC" });
const ts = iso => Date.parse(iso + "T00:00:00Z");
const indexOfDate = iso => Math.max(0, S.dates.findIndex(d => d.date === iso));

// ============================================================ data
async function loadData() {
  const get = (f, t) => fetch("data/" + f).then(r => { if (!r.ok) throw new Error(f); return r[t](); });
  const [dj, bj, csv, lj] = await Promise.all([get("dates.json", "json"), get("bounds.json", "json"),
                                              get("series.csv", "text"), get("layers.json", "json")]);
  S.dates = dj.dates;
  S.keys = S.dates.map(d => d.file.replace(/^hh_|\.png$/g, ""));
  S.scale = dj.colour_scale;
  S.corners = bj.maplibre_image_coordinates_lonlat;
  S.W = bj.width; S.H = bj.height;
  S.layers = lj;
  S.dispCorners = lj.display40.corners;
  const lines = csv.trim().split(/\r?\n/), head = lines[0].split(","), byDate = {};
  for (const ln of lines.slice(1)) { const v = ln.split(","), r = {}; head.forEach((h, i) => { r[h] = v[i]; }); byDate[r.date] = r; }
  S.rows = S.dates.map(d => byDate[d.date]);
}

// display layers are the sharp 40 m versions (numbers and click-to-inspect use the exact 100 m data)
function layerUrl(layer, i) {
  if (layer === "timecolor") return "data/timecolor40.webp";
  if (layer === "duration") return "data/duration40.png";
  if (layer === "flood") return `data/flood40_${S.keys[i]}.png`;
  if (layer === "radar") return `data/radar40_${S.keys[i]}.webp`;
  return `data/hh40_${S.keys[i]}.webp`;
}

function preloadLayer(layer) {
  if (!DYNAMIC.has(layer)) { new Image().src = layerUrl(layer, 0); return; }
  S.keys.forEach((_, i) => { new Image().src = layerUrl(layer, i); });
}

// ============================================================ map
let mapA, mapB, popup, popupChart;
const ready = {};
const bounds = () => {
  const lons = S.corners.map(c => c[0]), lats = S.corners.map(c => c[1]);
  return [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]];
};

// the camera can never leave the study area (about 1.5 window-widths around it) or zoom out to the region
function maxBounds() {
  // generous margins: framing the image beside a text card needs the camera to look well past its edges
  const [[w, s], [e, n]] = bounds(), dx = (e - w) * 3, dy = (n - s) * 2;
  return [[w - dx, s - dy], [e + dx, n + dy]];
}

function makeMap(id) {
  const m = new maplibregl.Map({
    container: id, style: BASEMAP, attributionControl: false, dragRotate: false, pitchWithRotate: false,
    bounds: bounds(), fitBoundsOptions: { padding: framePadding() }, fadeDuration: 0,
    maxBounds: maxBounds(), minZoom: 8.5, maxZoom: 15,
  });
  m.touchZoomRotate.disableRotation();
  ready[id] = new Promise(res => m.on("load", () => {
    m.addSource("ov", { type: "image", url: layerUrl("flood", 0), coordinates: S.dispCorners });
    m.addLayer({ id: "ov", type: "raster", source: "ov",
                 paint: { "raster-opacity": S.opacity, "raster-fade-duration": 0, "raster-resampling": "nearest" } });
    res(m);
  }));
  return m;
}

function setInteractive(on) {
  for (const m of [mapA, mapB]) {
    for (const h of ["dragPan", "scrollZoom", "boxZoom", "doubleClickZoom", "touchZoomRotate", "keyboard"]) {
      if (m[h]) on ? m[h].enable() : m[h].disable();
    }
    if (m.cooperativeGestures) on ? m.cooperativeGestures.enable() : m.cooperativeGestures.disable();
  }
  if (on) m_touchNoRotate();
}
function m_touchNoRotate() { mapA.touchZoomRotate.disableRotation(); mapB.touchZoomRotate.disableRotation(); }

async function showImage(mapId, layer, i) {
  const m = await ready[mapId];
  m.getSource("ov").updateImage({ url: layerUrl(layer, i), coordinates: S.dispCorners });
}

function setOpacity(v) {
  S.opacity = v;
  for (const id of ["mapA", "mapB"]) ready[id].then(m => m.setPaintProperty("ov", "raster-opacity", v));
}

function syncMaps(a, b) {
  let busy = false;
  const follow = (src, dst) => src.on("move", () => {
    if (busy) return;
    busy = true;
    dst.jumpTo({ center: src.getCenter(), zoom: src.getZoom(), bearing: src.getBearing(), pitch: src.getPitch(),
                 padding: src.getPadding() });
    busy = false;
  });
  follow(a, b); follow(b, a);
}

// leave room for the story card on the left (desktop) or the card at the bottom (mobile)
function framePadding() {
  const W = window.innerWidth, H = window.innerHeight;
  let p;
  if (S.exploring) {
    // keep the image clear of the explore card (left) and the control dock (bottom), measured live
    const dockH = el("dock").offsetHeight || 150;
    const card = document.querySelector("#explore .card");
    const cardR = card && !isMobile() ? Math.min(card.getBoundingClientRect().right, W * 0.45) : 0;
    p = isMobile() ? { top: 60, bottom: dockH + 16, left: 10, right: 10 }
                   : { top: 50, bottom: dockH + 30, left: cardR + 24, right: 60 };
  } else if (S.step && S.step.classList.contains("hero")) {
    p = isMobile() ? { top: 90, bottom: Math.round(H * 0.2), left: 10, right: 10 }
                   : { top: 50, bottom: 50, left: W * 0.5, right: 70 };
  } else p = isMobile() ? { top: 90, bottom: Math.round(H * 0.2), left: 10, right: 10 }
                        : { top: 60, bottom: 60, left: Math.min(560, W * 0.42), right: 80 };
  // never let the padding eat the map: keep at least 35% of the width and height for the image
  const sx = Math.min(1, (W * 0.65) / (p.left + p.right)), sy = Math.min(1, (H * 0.65) / (p.top + p.bottom));
  return { top: p.top * sy, bottom: p.bottom * sy, left: p.left * sx, right: p.right * sx };
}

let currentView = "full";
function flyToView(view, instant) {
  if (!mapA) return;
  currentView = view || "full";
  const opts = { duration: reducedMotion || instant ? 0 : 2200, essential: true };
  if (!view || view === "full") {
    mapA.fitBounds(bounds(), { ...opts, padding: framePadding() });
  } else {
    const [lng, lat, z] = view.split(",").map(Number);
    mapA.flyTo({ ...opts, center: [lng, lat], zoom: isMobile() ? z - 0.6 : z, padding: framePadding() });
  }
}

// ============================================================ legend + HUD + progress
function swatchRow(color, text) { return `<div class="row"><span class="sw" style="background:${color}"></span><span>${text}</span></div>`; }

function renderLegend() {
  const L = S.layer;
  let h = `<h4>${LAYER_NAMES[L]}</h4>`;
  if (L === "flood") {
    const c = S.layers.classes, rgb = k => `rgb(${c[k].rgb.join(",")})`;
    h += swatchRow(rgb(1), "Open water") + swatchRow(rgb(2), "Flooded forest <span class='lg-x'>(water under trees)</span>") +
         swatchRow(rgb(3), "Forest, no flood seen") + swatchRow(rgb(4), "Fields, grass, wetland") + swatchRow(rgb(5), "Town");
  } else if (L === "grey") {
    h += `<div class="ramp" style="background:linear-gradient(90deg,#000,#808080 55%,#fff)"></div>
          <div class="ramp-lab"><span>open water</span><span>land</span><span>flooded forest</span></div>`;
  } else if (L === "radar") {
    h += swatchRow("#14156e", "Deep blue: open water") + swatchRow("#e8e85a", "Yellow: forest &amp; tall plants") +
         swatchRow("#f3e7c8", "Pale: flooded forest (strong bounce)") + swatchRow("#6a4fd0", "Purple: wet rice fields") +
         `<div style="margin-top:4px;font-size:11px">red = HH · green = HV · blue = HH÷HV</div>`;
  } else if (L === "timecolor") {
    h += swatchRow("#e6a6d6", "Lit up Aug–Oct: hidden flood") + swatchRow("#b5b04a", "Under water by October") +
         swatchRow("#22c33a", "Under water by August") +
         swatchRow("#7a1f12", "Rice: bright late Aug") + swatchRow("#000", "Lake") + swatchRow("#f2f2f2", "Town");
  } else if (L === "duration") {
    h += `<div class="ramp" style="background:linear-gradient(90deg,#fff1a8,#7fcd78,#1d91a0,#253494,#081d58)"></div>
          <div class="ramp-lab"><span>~2 weeks</span><span>~2 months</span><span>all season</span></div>` +
         swatchRow("rgb(47,143,232)", "Permanent lake");
  }
  el("legend").innerHTML = h;
}

function renderHud() {
  el("hudLayer").textContent = LAYER_NAMES[S.layer];
  el("hudDate").textContent = DYNAMIC.has(S.layer)
    ? fmt(S.dates[S.idx].date, true) + (S.dates[S.idx].validated ? "*" : "") : "Jun–Oct 2026";
  [...el("progress").querySelectorAll("span[data-i]")].forEach(s => s.classList.toggle("on", +s.dataset.i === S.idx));
  el("progress").style.visibility = DYNAMIC.has(S.layer) ? "visible" : "hidden";
}

function buildProgress() {
  el("progress").innerHTML = S.dates.map((d, i) => {
    const gap = i > 0 && ts(d.date) - ts(S.dates[i - 1].date) > 20 * 864e5 ? '<span class="gap" title="instrument gap"></span>' : "";
    return gap + `<span data-i="${i}" title="${fmt(d.date, true)}"></span>`;
  }).join("");
}

// ============================================================ date, layer, playback
function setLayer(layer) {
  S.layer = layer;
  preloadLayer(layer);
  document.querySelectorAll(".d-seg").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.layer === layer)));
  if (S.compare) { showImage("mapA", layer, S.left); showImage("mapB", layer, S.right); }
  else showImage("mapA", layer, S.idx);
  renderLegend(); renderHud();
}

let counterAnim = {};
function tweenCounter(node, to) {
  const from = +node.dataset.v || 0;
  node.dataset.v = to;
  if (reducedMotion) { node.textContent = Math.round(to).toLocaleString("en-US"); return; }
  const t0 = performance.now(), dur = 600, key = node.dataset.counter;
  cancelAnimationFrame(counterAnim[key]);
  const step = t => {
    const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
    node.textContent = Math.round(from + (to - from) * e).toLocaleString("en-US");
    if (k < 1) counterAnim[key] = requestAnimationFrame(step);
  };
  counterAnim[key] = requestAnimationFrame(step);
}

function setDate(i) {
  S.idx = Math.max(0, Math.min(S.dates.length - 1, i));
  const d = S.dates[S.idx];
  el("slider").value = S.idx;
  el("sliderLabel").textContent = fmt(d.date, true) + (d.validated ? "*" : "");
  el("slider").setAttribute("aria-valuetext", fmt(d.date, true));
  if (!S.compare && DYNAMIC.has(S.layer)) showImage("mapA", S.layer, S.idx);
  document.querySelectorAll("[data-counter]").forEach(n => tweenCounter(n, +S.rows[S.idx][n.dataset.counter]));
  el("counterDate").textContent = fmt(d.date);
  renderHud();
  drawCharts();
}

function play(end = S.dates.length - 1, loop = true) {
  if (S.timer || S.compare) return;
  el("play").textContent = "❚❚"; el("play").setAttribute("aria-label", "Pause");
  if (S.idx >= end) setDate(0);
  S.timer = setInterval(() => {
    if (S.idx >= end) { if (loop) setDate(0); else stop(); return; }
    setDate(S.idx + 1);
  }, PLAY_MS);
}
function stop() {
  clearInterval(S.timer); S.timer = null;
  el("play").textContent = "▶"; el("play").setAttribute("aria-label", "Play");
}

// ============================================================ swipe compare
function placeDivider() {
  const w = el("stage").clientWidth, x = Math.round(S.divX * w);
  el("divider").style.left = x + "px";
  el("mapB").style.clipPath = `inset(0 0 0 ${x}px)`;
}
function initDivider() {
  const handle = el("divider").querySelector(".handle");
  let dragging = false;
  handle.addEventListener("pointerdown", e => { dragging = true; handle.setPointerCapture(e.pointerId); e.preventDefault(); });
  handle.addEventListener("pointermove", e => {
    if (!dragging) return;
    S.divX = Math.min(0.97, Math.max(0.03, e.clientX / el("stage").clientWidth));
    placeDivider();
  });
  handle.addEventListener("pointerup", () => { dragging = false; });
  handle.addEventListener("keydown", e => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      S.divX = Math.min(0.97, Math.max(0.03, S.divX + (e.key === "ArrowLeft" ? -0.05 : 0.05))); placeDivider();
    }
  });
  window.addEventListener("resize", () => { if (S.compare) placeDivider(); });
}
function setCompare(on, left, right) {
  S.compare = on;
  if (on) { stop(); if (popup) popup.remove(); }
  if (left !== undefined) S.left = left;
  if (right !== undefined) S.right = right;
  el("stage").classList.toggle("compare", on);
  el("divider").hidden = !on;
  el("tagL").hidden = el("tagR").hidden = !on;
  el("cmpSelects").hidden = !on;
  el("slider").disabled = el("play").disabled = on;
  el("compareBtn").setAttribute("aria-pressed", String(on));
  if (on) {
    el("selL").value = S.left; el("selR").value = S.right;
    if (!S.exploring) S.divX = isMobile() ? 0.5 : 0.62;
    mapB.resize();
    mapB.jumpTo({ center: mapA.getCenter(), zoom: mapA.getZoom(), padding: mapA.getPadding() });
    placeDivider();
    showImage("mapA", S.layer, S.left); showImage("mapB", S.layer, S.right);
    el("tagL").textContent = "◀ " + fmt(S.dates[S.left].date, true);
    el("tagR").textContent = fmt(S.dates[S.right].date, true) + " ▶";
  } else {
    showImage("mapA", S.layer, S.idx);
  }
  drawCharts();
}

// ============================================================ charts
let chW, chF, chP;
const xScale = () => ({
  type: "linear", min: X_MIN, max: X_MAX, grid: { display: false }, border: { color: COL.ink3 },
  afterBuildTicks: a => { a.ticks = [6, 7, 8, 9, 10].map(m => ({ value: Date.UTC(2026, m, 1) })); },
  ticks: { color: COL.ink2, font: { size: 10.5 }, maxRotation: 0,
           callback: v => new Date(v).toLocaleDateString("en-GB", { month: "short", timeZone: "UTC" }) },
});

const gapPlugin = {
  id: "gapAndMarker",
  beforeDatasetsDraw(c) {
    const { ctx, chartArea: a, scales: { x } } = c, x0 = x.getPixelForValue(GAP[0]), x1 = x.getPixelForValue(GAP[1]);
    ctx.save(); ctx.fillStyle = COL.gap; ctx.fillRect(x0, a.top, x1 - x0, a.bottom - a.top);
    ctx.fillStyle = COL.ink3; ctx.font = "10px Inter, sans-serif"; ctx.textAlign = "center";
    ctx.fillText("no images", (x0 + x1) / 2, a.bottom - 6); ctx.restore();
  },
  afterDatasetsDraw(c) {
    if (c.options.plugins.gapAndMarker === false) return;
    const { ctx, chartArea: a, scales: { x } } = c;
    const marks = S.compare ? [[S.left, [4, 3]], [S.right, []]] : [[S.idx, []]];
    ctx.save();
    for (const [i, dash] of marks) {
      const px = x.getPixelForValue(ts(S.dates[i].date));
      ctx.strokeStyle = COL.gold; ctx.lineWidth = 1.5; ctx.setLineDash(dash);
      ctx.beginPath(); ctx.moveTo(px, a.top); ctx.lineTo(px, a.bottom); ctx.stroke();
    }
    ctx.restore();
  },
};

function series(key, label, color, opts = {}) {
  const val = S.rows.map(r => r.validated === "True");
  const vals = S.rows.map(r => +r[key]);
  const peak = Math.max(...vals);
  return {
    label, tech: opts.tech,
    data: vals.map((y, i) => ({ x: ts(S.rows[i].date), y: opts.pct ? (100 * y) / peak : y, raw: y })),
    borderColor: color, backgroundColor: color, borderWidth: opts.width || 2.2, tension: 0.25, borderDash: opts.dash,
    pointRadius: opts.points === false ? 0 : 3.5, pointHoverRadius: 6, pointBorderWidth: 2, pointBorderColor: color,
    pointBackgroundColor: val.map(v => (v ? "#0f1620" : color)),
    segment: { borderDash: ctx => (val[ctx.p1DataIndex] ? [4, 3] : opts.dash) },
  };
}

function baseOptions({ legend = false, yTitle, yMax, yFmt = v => v } = {}) {
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    layout: { padding: { top: 6, right: 8 } },
    plugins: {
      legend: { display: legend, position: "top", align: "start",
                labels: { boxWidth: 14, boxHeight: 2, color: COL.ink2, font: { size: 11 } } },
      tooltip: {
        backgroundColor: "#0f1620", borderColor: "rgba(255,255,255,.15)", borderWidth: 1, titleColor: "#fff", bodyColor: COL.ink2,
        callbacks: {
          title: it => { const r = S.rows[it[0].dataIndex]; return fmt(r.date, true) + (r.validated === "True" ? " (newer processing version)" : ""); },
          label: it => ` ${it.dataset.label}: ${Math.round(it.raw.raw).toLocaleString("en-US")} km²` +
                        (yTitle === "%" ? ` (${Math.round(it.parsed.y)}% of peak)` : ""),
          footer: it => it.map(i => i.dataset.tech).filter(Boolean).join("\n"),
        },
        footerFont: { size: 10, weight: "normal" }, footerColor: COL.ink3,
      },
    },
    scales: {
      x: xScale(),
      y: { beginAtZero: true, max: yMax, grid: { color: COL.grid }, border: { display: false },
           ticks: { color: COL.ink2, font: { size: 10.5 }, maxTicksLimit: 5, callback: yFmt } },
    },
  };
}

// chapter 8: both curves as % of their own peak, peaks annotated
const peakPlugin = {
  id: "peaks",
  afterDatasetsDraw(c) {
    const { ctx, scales: { x, y } } = c;
    const pk = ds => ds.data.reduce((b, p, i) => (p.y > ds.data[b].y ? i : b), 0);
    const f = c.data.datasets[0], w = c.data.datasets[1];
    const fi = pk(f), wi = pk(w);
    const fx = x.getPixelForValue(f.data[fi].x), wx = x.getPixelForValue(w.data[wi].x), top = y.getPixelForValue(100);
    ctx.save();
    ctx.font = "600 11px Inter, sans-serif"; ctx.textBaseline = "bottom";
    ctx.fillStyle = "#3ee6a0"; ctx.textAlign = "center"; ctx.fillText("hidden flood peaks · " + fmt(S.rows[fi].date), fx, top - 20);
    ctx.fillStyle = "#4aa3ff"; ctx.textAlign = "right"; ctx.fillText("open water peaks · " + fmt(S.rows[wi].date), wx + 6, top - 5);
    // bracket
    ctx.strokeStyle = COL.gold; ctx.lineWidth = 1.5;
    const by = y.getPixelForValue(55);
    ctx.beginPath(); ctx.moveTo(fx, by - 5); ctx.lineTo(fx, by + 5); ctx.moveTo(fx, by); ctx.lineTo(wx, by);
    ctx.moveTo(wx, by - 5); ctx.lineTo(wx, by + 5); ctx.stroke();
    const weeks = Math.round((f.data[fi].x < w.data[wi].x ? w.data[wi].x - f.data[fi].x : 0) / (7 * 864e5));
    ctx.fillStyle = COL.gold; ctx.textAlign = "center"; ctx.font = "700 12px Inter, sans-serif";
    ctx.fillText(`≈ ${weeks} weeks later`, (fx + wx) / 2, by - 4);
    ctx.restore();
  },
};

function initCharts() {
  Chart.defaults.font.family = "Inter, system-ui, sans-serif";
  Chart.defaults.color = COL.ink2;
  const pctOpts = baseOptions({ legend: true, yTitle: "%", yMax: 118, yFmt: v => (v <= 100 ? v + "%" : "") });
  pctOpts.layout.padding.top = 34;
  pctOpts.plugins.legend.position = "bottom";
  pctOpts.plugins.gapAndMarker = false;
  chP = new Chart(el("chartPeaks"), {
    type: "line", plugins: [gapPlugin, peakPlugin], options: pctOpts,
    data: { datasets: [
      series("flooded_forest_m5dB_km2", "Flooded forest (hidden)", COL.forest, { pct: true, width: 3 }),
      series("open_water_km2", "Open water", COL.water, { pct: true, width: 3 }),
    ] },
  });
  const click = (evt, els) => { if (els.length && !S.compare) { stop(); setDate(els[0].index); } };
  const o1 = baseOptions(); o1.onClick = click;
  const o2 = baseOptions({ legend: true }); o2.onClick = click;
  chW = new Chart(el("chartWater"), { type: "line", plugins: [gapPlugin], options: o1,
    data: { datasets: [series("open_water_km2", "Open water", COL.water, { tech: "Open water: HH below −15 dB" })] } });
  chF = new Chart(el("chartForest"), { type: "line", plugins: [gapPlugin], options: o2,
    data: { datasets: [
      series("flooded_forest_m5dB_km2", "Broad estimate", COL.forest, { tech: "Broad: tree cells with HH ≥ −5 dB" }),
      series("flooded_forest_m3dB_km2", "Strict estimate", COL.forest, { dash: [5, 4], width: 1.6, tech: "Strict: tree cells with HH ≥ −3 dB" }),
    ] } });
}
function drawCharts() { for (const c of [chW, chF]) if (c) c.draw(); }

// ============================================================ click-to-inspect
function readPixels(url) {
  return new Promise((resolve, reject) => {
    const im = new Image();
    im.onload = () => {
      const c = document.createElement("canvas"); c.width = im.naturalWidth; c.height = im.naturalHeight;
      const ctx = c.getContext("2d", { willReadFrequently: true }); ctx.drawImage(im, 0, 0);
      resolve(ctx.getImageData(0, 0, c.width, c.height).data);
    };
    im.onerror = reject; im.src = url;
  });
}
let pixelsPromise = null;
function loadPixels() {
  if (!pixelsPromise) {
    pixelsPromise = Promise.all([...S.keys.map((_, i) => readPixels(layerUrl("grey", i))), readPixels("data/worldcover100.png")])
      .then(all => { S.wc = all.pop(); S.pixels = all; });
  }
  return pixelsPromise;
}
function lonLatToPixel(lng, lat) {           // invert the bilinear map of the 4 image corners (TL, TR, BR, BL)
  const [tl, tr, br, bl] = S.corners;
  const f = (u, v, k) => (1 - u) * (1 - v) * tl[k] + u * (1 - v) * tr[k] + u * v * br[k] + (1 - u) * v * bl[k];
  let u = 0.5, v = 0.5;
  for (let it = 0; it < 8; it++) {
    const ex = f(u, v, 0) - lng, ey = f(u, v, 1) - lat;
    const dxu = (1 - v) * (tr[0] - tl[0]) + v * (br[0] - bl[0]), dxv = (1 - u) * (bl[0] - tl[0]) + u * (br[0] - tr[0]);
    const dyu = (1 - v) * (tr[1] - tl[1]) + v * (br[1] - bl[1]), dyv = (1 - u) * (bl[1] - tl[1]) + u * (br[1] - tr[1]);
    const det = dxu * dyv - dxv * dyu;
    u -= (ex * dyv - ey * dxv) / det; v -= (ey * dxu - ex * dyu) / det;
  }
  if (u < 0 || u >= 1 || v < 0 || v >= 1) return null;
  return { col: Math.floor(u * S.W), row: Math.floor(v * S.H) };
}
function reading(db) {
  if (db === null) return "no data";
  if (db < WATER_DB) return "dark: open water";
  if (db >= BRIGHT_DB) return "bright: flooded forest (or buildings)";
  return "grey: land, fields or dry forest";
}
async function inspect(lngLat) {
  if (!S.exploring || S.compare) return;
  const px = lonLatToPixel(lngLat.lng, lngLat.lat);
  if (!px) return;
  await loadPixels();
  const k = (px.row * S.W + px.col) * 4, { min, max } = S.scale;
  const values = S.pixels.map(p => (p[k + 3] === 0 ? null : +(min + (p[k] / 255) * (max - min)).toFixed(1)));
  if (values.every(v => v === null)) return;
  const wc = S.wc[k], cur = values[S.idx];
  const html = `<div class="inspect" data-values='${JSON.stringify(values)}' data-wc="${wc}">
      <h4>This spot through the season</h4>
      <p class="meta">${lngLat.lat.toFixed(4)}°N, ${lngLat.lng.toFixed(4)}°E · Land cover: <b>${WC_NAMES[wc] || "unknown"}</b></p>
      <div class="pc"><canvas></canvas></div>
      <p class="reading">${fmt(S.dates[S.idx].date)}: <b>${reading(cur)}</b>${cur === null ? "" : ` · ${cur} dB`}</p></div>`;
  if (popupChart) { popupChart.destroy(); popupChart = null; }
  if (popup) popup.remove();
  // open to the right of the spot when the story card covers the left side of the screen
  const card = document.querySelector("#explore .card"), pt = mapA.project(lngLat);
  const cardRight = card && !isMobile() ? card.getBoundingClientRect().right : 0;
  const anchor = pt.x < cardRight + 320 ? "left" : undefined;
  popup = new maplibregl.Popup({ maxWidth: "290px", focusAfterOpen: false, anchor, offset: 10 })
    .setLngLat(lngLat).setHTML(html).addTo(mapA);
  popup.on("close", () => { if (popupChart) { popupChart.destroy(); popupChart = null; } });
  const band = (y, color) => ({ data: [{ x: X_MIN, y }, { x: X_MAX, y }], borderColor: color, borderWidth: 1,
                                borderDash: [4, 3], pointRadius: 0, pointHoverRadius: 0 });
  const o = baseOptions();
  o.interaction = { mode: "nearest", intersect: false, axis: "x" };
  o.plugins.tooltip.filter = it => it.datasetIndex === 0;
  o.plugins.tooltip.callbacks = { title: it => fmt(S.dates[it[0].dataIndex].date, true),
                                  label: it => ` ${it.parsed.y} dB · ${reading(it.parsed.y)}`, footer: () => "" };
  o.scales.y = { min, max: max + 1, grid: { color: COL.grid }, border: { display: false },
                 ticks: { stepSize: 5, color: COL.ink2, font: { size: 9.5 } },
                 title: { display: true, text: "radar brightness (dB)", font: { size: 9.5 }, color: COL.ink3 } };
  popupChart = new Chart(popup.getElement().querySelector("canvas"), {
    type: "line", plugins: [gapPlugin], options: o,
    data: { datasets: [
      { data: values.map((y, i) => ({ x: ts(S.dates[i].date), y })), borderColor: "#fff", borderWidth: 2, tension: 0.2,
        pointRadius: 3, pointBackgroundColor: S.dates.map(d => (d.validated ? "#0f1620" : "#fff")), pointBorderColor: "#fff", spanGaps: true },
      band(WATER_DB, COL.water), band(BRIGHT_DB, "#3ee6a0"),
    ] },
  });
}

// ============================================================ steps (scroll-driven)
function enterExplore(on) {
  if (S.exploring === on) return;
  S.exploring = on;
  document.body.classList.toggle("exploring", on);
  el("dock").hidden = !on;
  setInteractive(on);
  if (on) loadPixels(); else if (popup) popup.remove();
}

function activate(step) {
  if (S.step === step) return;
  S.step = step;
  document.querySelectorAll(".step").forEach(s => s.classList.toggle("active", s === step));
  document.body.classList.toggle("hero-active", step.classList.contains("hero"));
  const d = step.dataset, mode = d.mode || "single";
  stop();
  enterExplore(mode === "explore");
  if (d.layer) setLayer(d.layer);
  if (mode === "compare") {
    setDate(indexOfDate(d.date));
    setCompare(true, indexOfDate(d.left), indexOfDate(d.right));
  } else {
    if (S.compare) setCompare(false);
    if (d.date) setDate(indexOfDate(d.date));
    if (mode === "play" && !reducedMotion && !params.has("noplay")) play(d.end ? indexOfDate(d.end) : S.dates.length - 1, d.loop === "true");
  }
  flyToView(d.view);
}

function initSteps() {
  const steps = [...document.querySelectorAll(".step")];
  const check = () => {
    const line = window.innerHeight * (isMobile() ? 0.62 : 0.5);
    let cur = steps[0];
    for (const s of steps) if (s.offsetParent !== null && s.getBoundingClientRect().top < line) cur = s;
    activate(cur);
  };
  let ticking = false;
  window.addEventListener("scroll", () => { if (!ticking) { ticking = true; requestAnimationFrame(() => { ticking = false; check(); }); } }, { passive: true });
  // re-frame the current view whenever the window size changes (otherwise a page opened in a small window
  // and then maximised keeps a far-out zoom)
  let rt = null;
  window.addEventListener("resize", () => {
    check();
    clearTimeout(rt);
    rt = setTimeout(() => { if (!S.exploring) flyToView(currentView, true); }, 250);
  });
  return check;
}

// ============================================================ controls
function initControls() {
  el("slider").max = S.dates.length - 1;
  el("slider").addEventListener("input", e => { stop(); setDate(+e.target.value); });
  el("play").addEventListener("click", () => (S.timer ? stop() : play()));
  el("opacity").addEventListener("input", e => setOpacity(+e.target.value));
  document.querySelectorAll(".d-seg").forEach(b => b.addEventListener("click", () => setLayer(b.dataset.layer)));
  el("resetBtn").addEventListener("click", () => {
    if (popup) popup.remove();
    mapA.fitBounds(bounds(), { padding: framePadding(), duration: reducedMotion ? 0 : 900 });
  });
  el("compareBtn").addEventListener("click", () => {
    if (S.compare) setCompare(false);
    else { S.divX = 0.5; setCompare(true, indexOfDate("2026-06-19"), indexOfDate("2026-08-30")); }
  });
  for (const id of ["selL", "selR"]) {
    el(id).innerHTML = S.dates.map((d, i) => `<option value="${i}">${fmt(d.date, true)}${d.validated ? "*" : ""}</option>`).join("");
    el(id).addEventListener("change", () => setCompare(true, +el("selL").value, +el("selR").value));
  }
  let user = CONFIG.githubUser, repo = CONFIG.repoName;
  if (location.hostname.endsWith(".github.io")) {
    user = location.hostname.split(".")[0];
    repo = location.pathname.split("/").filter(Boolean)[0] || repo;
  }
  el("repo-link").href = `https://github.com/${user}/${repo}`;
  el("process-link").href = `https://github.com/${user}/${repo}/blob/${CONFIG.branch}/docs/PROCESS.md`;
}

// ============================================================ start
(async function main() {
  if (params.has("noplay")) document.body.classList.add("still");
  if (params.has("noshadow")) document.body.classList.add("noshadow");
  try { await loadData(); }
  catch (e) {
    document.body.insertAdjacentHTML("afterbegin", "<p style='position:fixed;z-index:99;padding:16px;background:#300;color:#fff'>" +
      "Could not load the data files. If you opened index.html from disk, serve the folder: python -m http.server 8000 -d site</p>");
    throw e;
  }
  mapA = makeMap("mapA"); mapB = makeMap("mapB");
  mapA.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  mapA.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: "Radar: NISAR (NASA/ISRO), ASF DAAC" }), "bottom-right");
  mapA.on("click", e => inspect(e.lngLat));
  syncMaps(mapA, mapB);
  setInteractive(false);
  ready.mapA.then(() => { mapA.resize(); mapB.resize(); flyToView(currentView, true); });
  buildProgress(); initDivider(); initControls(); initCharts();
  preloadLayer("flood");
  setDate(0); renderLegend();
  const check = initSteps();

  // deep links: #ch1..#ch12, #explore, ?ch=5 / ?ch=explore. Test hooks: &noscroll=1, &inspect=lng,lat
  const q = params.get("ch");
  const hash = q ? "#" + (/^\d+$/.test(q) ? "ch" + q : q) : location.hash;
  const target = /^#(ch\d+|explore|top)$/.test(hash) && document.querySelector(hash);
  if (target) S.step = null;
  if (target && params.has("solo")) {               // test hook: show only this step (no scrolling needed)
    document.querySelectorAll(".step").forEach(s => { if (s !== target) s.style.display = "none"; });
  }
  if (target) {
    history.scrollRestoration = "manual";
    if (!params.has("noscroll")) target.scrollIntoView({ behavior: "auto" });
    activate(target);
  } else if (!params.has("noscroll")) check();
  else activate(document.querySelector(".step"));
  if (params.has("inspect")) {
    const [lng, lat] = params.get("inspect").split(",").map(Number);
    await ready.mapA; await loadPixels();
    setTimeout(() => inspect({ lng, lat }), 2600);
  }
})();
