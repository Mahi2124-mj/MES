"""Q Chart layout search (2026-11-12).

Finds, for every month, a pyramid orientation and position inside its ring
segment that keeps every day bubble and the month label as far as possible from
the ring edges, the dividing lines and the Q tail.  Prints the JS layout table
and the worst clearance.  Geometry = the chart's own CSS: bubble 22 px (+1 px
side margins -> 24 px pitch), rows 21 px apart, label 6 px under the last row.
"""
import json
import math
import sys

import numpy as np

W, H = 1000, 1100
CX, CY = 500, 530
RX, RY = 455, 510            # outer ellipse
TAIL_HALF = 20               # half width of the tail bar
TAIL_ANG = 45                # parametric angle of the tail (replaces that divider)
TAIL_OUT = 1.30              # tail end, as a factor of the outer ellipse
BUB_R = 11.75                # bubble radius incl. border
PITCH_X, PITCH_Y = 24, 21
LABEL_GAP, LABEL_H = 6, 17
CHAR_W = 10.4

MONTHS = [("JULY", 31, -90), ("AUGUST", 31, -60), ("SEPTEMBER", 30, -30), ("OCTOBER", 31, 0),
          ("NOVEMBER", 30, 30), ("DECEMBER", 31, 60), ("JANUARY", 31, 90), ("FEBRUARY", 29, 120),
          ("MARCH", 31, 150), ("APRIL", 30, 180), ("MAY", 31, 210), ("JUNE", 30, 240)]
SHAPES = {"up": [3, 4, 5, 6, 7, 6], "down": [6, 7, 6, 5, 4, 3]}


def ell(rx, ry, a):
    t = math.radians(a)
    return np.array([CX + rx * math.cos(t), CY + ry * math.sin(t)])


def tail_poly(rx, ry):
    """Centre line from the inner ellipse at TAIL_ANG out to TAIL_OUT x outer."""
    p0 = ell(rx * 0.97, ry * 0.97, TAIL_ANG)
    p1 = ell(RX * TAIL_OUT, RY * TAIL_OUT, TAIL_ANG)
    d = (p1 - p0) / np.linalg.norm(p1 - p0)
    n = np.array([-d[1], d[0]]) * TAIL_HALF
    return p0, p1, [p0 + n, p1 + n, p1 - n, p0 - n]


def seg_dist(P, a, b):
    ab = b - a
    t = np.clip(((P - a) @ ab) / (ab @ ab), 0, 1)
    proj = a + t[:, None] * ab
    return np.linalg.norm(P - proj, axis=1)


def ellipse_pts(rx, ry, n=6000):
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return np.stack([CX + rx * np.cos(t), CY + ry * np.sin(t)], axis=1)


class Geo:
    def __init__(self, rx, ry):
        self.rx, self.ry = rx, ry
        self.outer = ellipse_pts(RX, RY)
        self.inner = ellipse_pts(rx, ry)
        self.divs = []
        for a in range(-105, 255, 30):
            if TAIL_HALF > 0 and a % 360 == TAIL_ANG % 360:
                continue
            self.divs.append((ell(rx, ry, a), ell(RX, RY, a)))
        self.p0, self.p1, self.tq = tail_poly(rx, ry)
        self.tail_edges = [(self.tq[i], self.tq[(i + 1) % 4]) for i in range(4)] if TAIL_HALF > 0 else []

    def clearance(self, P, R):
        """min distance from circles (centres P, radii R) to every line; <0 = crossing."""
        x = (P[:, 0] - CX) / RX
        y = (P[:, 1] - CY) / RY
        inside_outer = x * x + y * y < 1
        xi = (P[:, 0] - CX) / self.rx
        yi = (P[:, 1] - CY) / self.ry
        outside_inner = xi * xi + yi * yi > 1
        d = np.full(len(P), 1e9)
        for a_, b_ in ((RX, RY), (self.rx, self.ry)):
            # first-order distance to the ellipse |F| / |grad F| (exact enough near the curve)
            dx, dy = P[:, 0] - CX, P[:, 1] - CY
            F = dx * dx / (a_ * a_) + dy * dy / (b_ * b_) - 1
            g = np.sqrt((2 * dx / (a_ * a_)) ** 2 + (2 * dy / (b_ * b_)) ** 2) + 1e-12
            d = np.minimum(d, np.abs(F) / g)
        for a, b in self.divs + self.tail_edges:
            d = np.minimum(d, seg_dist(P, a, b))
        # inside the tail polygon?
        q = self.tq
        if TAIL_HALF <= 0:
            c = d - R
            c[~inside_outer | ~outside_inner] = -50
            return c.min()
        inside_tail = np.ones(len(P), bool)
        for i in range(4):
            a, b = q[i], q[(i + 1) % 4]
            cross = (b[0] - a[0]) * (P[:, 1] - a[1]) - (b[1] - a[1]) * (P[:, 0] - a[0])
            inside_tail &= cross > 0
        if not inside_tail.any():
            inside_tail2 = np.ones(len(P), bool)
            for i in range(4):
                a, b = q[i], q[(i + 1) % 4]
                cross = (b[0] - a[0]) * (P[:, 1] - a[1]) - (b[1] - a[1]) * (P[:, 0] - a[0])
                inside_tail2 &= cross < 0
            inside_tail = inside_tail2
        c = d - R
        c[~inside_outer | ~outside_inner | inside_tail] = -50
        return c.min()


