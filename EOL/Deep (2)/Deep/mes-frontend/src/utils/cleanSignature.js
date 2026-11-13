// cleanSignature.js — 2026-11-06.
//
// Turns a phone photo of a signature into a clean stamp: cropped to the ink,
// paper made transparent, ink drawn in one colour, at most 480 px wide.  Used
// by Employee Master → Leaders → Upload sign, so a new signature looks like
// the 20 cleaned on 6-Nov (same method as /home/server/clean_signature_20261106.py).
//
// How: the paper level is a heavy blur of the brightness (follows uneven
// light); ink = clearly darker than that paper AND bluer than the paper (blue /
// purple pen; the blue test is skipped for a black pen); isolated specks of
// paper texture are dropped.  Thresholds scale with each photo's own ink.

const MAX_WORK = 960;   // px, longest side worked on
const MAX_OUT = 480;    // px, stored width (server caps the data URL at ~400 KB)

// running-sum box blur, edges clamped; in place on `a` via scratch `b`
function boxPass(a, b, w, h, r) {
  const n = 2 * r + 1;
  for (let y = 0; y < h; y++) {                      // horizontal a -> b
    const o = y * w;
    let s = 0;
    for (let i = -r; i <= r; i++) s += a[o + Math.min(w - 1, Math.max(0, i))];
    for (let x = 0; x < w; x++) {
      b[o + x] = s / n;
      s += a[o + Math.min(w - 1, x + r + 1)] - a[o + Math.max(0, x - r)];
    }
  }
  for (let x = 0; x < w; x++) {                      // vertical b -> a
    let s = 0;
    for (let i = -r; i <= r; i++) s += b[Math.min(h - 1, Math.max(0, i)) * w + x];
    for (let y = 0; y < h; y++) {
      a[y * w + x] = s / n;
      s += b[Math.min(h - 1, y + r + 1) * w + x] - b[Math.max(0, y - r) * w + x];
    }
  }
}

// value at fraction p of an integer histogram whose bin 0 means `lo`
function pct(hist, lo, total, p) {
  const goal = total * p;
  let acc = 0;
  for (let i = 0; i < hist.length; i++) { acc += hist[i]; if (acc >= goal) return i + lo; }
  return hist.length - 1 + lo;
}

/**
 * Pure pixel work.  rgba: Uint8ClampedArray (w*h*4).  Returns null when no ink
 * is found, else { data: Uint8ClampedArray, w, h } — the cropped RGBA stamp.
 */
