"""
Visual user-manual generator (PDF, multi-page).

Each page is a matplotlib figure laid out like a piece of design work:
boxes + arrows + colour-coded zones.  No tables.  Output PDF lands at
EOL_MES_User_Manual.pdf next to this script.
"""
from __future__ import annotations

import os
from matplotlib.backends.backend_pdf import PdfPages
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle, Circle, Wedge, Polygon
from matplotlib.lines import Line2D
import matplotlib.patheffects as pe
from matplotlib.transforms import blended_transform_factory


# ───────────────────────────── PALETTE ─────────────────────────────
NAVY    = "#0B2447"
ROYAL   = "#19376D"
BLUE    = "#3A86FF"
LIGHT_B = "#A0C4FF"
GREEN   = "#06A77D"
LIGHT_G = "#B7EFC5"
RED     = "#D62828"
LIGHT_R = "#FFADAD"
AMBER   = "#F4A261"
LIGHT_A = "#FFE5B4"
GREY    = "#6C757D"
LIGHT_K = "#E9ECEF"
INK     = "#212529"
SLATE   = "#475569"
BG      = "#FBFBFD"


def _new_page(figsize=(11.69, 8.27)):
    """A4 landscape figure with consistent background + no axes."""
    fig, ax = plt.subplots(figsize=figsize, dpi=150)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 70)
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    return fig, ax


def title_bar(ax, title, subtitle=None, color=NAVY):
    """Bold coloured strip at the top of every page."""
    ax.add_patch(Rectangle((0, 65), 100, 5, color=color, zorder=5))
    ax.text(2, 67.5, title, color="white", weight="bold", fontsize=18, va="center", zorder=6)
    if subtitle:
        ax.text(99, 67.5, subtitle, color="white", fontsize=10, va="center", ha="right", zorder=6)


def box(ax, x, y, w, h, label, fill=BLUE, txt_color="white",
        sub=None, fontsize=10, sub_size=8, radius=0.4, edge=None, alpha=1.0):
    """Rounded rectangle with bold label + optional small subtitle."""
    rect = FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0.08,rounding_size={radius}",
        linewidth=1.2, facecolor=fill,
        edgecolor=(edge or fill), alpha=alpha, zorder=4,
    )
    ax.add_patch(rect)
    cy = y + h/2 + (h*0.10 if sub else 0)
    ax.text(x + w/2, cy, label, ha="center", va="center",
            color=txt_color, weight="bold", fontsize=fontsize, zorder=5)
    if sub:
        ax.text(x + w/2, y + h/2 - h*0.22, sub, ha="center", va="center",
                color=txt_color, fontsize=sub_size, alpha=0.95, zorder=5)


def arrow(ax, x1, y1, x2, y2, color=NAVY, width=1.5, label=None,
          mut=14, lw=1.6, style="->"):
    """Curved/straight arrow with optional mid-label."""
    a = FancyArrowPatch(
        (x1, y1), (x2, y2),
        arrowstyle=style, mutation_scale=mut,
        linewidth=lw, color=color,
        zorder=3, shrinkA=2, shrinkB=2,
    )
    ax.add_patch(a)
    if label:
        mx, my = (x1+x2)/2, (y1+y2)/2
        ax.text(mx, my+0.6, label, ha="center", va="bottom",
                fontsize=8, color=color, weight="bold",
                bbox=dict(boxstyle="round,pad=0.2", fc="white",
                          ec=color, lw=0.6))


def caption(ax, x, y, text, color=SLATE, size=9, italic=False, weight="normal"):
    ax.text(x, y, text, fontsize=size, color=color, va="top",
            style="italic" if italic else "normal", weight=weight)


def section_heading(ax, x, y, text, color=NAVY):
    ax.text(x, y, text, fontsize=12, color=color, weight="bold")
    ax.add_line(Line2D([x, x+20], [y-1.2, y-1.2], color=color, lw=1.5))