def layout_points(shape, name, cx, top):
    """bubble centres + label sample points for a block whose top row starts at `top`."""
    P, R = [], []
    for i, n in enumerate(shape):
        y = top + 11 + i * PITCH_Y
        for j in range(n):
            P.append((cx + (j - (n - 1) / 2) * PITCH_X, y))
            R.append(BUB_R)
    ly = top + len(shape) * PITCH_Y + 1 + LABEL_GAP + LABEL_H / 2
    lw = len(name) * CHAR_W / 2
    for dx in (-lw, -lw / 2, 0, lw / 2, lw):
        for dy in (-LABEL_H / 2, LABEL_H / 2):
            P.append((cx + dx, ly + dy))
            R.append(2)
    return np.array(P), np.array(R)


def block_height(shape):
    return len(shape) * PITCH_Y + 1 + LABEL_GAP + LABEL_H


def fit_month(geo, name, days, ang, shapes):
    best = None
    for sk in shapes:
        shape = list(SHAPES[sk])
        # trim the last day(s) for 29 / 30-day months from the last row
        extra = 31 - days
        k = len(shape) - 1
        while extra:
            if shape[k] > 0:
                shape[k] -= 1
                extra -= 1
            else:
                k -= 1
        hgt = block_height(shape)
        for da in np.arange(-10, 10.1, 1.0):
            for f in np.arange(0.25, 0.76, 0.025):
                r_x = geo.rx + f * (RX - geo.rx)
                r_y = geo.ry + f * (RY - geo.ry)
                c = ell(r_x, r_y, ang + da)
                P, R = layout_points(shape, name, c[0], c[1] - hgt / 2)
                cl = geo.clearance(P, R)
                if best is None or cl > best[0]:
                    best = (cl, sk, shape, float(c[0]), float(c[1] - hgt / 2), float(da), float(f))
    return best


def solve(rx, ry, shapes=("up", "down"), verbose=False):
    geo = Geo(rx, ry)
    res = []
    for name, days, ang in MONTHS:
        b = fit_month(geo, name, days, ang, shapes)
        res.append((name,) + b)
        if verbose:
            print(f"  {name:<10} clear={b[0]:6.1f} shape={b[1]:<4} x={b[3]:.0f} top={b[4]:.0f} da={b[5]:+.0f} f={b[6]:.2f}")
    return min(r[1] for r in res), res, geo


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "search"
    if mode == "search":
        out = []
        for rx in range(170, 236, 10):
            for ry in range(200, 286, 10):
                worst, res, _ = solve(rx, ry)
                out.append((worst, rx, ry))
                print(f"rx={rx} ry={ry} worst={worst:.1f}", flush=True)
        out.sort(reverse=True)
        print("BEST", out[:5])
    else:
        rx, ry = int(sys.argv[2]), int(sys.argv[3])
        worst, res, geo = solve(rx, ry, verbose=True)
        print("WORST", round(worst, 1))
        table = [{"name": r[0], "rows": r[3], "x": round(r[4], 1), "top": round(r[5], 1)} for r in res]
        print("JSON", json.dumps({"rx": rx, "ry": ry, "months": table,
                                  "tail": [[round(float(v), 1) for v in p] for p in geo.tq],
                                  "p0": [round(float(v), 1) for v in geo.p0],
                                  "p1": [round(float(v), 1) for v in geo.p1]}))