export function cleanSignaturePixels(rgba, w, h) {
  const N = w * h;
  const L = new Float32Array(N), blue = new Float32Array(N);
  const hL = new Uint32Array(256), hB = new Uint32Array(511);
  for (let i = 0, j = 0; i < N; i++, j += 4) {
    const R = rgba[j], G = rgba[j + 1], B = rgba[j + 2];
    L[i] = 0.299 * R + 0.587 * G + 0.114 * B;
    blue[i] = B - (R + G) / 2;
    hL[Math.round(L[i])]++;
    hB[Math.round(blue[i]) + 255]++;
  }
  // paper level: 3 box passes ≈ a Gaussian blur, never below the 55th percentile
  const rad = Math.max(12, Math.floor(Math.min(w, h) / 12));
  const bg = Float32Array.from(L), tmp = new Float32Array(N);
  for (let p = 0; p < 3; p++) boxPass(bg, tmp, w, h, rad);
  const floor = pct(hL, 0, N, 0.55);
  const diff = new Float32Array(N), hD = new Uint32Array(511);
  for (let i = 0; i < N; i++) {
    diff[i] = Math.max(bg[i], floor) - L[i];
    hD[Math.max(0, Math.min(510, Math.round(diff[i]) + 255))]++;
  }
  const pb = pct(hB, -255, N, 0.5);
  const cut = Math.max(25, pct(hD, -255, N, 0.97));   // sparse ink: never "ink" = plain paper
  // the photo's own ink: median darkness / blueness of its darkest 3 % (at least 25 darker than paper)
  const hTD = new Uint32Array(511), hTB = new Uint32Array(511);
  let nTop = 0;
  for (let i = 0; i < N; i++) {
    if (Math.round(diff[i]) >= cut) {               // same rounding as the histogram
      nTop++;
      hTD[Math.max(0, Math.min(510, Math.round(diff[i]) + 255))]++;
      hTB[Math.round(blue[i]) + 255]++;
    }
  }
  if (!nTop) return null;
  const inkD = pct(hTD, -255, nTop, 0.5), inkB = pct(hTB, -255, nTop, 0.5) - pb;
  if (inkD < 20) return null;                        // nothing darker than the paper
  const useBlue = inkB > 12;
  const alpha = new Float32Array(N), mask = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    let a = Math.min(1, Math.max(0, (diff[i] - 0.28 * inkD) / (0.4 * inkD)));
    if (useBlue) a *= Math.min(1, Math.max(0, (blue[i] - pb - 0.25 * inkB) / (0.4 * inkB)));
    alpha[i] = a;
    mask[i] = a > 0.35 ? 1 : 0;
  }
  // drop isolated specks: keep ink only where its 9x9 neighbourhood has some
  boxPass(mask, tmp, w, h, 4);
  let x0 = w, y0 = h, x1 = -1, y1 = -1, sr = 0, sg = 0, sb = 0, sn = 0;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = y * w + x;
      if (mask[i] <= 0.12) { alpha[i] = 0; continue; }
      if (alpha[i] > 0.5) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
      if (alpha[i] > 0.9) { sr += rgba[i * 4]; sg += rgba[i * 4 + 1]; sb += rgba[i * 4 + 2]; sn++; }
    }
  }
  if (x1 < 0 || sn < 30) return null;
  const pad = 8;
  x0 = Math.max(0, x0 - pad); y0 = Math.max(0, y0 - pad);
  x1 = Math.min(w - 1, x1 + pad); y1 = Math.min(h - 1, y1 + pad);
  const cw = x1 - x0 + 1, ch = y1 - y0 + 1;
  const ir = Math.round(sr / sn * 0.85), ig = Math.round(sg / sn * 0.85), ib = Math.round(sb / sn * 0.85);
  const out = new Uint8ClampedArray(cw * ch * 4);
  for (let y = 0; y < ch; y++) {
    for (let x = 0; x < cw; x++) {
      const k = (y * cw + x) * 4;
      out[k] = ir; out[k + 1] = ig; out[k + 2] = ib;
      out[k + 3] = Math.round(alpha[(y + y0) * w + (x + x0)] * 255);
    }
  }
  return { data: out, w: cw, h: ch };
}

function loadImage(src) {
  return new Promise((res, rej) => {
    const img = new Image();
    img.onload = () => res(img);
    img.onerror = rej;
    img.src = src;
  });
}

function canvasOf(w, h) {
  const cv = document.createElement("canvas");
  cv.width = w; cv.height = h;
  return cv;
}

function scaledPng(source, sw, sh, maxW) {
  const s = Math.min(1, maxW / sw);
  const cv = canvasOf(Math.max(1, Math.round(sw * s)), Math.max(1, Math.round(sh * s)));
  const ctx = cv.getContext("2d");
  ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "high";
  ctx.drawImage(source, 0, 0, cv.width, cv.height);
  return cv.toDataURL("image/png");
}

/**
 * Browser: image data URL in, clean PNG data URL out.  `cleaned` is false when
 * no ink could be told from the paper — then the photo is only downscaled
 * (the old behaviour), so an upload never fails because of the cleaning.
 */
export async function cleanSignatureDataUrl(dataUrl) {
  const img = await loadImage(dataUrl);
  const iw = img.naturalWidth || img.width, ih = img.naturalHeight || img.height;
  try {
    const s = Math.min(1, MAX_WORK / Math.max(iw, ih));
    const w = Math.max(1, Math.round(iw * s)), h = Math.max(1, Math.round(ih * s));
    const work = canvasOf(w, h);
    const wctx = work.getContext("2d", { willReadFrequently: true });
    wctx.fillStyle = "#fff"; wctx.fillRect(0, 0, w, h);   // a transparent PNG reads as white paper
    wctx.drawImage(img, 0, 0, w, h);
    const res = cleanSignaturePixels(wctx.getImageData(0, 0, w, h).data, w, h);
    if (res) {
      const stamp = canvasOf(res.w, res.h);
      stamp.getContext("2d").putImageData(new ImageData(res.data, res.w, res.h), 0, 0);
      return { dataUrl: scaledPng(stamp, res.w, res.h, MAX_OUT), cleaned: true };
    }
  } catch { /* fall through to the plain downscale */ }
  return { dataUrl: scaledPng(img, iw, ih, MAX_OUT), cleaned: false };
}