# ════════════════════════════════════════════════════════════════════
#  PAGE 1 — Cover
# ════════════════════════════════════════════════════════════════════
def page_cover(pdf):
    fig, ax = _new_page()
    # Big colour blocks left
    ax.add_patch(Rectangle((0, 0),  35, 70, color=NAVY,  zorder=1))
    ax.add_patch(Rectangle((0, 49), 35,  6, color=BLUE,  zorder=2))
    ax.add_patch(Rectangle((0, 38), 35,  4, color=GREEN, zorder=2))
    ax.add_patch(Rectangle((0, 31), 35,  4, color=AMBER, zorder=2))
    ax.add_patch(Rectangle((0, 24), 35,  4, color=RED,   zorder=2))

    ax.text(2.5, 60, "EOL", color="white", weight="bold", fontsize=42)
    ax.text(2.5, 56, "MES + Camera CMS", color="white", weight="bold", fontsize=16)
    ax.text(2.5, 52, "User Manual", color="white", fontsize=14, alpha=0.9)
    ax.text(2.5, 14, "Toyota Boshoku Device India", color="white", fontsize=10, alpha=0.85)
    ax.text(2.5, 11, "Bawal Plant   |   YNC SS Line", color="white", fontsize=10, alpha=0.85)
    ax.text(2.5, 6,  "Maintainer: Vivek Kumar", color="white", fontsize=9, alpha=0.7)
    ax.text(2.5, 4,  "Version: 1.0   |   2026-05-07",   color="white", fontsize=9, alpha=0.7)

    # Right side: schematic preview
    ax.text(40, 58, "What's inside",
            color=NAVY, weight="bold", fontsize=18)
    ax.add_line(Line2D([40, 95], [56.5, 56.5], color=NAVY, lw=2))

    items = [
        ("1", "Purpose & Methodology",        "Why we built this and how the pieces fit",       NAVY),
        ("2", "Collector  →  4 Departments",  "One poll, four views — the heart of the system", BLUE),
        ("3", "End-to-End Architecture",      "PLC bit  →  database  →  screen, in seconds",    GREEN),
        ("4", "Network & IPs",                "Where each component physically lives",           AMBER),
        ("5", "Directory Structure",          "Where every important file is kept",              RED),
        ("6", "Page → Data → DB Map",         "Which screen reads which table",                  "#7E6BC4"),
        ("7", "How to Start  /  Roles",       "One launcher + per-role access",                  SLATE),
    ]
    for i, (num, title, sub, c) in enumerate(items):
        y = 50 - i*5.7
        ax.add_patch(Circle((42.5, y), 1.6, color=c, zorder=4))
        ax.text(42.5, y, num, ha="center", va="center",
                color="white", weight="bold", fontsize=12, zorder=5)
        ax.text(46, y+0.6, title, color=NAVY, weight="bold", fontsize=12)
        ax.text(46, y-1.2, sub,   color=SLATE, fontsize=9.5)

    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


# ════════════════════════════════════════════════════════════════════
#  PAGE 2 — Purpose & Methodology
# ════════════════════════════════════════════════════════════════════
def page_purpose(pdf):
    fig, ax = _new_page()
    title_bar(ax, "  1.  Purpose & Methodology", "Why this project exists", NAVY)

    # The three pillar boxes
    pillars = [
        ("VISIBILITY",   "Operators on the floor and managers\nin the office see the SAME numbers,\nin real time, with no manual entry.",       BLUE,  10),
        ("TRACEABILITY", "Every cycle is recorded on video.\nClick a defect in the dashboard →\ninstantly play that exact moment.",            GREEN, 38),
        ("AUTOMATION",   "PLC bits drive everything.\nNo human in the loop between\nthe machine and the screen.",                              AMBER, 66),
    ]
    for label, text, c, x in pillars:
        box(ax, x, 45, 26, 13, label, fill=c, fontsize=14, radius=1.0)
        ax.text(x + 13, 41, text, ha="center", va="top", color=INK, fontsize=9.5)

    # Methodology icon-row
    ax.text(50, 32, "How it works",
            color=NAVY, weight="bold", fontsize=14, ha="center")
    ax.add_line(Line2D([35, 65], [30.5, 30.5], color=NAVY, lw=1.5))

    # Five rounded stages with arrows between
    stages = [
        ("PLC", "Mitsubishi MC4E\n(192.168.10.150)",       NAVY,  6,  18, 14, 8),
        ("COLLECTOR", "Python\npolls every 30 ms",         BLUE, 24,  18, 14, 8),
        ("DATABASE",  "PostgreSQL\n(192.168.10.210)",      GREEN, 42, 18, 14, 8),
        ("BACKEND",   "FastAPI\n(:8080)",                  AMBER, 60, 18, 14, 8),
        ("BROWSER",   "React + Vite\n(:5656)",             RED,   78, 18, 14, 8),
    ]
    for label, sub, c, x, y, w, h in stages:
        box(ax, x, y, w, h, label, fill=c, sub=sub, fontsize=11, sub_size=8.5, radius=0.6)
    for i in range(len(stages)-1):
        x1 = stages[i][3] + stages[i][5]
        x2 = stages[i+1][3]
        arrow(ax, x1, 22, x2, 22, color=NAVY, lw=2.5, mut=20)

    # Bottom note
    caption(ax, 50, 14, "End-to-end latency: ~1-2 seconds.  All on one server PC for now (192.168.10.185).",
            italic=True, size=10, color=SLATE)
    # center it
    ax.texts[-1].set_horizontalalignment("center")

    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


# ════════════════════════════════════════════════════════════════════
#  PAGE 3 — Architecture diagram
# ════════════════════════════════════════════════════════════════════
def page_architecture(pdf):
    """Five-stack architecture, with EACH layer explained in plain
    language — the reader should be able to point to a layer and say
    'this is where X happens'.  Vertical layered diagram with arrows
    showing the bottom-up flow."""
    fig, ax = _new_page()
    title_bar(ax, "  3.  End-to-End Architecture",
              "5 stacked layers  ·  data flows bottom → top", BLUE)

    # Each layer is a wide horizontal band.  LEFT side = layer number +
    # one-line plain-english blurb (in its own dedicated 28-wide column,
    # NOT overlapping the item boxes).  RIGHT side = component boxes.
    layers = [
        # (y, h, num, title, blurb, c_dark, c_light, items)
        ( 6, 9, "5", "PRESENTATION",
            "What the user actually sees.\nThree React apps, three audiences.",
            "#8B1414", LIGHT_R,
            [("MES Frontend",   ":5656  ·  operators + admins", RED),
             ("CMS Frontend",   ":5173  ·  camera admins",      "#E04F5F"),
             ("Fullscreen TV",  "public, no login",            "#F08080")]),
        (16, 9, "4", "BACKEND APIs",
            "JWT-protected JSON endpoints.\nNo HTML rendering, pure data.",
            "#7B3F00", LIGHT_A,
            [("MES API (FastAPI)",  "port 8080", AMBER),
             ("CMS API (Flask)",    "port 5000", "#E29A55")]),
        (26, 9, "3", "STORAGE",
            "Where finished data lives.\nPostgreSQL is source of truth.",
            "#0B6B58", LIGHT_G,
            [("PostgreSQL", "energydb · 30 tables", GREEN),
             ("JSON files", "zones / cameras / plcs",  "#5BB68A"),
             ("cycles.csv", "append-only log",         "#7FCBA0")]),
        (36, 9, "2", "COLLECTORS",
            "Small Python services that\nREAD hardware on a tight loop.",
            ROYAL,  LIGHT_B,
            [("MES Collector",  "30 ms poll, edge-detect", BLUE),
             ("ffmpeg Recorder","rolling .ts capture",     "#6A8FE5"),
             ("CMS PLC Poller", "cycle events",            "#94A8E5")]),
        (46, 9, "1", "PHYSICAL",
            "The hardware itself.\nPLCs, cameras, the HDD.",
            GREY,    LIGHT_K,
            [("Main PLC",    "192.168.10.150", NAVY),
             ("Sub-PLCs ×3", ".190 / .191 / .192", ROYAL),
             ("Camera",      "192.168.10.115", "#5E548E"),
             ("HDD",         "F:\\ external", "#9B5DE5")]),
    ]

    for y, h, num, title, blurb, c_dark, c_light, items in layers:
        # Layer band
        ax.add_patch(FancyBboxPatch((2, y), 96, h,
                    boxstyle="round,pad=0.05,rounding_size=0.4",
                    facecolor=c_light, edgecolor=c_dark, linewidth=1.0, zorder=1))
        # LEFT column (28 wide): big number + title + 2-line blurb
        # Number circle
        ax.add_patch(Circle((6, y + h/2), 2.4, facecolor=c_dark, edgecolor="white",
                             linewidth=2, zorder=4))
        ax.text(6, y + h/2, num, ha="center", va="center",
                color="white", weight="bold", fontsize=14, zorder=5)
        # Title
        ax.text(10, y + h - 1.8, title, fontsize=11, color=c_dark, weight="bold")
        # Blurb
        ax.text(10, y + h - 4, blurb, fontsize=8.5, color=INK, va="top")

        # RIGHT side (from x=32) — item boxes
        n = len(items)
        x0, x1 = 32, 96
        avail = x1 - x0
        item_w = (avail - (n-1) * 2) / n
        for i, (lab, sub, c) in enumerate(items):
            x = x0 + i * (item_w + 2)
            box(ax, x, y + 1.5, item_w, h - 3, lab, fill=c,
                sub=sub, fontsize=10, sub_size=8, radius=0.4)

    # Tall arrow on the FAR-LEFT margin showing data direction
    ax.annotate("", xy=(1.0, 56), xytext=(1.0, 6),
                arrowprops=dict(arrowstyle="->,head_length=0.9,head_width=0.7",
                                color=NAVY, lw=3))
    ax.text(1.4, 30, "DATA  FLOW", rotation=90, fontsize=10,
            color=NAVY, weight="bold", va="center")

    caption(ax, 50, 2,
            "Every screen number can be traced down to one PLC bit at the bottom.  No layer skips another.",
            italic=True, size=9, color=SLATE)
    ax.texts[-1].set_horizontalalignment("center")

    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


# ════════════════════════════════════════════════════════════════════
#  PAGE 4 — Network topology
# ════════════════════════════════════════════════════════════════════
def page_network(pdf):
    fig, ax = _new_page()
    title_bar(ax, "  4.  Network & IP Map", "All hosts on the factory LAN", AMBER)

    # central hub
    hub = (50, 36)
    ax.add_patch(Circle(hub, 6, facecolor=NAVY, edgecolor="white", linewidth=2, zorder=4))
    ax.text(*hub, "LAN\nSwitch", color="white", weight="bold", fontsize=11,
            ha="center", va="center", zorder=5)

    # Spokes
    nodes = [
        # (x, y, w, h, label, color, sub, port-list)
        (15, 56, 22, 8, "Server PC",          BLUE,  "192.168.10.185\n(MES + CMS host)",       "8080 · 5656 · 5000 · 8050 · 5173"),
        (63, 56, 22, 8, "PostgreSQL",         GREEN, "192.168.10.210",                          "5432  ·  energydb"),
        ( 6, 36, 18, 8, "Main PLC",           NAVY,  "192.168.10.150",                          "5002  ·  MC4E"),
        (76, 36, 18, 8, "Camera (final)",     "#5E548E", "192.168.10.115",                      "554  ·  RTSP"),
        ( 5, 14, 17, 8, "Sub-PLC #1",         ROYAL, "192.168.10.190",                          "Upper Rail Greasing"),
        (28, 8,  18, 8, "Sub-PLC #2",         ROYAL, "192.168.10.191",                          "Lock Bar Insert"),
        (52, 8,  18, 8, "Sub-PLC #3",         ROYAL, "192.168.10.192",                          "Lower Rail Greasing"),
        (76, 14, 19, 8, "External HDD",       "#9B5DE5", "F:\\ drive",                          "video storage"),
    ]

    for x, y, w, h, lab, c, sub, ports in nodes:
        box(ax, x, y, w, h, lab, fill=c, sub=sub, fontsize=10, sub_size=8, radius=0.6)
        ax.text(x + w/2, y - 1.5, ports, ha="center", va="top",
                color=SLATE, fontsize=8, style="italic")
        # arrow from node to hub
        cx = x + w/2; cy = y + h/2
        # rough endpoint on hub edge
        import math
        dx = hub[0] - cx; dy = hub[1] - cy
        d = (dx*dx + dy*dy) ** 0.5
        ex = hub[0] - dx * 6 / d
        ey = hub[1] - dy * 6 / d
        ax.add_line(Line2D([cx, ex], [cy, ey], color=GREY, lw=1.0,
                            zorder=2, alpha=0.6))

    # ports legend at top
    caption(ax, 4, 4, "Storage path is configurable from the CMS UI:  Configuration → System Settings.",
            italic=True, color=SLATE, size=9)

    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


# ════════════════════════════════════════════════════════════════════
#  PAGE 5 — Directory tree (visual, not table)
# ════════════════════════════════════════════════════════════════════
def page_directory(pdf):
    fig, ax = _new_page()
    title_bar(ax, "  5.  Directory Structure", "What lives where", GREEN)

    # Root
    box(ax, 32, 58.5, 36, 4, "D:\\EOL\\EOL\\Deep (2)\\Deep\\", fill=NAVY,
        fontsize=12, radius=0.4)

    # Three big children
    children = [
        (3,  46, 30, "Phase2",       BLUE,  "MES backend (FastAPI)"),
        (35, 46, 30, "mes-frontend", RED,   "MES frontend (React)"),
        (67, 46, 30, "Camera CMS",   AMBER, "Camera CMS (Flask + React)"),
    ]
    for x, y, w, label, color, sub in children:
        box(ax, x, y, w, 5, label, fill=color, sub=sub, fontsize=12, sub_size=9, radius=0.5)
        # connector to root
        arrow(ax, 50, 58.5, x + w/2, y + 5, color=color, lw=1.5, mut=12)

    # Phase2 children
    p2_files = [
        ("main.py",                "App entrypoint + migrations"),
        ("auth.py",                "JWT login + role checks"),
        ("collector_engine.py",    "PLC poller class"),
        ("collectors\\collector_ync_l6.py", "Bootstrap for Line 2"),
        ("routers\\*.py",          "One file per page area"),
    ]
    for i, (f, sub) in enumerate(p2_files):
        y = 41 - i*4.4
        box(ax, 4, y, 28, 3.4, f, fill=LIGHT_B, txt_color=INK,
            fontsize=9.5, radius=0.3, sub=sub, sub_size=7.5)
        # tree connector
        ax.add_line(Line2D([2.5, 4], [y+1.7, y+1.7], color=BLUE, lw=1.0))
        ax.add_line(Line2D([2.5, 2.5], [46, y+1.7], color=BLUE, lw=1.0))

    # mes-frontend children
    fe_files = [
        ("src\\pages\\Dashboard.jsx",     "Live shop-floor dashboard"),
        ("src\\pages\\Fullscreen.jsx",    "TV display (no login)"),
        ("src\\pages\\AdminPanel.jsx",    "Admin console (huge)"),
        ("src\\pages\\ProcessGraphs.jsx", "Sub-machine graphs"),
        ("src\\context\\AuthContext.jsx", "Per-tab session + JWT"),
    ]
    for i, (f, sub) in enumerate(fe_files):
        y = 41 - i*4.4
        box(ax, 36, y, 28, 3.4, f, fill=LIGHT_R, txt_color=INK,
            fontsize=9.5, radius=0.3, sub=sub, sub_size=7.5)
        ax.add_line(Line2D([34.5, 36], [y+1.7, y+1.7], color=RED, lw=1.0))
        ax.add_line(Line2D([34.5, 34.5], [46, y+1.7], color=RED, lw=1.0))

    # Camera CMS children
    cms_files = [
        ("backend\\api_server.py",       "Flask API + RecordingManager"),
        ("backend\\recorder_engine.py",  "ffmpeg per camera (.ts roll)"),
        ("backend\\cycle_events.py",     "cycles.csv writer"),
        ("backend\\plc_poller.py",       "L108/L109/M-bit poller"),
        ("backend\\bin\\ffmpeg.exe",     "Bundled ffmpeg static build"),
    ]
    for i, (f, sub) in enumerate(cms_files):
        y = 41 - i*4.4
        box(ax, 68, y, 28, 3.4, f, fill=LIGHT_A, txt_color=INK,
            fontsize=9.5, radius=0.3, sub=sub, sub_size=7.5)
        ax.add_line(Line2D([66.5, 68], [y+1.7, y+1.7], color=AMBER, lw=1.0))
        ax.add_line(Line2D([66.5, 66.5], [46, y+1.7], color=AMBER, lw=1.0))

    # Bottom: launchers
    box(ax, 8, 11, 35, 6, "start_everything.bat", fill="#7E6BC4",
        sub="Launches all 6 services at once", fontsize=11, sub_size=9)
    box(ax, 57, 11, 35, 6, "stop_everything.bat", fill=GREY,
        sub="Kills python + node + ffmpeg cleanly", fontsize=11, sub_size=9)

    caption(ax, 50, 5, "Tree shows only the files you'll touch.  Hidden files (caches, node_modules) live alongside but you should never edit them.",
            italic=True, size=9, color=SLATE)
    ax.texts[-1].set_horizontalalignment("center")

    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


# ════════════════════════════════════════════════════════════════════
#  PAGE 6 — Page → Data → DB (swim-lane diagram)
# ════════════════════════════════════════════════════════════════════
def page_pagedata(pdf):
    fig, ax = _new_page()
    title_bar(ax, "  6.  Page → Data → Database", "Which screen reads which table", RED)

    # Three swim lanes
    lanes = [
        ("PAGE",       2,  RED,    LIGHT_R),
        ("API CALL",  37,  AMBER,  LIGHT_A),
        ("DATABASE",  72,  GREEN,  LIGHT_G),
    ]
    for label, x, c_dark, c_light in lanes:
        ax.add_patch(Rectangle((x, 6), 26, 56, facecolor=c_light, edgecolor=c_dark,
                                linewidth=1.0, alpha=0.55, zorder=1))
        ax.text(x + 13, 60.5, label, color=c_dark, weight="bold",
                fontsize=12, ha="center")

    # Rows: page  →  api  →  table
    rows = [
        ("Dashboard\n(live OEE)",        "/api/lines/2/realtime",        "ync_dashboard_complete"),
        ("Cycle-Time graph",             "/api/lines/2/ct-history",      "ync_dashboard_complete_ct_log"),
        ("Loss Distribution",            "/api/lines/2/hourly-loss-breakdown","ync_status_log"),
        ("Shift timeline strip",         "/api/lines/2/status-log",      "ync_status_log"),
        ("Quality / Poka-Yoke",          "/api/poka-yoke/live/2",        "mes_poka_yoke_events"),
        ("Maintenance Dashboard",        "/api/breakdowns/pending-production","mes_breakdowns"),
        ("Process Graphs",               "/api/submachines/{id}/hourly", "mes_machine_process_log"),
        ("Production History",           "/api/lines/2/production_history","ync_hourly_production"),
        ("Admin → Cameras",              "/api/cms/cameras (proxy)",     "CMS cameras.json"),
    ]
    n = len(rows)
    top = 56; bot = 9
    step = (top - bot) / (n - 1)
    for i, (page, api, table) in enumerate(rows):
        y = top - i*step
        box(ax, 3,  y-1.4, 24, 2.8, page,  fill=RED,    fontsize=8.5, radius=0.3)
        box(ax, 38, y-1.4, 24, 2.8, api,   fill=AMBER, txt_color=INK, fontsize=8.5, radius=0.3)
        box(ax, 73, y-1.4, 24, 2.8, table, fill=GREEN,  fontsize=8.5, radius=0.3)
        # tiny arrows between
        arrow(ax, 27.2, y, 37.8, y, color=GREY, lw=1.0, mut=8)
        arrow(ax, 62.2, y, 72.8, y, color=GREY, lw=1.0, mut=8)

    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


# ════════════════════════════════════════════════════════════════════
#  PAGE 7 — How to start
# ════════════════════════════════════════════════════════════════════
def page_start(pdf):
    fig, ax = _new_page()
    title_bar(ax, "  7.  How to Start the System", "One double-click  →  six services", "#7E6BC4")

    # Three step boxes — wider so subtitles fit comfortably
    box(ax, 4,  50, 28, 8, "Step 1", fill=BLUE,
        sub="Plug in the F:\\ external HDD", fontsize=14, sub_size=10, radius=0.6)
    box(ax, 36, 50, 28, 8, "Step 2", fill=BLUE,
        sub="Double-click  start_everything.bat", fontsize=14, sub_size=9.5, radius=0.6)
    box(ax, 68, 50, 28, 8, "Step 3", fill=BLUE,
        sub="Wait ~15 s for the 6 ports to bind", fontsize=14, sub_size=10, radius=0.6)
    arrow(ax, 32, 54, 36, 54, color=NAVY, lw=2.5, mut=18)
    arrow(ax, 64, 54, 68, 54, color=NAVY, lw=2.5, mut=18)

    # Six service windows that pop open
    section_heading(ax, 4, 44, "What opens automatically", color="#7E6BC4")
    services = [
        ("MES-API",       "uvicorn  :8080",      BLUE),
        ("MES-Collector", "PLC poll loop",       BLUE),
        ("MES-Frontend",  "Vite  :5656",         BLUE),
        ("CMS-API",       "Flask  :5000",        AMBER),
        ("CMS-Streams",   "MJPEG  :8050",        AMBER),
        ("CMS-Frontend",  "Vite  :5173",         AMBER),
    ]
    for i, (name, sub, c) in enumerate(services):
        col = i % 3
        row = i // 3
        x = 4 + col * 32
        y = 32 - row * 9
        box(ax, x, y, 28, 6, name, fill=c, sub=sub, fontsize=11, sub_size=9)

    # Browser opens
    box(ax, 28, 13, 44, 6, "Browser opens automatically",
        fill=GREEN, sub="http://127.0.0.1:5656/   →   login as  admin / admin123",
        fontsize=11, sub_size=9.5)
    arrow(ax, 50, 23, 50, 19, color=GREEN, lw=2.5, mut=18)

    caption(ax, 6, 7, "To stop everything: double-click  stop_everything.bat",
            color=RED, weight="bold", size=10)
    caption(ax, 6, 4.5,
            "If a single service hangs, just close its window — the others keep running.  Collector lock auto-releases after 30 s.",
            italic=True, size=9, color=SLATE)

    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


# ════════════════════════════════════════════════════════════════════
#  PAGE 8 — Roles + colour scheme
# ════════════════════════════════════════════════════════════════════
def page_roles(pdf):
    fig, ax = _new_page()
    title_bar(ax, "  7.  User Roles & Colour Scheme", "Who sees what", SLATE)

    roles = [
        ("ADMIN",        BLUE,  "Full control — every page,\nevery action.\nUsers, Lines, Plants,\nCameras, Slip threshold.",
         ["admin"]),
        ("PRODUCTION",   GREEN, "Live dashboard, hourly grid,\nbreakdown raise (their half),\ncycle-time history.",
         ["production"]),
        ("MAINTENANCE",  RED,   "Breakdown dashboard,\nMTTR / MTBF tiles, CAPA,\nfill maintenance half of slips.",
         ["maintenance"]),
        ("QUALITY",      AMBER, "Poka-yoke live, Quality\ndeviations, NG counter,\nPY master assignments.",
         ["quality"]),
        ("OPERATOR",     GREY,  "Read-only dashboard,\ncycle-time view.\nNo admin, no editing.",
         ["operator"]),
        ("PLANT HEAD",   NAVY,  "Same as Admin —\nhigh-level oversight.",
         ["plant_head"]),
    ]
    for i, (name, c, desc, ids) in enumerate(roles):
        col = i % 3
        row = i // 3
        x = 4 + col * 32
        y = 47 - row * 22

        # Header strip
        ax.add_patch(FancyBboxPatch((x, y+12), 28, 4,
                    boxstyle="round,pad=0.05,rounding_size=0.4",
                    facecolor=c, edgecolor=c, zorder=3))
        ax.text(x+1.5, y+14, name, color="white", weight="bold", fontsize=12, va="center")
        ax.text(x+26.5, y+14, ids[0], color="white", style="italic", fontsize=8.5,
                ha="right", va="center", alpha=0.9)
        # Body
        ax.add_patch(FancyBboxPatch((x, y), 28, 13,
                    boxstyle="round,pad=0.05,rounding_size=0.4",
                    facecolor="white", edgecolor=c, linewidth=1.3, zorder=2))
        ax.text(x+1.5, y+9.5, desc, color=INK, fontsize=9, va="top")
        # Color sample bar
        ax.add_patch(Rectangle((x+1.5, y+1.5), 25, 1.2, color=c, zorder=4))
        ax.text(x+27, y+1.5, c, color=SLATE, fontsize=7.5, ha="right", style="italic")

    # Bottom note about per-user matrix
    section_heading(ax, 4, 6.5, "Per-user override", color=SLATE)
    caption(ax, 4, 5,
            "Admin can fine-tune access via  Admin Panel → Users → Permissions Matrix  (none / read / full × every page).  Overrides win over the role defaults above.",
            italic=True, size=9.5, color=INK)

    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


# ════════════════════════════════════════════════════════════════════
#  PAGE 9 — Page access HEATMAP (replaces ugly table)
# ════════════════════════════════════════════════════════════════════
def page_access(pdf):
    fig, ax = _new_page()
    title_bar(ax, "  7.  Page Access Map", "Heat-map view  ·  one glance = one answer", SLATE)

    role_cols = [
        ("ADMIN",   BLUE),
        ("PLANT HD", NAVY),
        ("PROD",    GREEN),
        ("MAINT",   RED),
        ("QUAL",    AMBER),
        ("OP",      GREY),
        ("DEPT",    SLATE),
    ]
    pages = [
        "Login",
        "Dashboard",
        "Fullscreen TV",
        "Cycle-Time History",
        "Loss Distribution",
        "Hourly Production Grid",
        "Process Graphs",
        "Quality / Poka-Yoke",
        "Maintenance Dashboard",
        "Breakdown raise / fill",
        "CAPA / Deviations",
        "Admin → Users",
        "Admin → Lines / Plants",
        "Admin → Cameras",
        "Admin → Slip Threshold",
        "AI Assistant chat",
        "Camera CMS portal",
    ]
    # F=full, R=read-only, X=blocked, P=production-half, M=maint-half, Q=quality-only, *=public
    matrix = [
        ["F","F","F","F","F","F","F"],   # Login
        ["F","F","F","F","F","F","F"],   # Dashboard
        ["*","*","*","*","*","*","*"],   # Fullscreen
        ["F","F","F","F","F","R","F"],   # Cycle-Time
        ["F","F","F","F","F","F","F"],   # Loss Distribution
        ["F","F","F","F","F","R","F"],   # Hourly Grid
        ["F","F","F","F","F","R","F"],   # Process Graphs
        ["F","F","R","R","F","R","F"],   # Quality / Poka-Yoke
        ["F","F","R","F","R","R","F"],   # Maintenance Dashboard
        ["F","F","P","M","X","X","X"],   # Breakdown raise/fill
        ["F","F","X","M","Q","X","Q"],   # CAPA / Deviations
        ["F","F","X","X","X","X","X"],   # Admin Users
        ["F","F","X","X","X","X","X"],   # Admin Lines
        ["F","F","X","X","X","X","X"],   # Admin Cameras
        ["F","F","X","X","X","X","X"],   # Admin Slip
        ["F","F","F","F","F","X","F"],   # AI Assistant
        ["F","F","X","X","X","X","X"],   # CMS portal
    ]
    glyph = {
        "F": ("●", GREEN, "Full"),
        "R": ("◐", BLUE,  "Read-only"),
        "X": ("·", "#D9D9D9", "No access"),
        "P": ("P", "#1E88E5", "Production half"),
        "M": ("M", "#E53935", "Maintenance half"),
        "Q": ("Q", "#FB8C00", "Quality only"),
        "*": ("◇", "#9C27B0", "Public"),
    }

    n_pages = len(pages)
    n_roles = len(role_cols)
    grid_left   = 32
    grid_right  = 96
    grid_top    = 60
    grid_bottom = 8
    col_w = (grid_right - grid_left) / n_roles
    row_h = (grid_top - grid_bottom) / n_pages

    # Page labels (rows)
    for i, page in enumerate(pages):
        cy = grid_top - (i + 0.5) * row_h
        ax.text(grid_left - 1, cy, page, ha="right", va="center",
                fontsize=9, color=INK)

    # Role headers (columns)
    for j, (rname, rcol) in enumerate(role_cols):
        cx = grid_left + (j + 0.5) * col_w
        ax.add_patch(Rectangle((grid_left + j*col_w + 0.4, grid_top + 0.4),
                               col_w - 0.8, 3.2,
                               facecolor=rcol, edgecolor="white", zorder=3))
        ax.text(cx, grid_top + 2.0, rname, ha="center", va="center",
                color="white", weight="bold", fontsize=9, zorder=4)

    # Cells
    for i, row in enumerate(matrix):
        for j, code in enumerate(row):
            sym, col, _ = glyph[code]
            cx = grid_left + (j + 0.5) * col_w
            cy = grid_top  - (i + 0.5) * row_h
            cell_color = col if code in ("F","R","P","M","Q","*") else "#F4F4F4"
            ax.add_patch(Rectangle((grid_left + j*col_w + 0.3,
                                     grid_top - (i+1)*row_h + 0.3),
                                    col_w - 0.6, row_h - 0.6,
                                    facecolor=cell_color, edgecolor="white",
                                    linewidth=1.0, alpha=0.85 if code != "X" else 0.4,
                                    zorder=2))
            txt_col = "white" if code in ("F","R","P","M","Q","*") else "#999999"
            ax.text(cx, cy, sym, ha="center", va="center",
                    fontsize=12 if code in ("F","R","X","*") else 10,
                    color=txt_col, weight="bold", zorder=5)

    # Legend
    legend_items = [("F","Full"), ("R","Read-only"), ("X","No access"),
                    ("*","Public"), ("P","Prod half"), ("M","Maint half"), ("Q","Qual only")]
    lx = 4; ly = 5.5
    for sym_code, lbl in legend_items:
        sym, col, _ = glyph[sym_code]
        ax.add_patch(Rectangle((lx, ly), 1.5, 1.5, facecolor=col, edgecolor="white"))
        ax.text(lx+0.75, ly+0.75, sym, ha="center", va="center",
                color="white", fontsize=10, weight="bold")
        ax.text(lx+2, ly+0.75, lbl, fontsize=9, va="center", color=INK)
        lx += 12

    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


# ════════════════════════════════════════════════════════════════════
#  Build
# ════════════════════════════════════════════════════════════════════
# ════════════════════════════════════════════════════════════════════
#  PAGE 3 (NEW) — Collector at the centre, 4 departments at the corners
# ════════════════════════════════════════════════════════════════════
def page_collector_hub(pdf):
    """The single most important diagram: the Collector reads the PLC
    once and FOUR different departments each get the slice of data
    they care about.  This is the 'why does this project exist' page."""
    fig, ax = _new_page()
    title_bar(ax, "  2.  One Collector  →  Four Departments",
              "Same PLC data, four different views", NAVY)

    # ── Centre: the Collector + PLC source
    cx, cy = 50, 35
    # outer halo
    ax.add_patch(Circle((cx, cy), 12.5, facecolor=NAVY, edgecolor="white",
                        linewidth=2.5, zorder=5))
    ax.add_patch(Circle((cx, cy), 11.0, facecolor=BLUE, edgecolor="white",
                        linewidth=1.5, zorder=6))
    ax.text(cx, cy + 3.5, "COLLECTOR",  ha="center", va="center",
            color="white", weight="bold", fontsize=15, zorder=7)
    ax.text(cx, cy + 0.5, "Phase2 / collector_engine.py", ha="center", va="center",
            color="white", fontsize=9, zorder=7, style="italic")
    ax.text(cx, cy - 2.5, "polls PLC every 30 ms", ha="center", va="center",
            color="white", fontsize=9, zorder=7, alpha=0.95)
    # PLC tag below the collector showing source
    ax.add_patch(FancyBboxPatch((cx-9, cy-9.5), 18, 3.8,
                                boxstyle="round,pad=0.1,rounding_size=0.4",
                                facecolor="white", edgecolor=BLUE, linewidth=1.3, zorder=8))
    ax.text(cx, cy-7.6, "PLC  192.168.10.150 : 5002", ha="center", va="center",
            color=NAVY, weight="bold", fontsize=9, zorder=9)

    # ── Four department boxes at corners
    depts = [
        # (name, x, y, color, items)
        ("PRODUCTION", 4,  47, GREEN, [
            "OK / NG part counts (per shift)",
            "Plan vs actual,  OEE %",
            "Cycle time per piece",
            "Hourly slot grid (8 slots)",
        ]),
        ("QUALITY", 70, 47, AMBER, [
            "NG count by Poka-Yoke check",
            "Defect tagging on each cycle",
            "Sensor / bypass triggers",
            "Model-wise NG distribution",
        ]),
        ("MAINTENANCE", 4,  4, RED, [
            "Status-code transitions  (D6005)",
            "Breakdown duration & buckets",
            "MTTR / MTBF counters",
            "Sensor-fault & override flags",
        ]),
        ("ADMIN  /  PLANT HEAD", 70, 4, "#5E548E", [
            "Cross-shift KPIs & history",
            "Camera and PLC bindings",
            "Per-user permissions matrix",
            "Slip threshold / config",
        ]),
    ]

    for name, x, y, c, items in depts:
        # Header strip
        ax.add_patch(FancyBboxPatch((x, y+15), 26, 4,
                                     boxstyle="round,pad=0.1,rounding_size=0.4",
                                     facecolor=c, edgecolor=c, zorder=4))
        ax.text(x+13, y+17, name, ha="center", va="center",
                color="white", weight="bold", fontsize=12, zorder=5)
        # Body
        ax.add_patch(FancyBboxPatch((x, y), 26, 16,
                                     boxstyle="round,pad=0.1,rounding_size=0.4",
                                     facecolor="white", edgecolor=c, linewidth=1.5, zorder=3))
        for i, item in enumerate(items):
            ax.text(x+1.5, y+12.5 - i*3.0, "•", color=c, fontsize=14, weight="bold",
                    va="center", zorder=4)
            ax.text(x+3.5, y+12.5 - i*3.0, item, color=INK, fontsize=9.2,
                    va="center", zorder=4)

    # ── Arrows from collector to each corner
    # Top-left
    arrow(ax, cx-9, cy+5, 30, 50, color=GREEN, lw=2.5, mut=18,
          label="part counts")
    # Top-right
    arrow(ax, cx+9, cy+5, 70, 50, color=AMBER, lw=2.5, mut=18,
          label="defect tags")
    # Bottom-left
    arrow(ax, cx-9, cy-5, 30, 18, color=RED,   lw=2.5, mut=18,
          label="status / loss")
    # Bottom-right
    arrow(ax, cx+9, cy-5, 70, 18, color="#5E548E", lw=2.5, mut=18,
          label="config / KPIs")

    # Footer
    caption(ax, 50, 1.5,
            "ONE poll loop is the source of truth.  Every department reads the same numbers — no spreadsheet stitching, no manual entry.",
            italic=True, size=9, color=SLATE)
    ax.texts[-1].set_horizontalalignment("center")

    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def build():
    out = r"D:\EOL\EOL\Deep (2)\Deep\EOL_MES_User_Manual.pdf"
    with PdfPages(out) as pdf:
        page_cover(pdf)
        page_purpose(pdf)
        page_collector_hub(pdf)        # NEW — central diagram
        page_architecture(pdf)
        page_network(pdf)
        page_directory(pdf)
        page_pagedata(pdf)
        page_start(pdf)
        page_roles(pdf)
        page_access(pdf)
    print(f"WROTE: {out}  ({os.path.getsize(out):,} bytes)")


if __name__ == "__main__":
    build()
