"""Write ../q-chart-layout.js (the Q Chart outline + month placement) from layout.json.

2026-11-12.  The Q tail follows the operator's reference drawing: a wide band
that runs through the DECEMBER segment.  Inside the ring the band IS the
December segment (between the normal NOV/DEC and DEC/JAN lines); the ring is
open where the band passes; the band goes on into the hole (curving a little
flatter, square end) and out of the ring at the lower right (curving flatter,
square end).  Month positions come from qlayout.py (layout.json).
"""
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
L = json.load(open(os.path.join(HERE, "layout.json")))
OUT = os.path.join(HERE, "..", "q-chart-layout.js")

CX, CY, RX, RY = 500, 530, 455, 510
rx, ry = L["rx"], L["ry"]


def ell(a_rx, a_ry, deg):
    t = math.radians(deg)
    return (CX + a_rx * math.cos(t), CY + a_ry * math.sin(t))


def add(p, v, k):
    return (p[0] + v[0] * k, p[1] + v[1] * k)


def unit(v):
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n)


def f(p):
    return "%.1f,%.1f" % p


# segment ends of the band (the December segment, parametric 45..75 deg)
D45i, D45o = ell(rx, ry, 45), ell(RX, RY, 45)
D75i = ell(rx, ry, 75)
Po = ell(RX, RY, 62)                         # where the band's lower edge leaves the ring
axis = unit((D45o[0] - D45i[0], D45o[1] - D45i[1]))
bend_in = unit((-0.87, -0.49))               # hole end: a little flatter than the axis
bend_out = unit((0.87, 0.49))                # outer end: the same, outwards

# hole part (quadratic curves, square end)
cU = add(D45i, axis, -95); Ucap = add(cU, bend_in, 85)
cL = add(D75i, axis, -80); Lcap = add(cL, bend_in, 70)
# outer part
cU2 = add(D45o, axis, 75); Uend = add(cU2, bend_out, 75)
cL2 = add(Po, axis, 75); Lend = add(cL2, bend_out, 75)

band = (
    # open the ring where the band passes: the inner arc of December, the outer arc 45..62 deg
    f'<path d="M{f(D45i)} A{rx},{ry} 0 0 1 {f(D75i)}" fill="none" stroke="#fff" stroke-width="6"/>'
    f'<path d="M{f(D45o)} A{RX},{RY} 0 0 1 {f(Po)}" fill="none" stroke="#fff" stroke-width="6"/>'
    # band edges + square ends
    f'<path d="M{f(D45i)} Q{f(cU)} {f(Ucap)} L{f(Lcap)} Q{f(cL)} {f(D75i)}" fill="none" stroke="#000" stroke-width="2.5" stroke-linejoin="miter"/>'
    f'<path d="M{f(D45o)} Q{f(cU2)} {f(Uend)} L{f(Lend)} Q{f(cL2)} {f(Po)}" fill="none" stroke="#000" stroke-width="2.5" stroke-linejoin="miter"/>'
)

months = [[m["name"], sum(m["rows"]), m["x"], m["top"]] for m in L["months"]]

js = f"""        // 1. Q OUTLINE (2026-11-12, MES, as the operator's reference drawing): ring and
        // 12 month dividers; the Q tail is a band through DECEMBER that opens the ring
        // and goes on into the hole and out at the lower right.
        const cx = {CX}, cy = {CY};
        const rxOuter = {RX}, ryOuter = {RY};
        const rxInner = {rx}, ryInner = {ry};

        let svg = `<ellipse cx="${{cx}}" cy="${{cy}}" rx="${{rxInner}}" ry="${{ryInner}}" fill="none" stroke="#000" stroke-width="2.5"/>`;
        svg += `<ellipse cx="${{cx}}" cy="${{cy}}" rx="${{rxOuter}}" ry="${{ryOuter}}" fill="none" stroke="#000" stroke-width="2.5"/>`;

        // Q tail band (drawn before the dividers so the NOV/DEC and DEC/JAN lines stay on top)
        svg += `{band}`;

        const divAngles = [-105, -75, -45, -15, 15, 45, 75, 105, 135, 165, 195, 225];
        divAngles.forEach(a => {{
            let angle = a * Math.PI / 180;
            let x1 = cx + rxInner * Math.cos(angle);
            let y1 = cy + ryInner * Math.sin(angle);
            let x2 = cx + rxOuter * Math.cos(angle);
            let y2 = cy + ryOuter * Math.sin(angle);
            svg += `<line x1="${{x1}}" y1="${{y1}}" x2="${{x2}}" y2="${{y2}}" stroke="#000" stroke-width="2.5"/>`;
        }});

        document.getElementById('svg-bg').innerHTML = svg;

        // 2. MONTHS: each pyramid placed inside its segment, clear of every line
        //    (positions from a clearance search, 2026-11-12): [name, days, centre x, top]
        const months = {json.dumps(months)};

        let monthsHTML = '';
        months.forEach(m => {{
            let html = `<div class="month-container" data-month="${{m[0]}}" style="left:${{m[2] - 85}}px; top:${{m[3]}}px; height:auto; justify-content:flex-start;">`;
            let day = 1;
            const rowSizes = [3, 4, 5, 6, 7, 6];
            rowSizes.forEach(size => {{
                if (day > m[1]) return;
                html += `<div class="pyramid-row">`;
                for (let i = 0; i < size; i++) {{
                    if (day <= m[1]) {{
                        html += `<div class="bubble" data-day="${{day}}" onclick="toggleColor(this)">${{day}}</div>`;
                        day++;
                    }}
                }}
                html += `</div>`;
            }});
            html += `<div class="month-label">${{m[0]}}</div>`;
            html += `</div>`;
            monthsHTML += html;
        }});

        document.getElementById('months-layer').innerHTML = monthsHTML;"""
FEB_JS = '\n\n        // 3. FEBRUARY follows the YEAR box (2026-11-12, operator).  The chart is for a\n        //    financial year, April -> March: FY 2026-27 -> February 2027.  "2026-27" (or\n        //    "2026-2027") names both years; one year typed is the FY\'s first year, so\n        //    "2026" -> February 2027.  Empty: the current FY (from April).  Only the 29th\n        //    is shown or hidden, so the colours already marked stay.\n        function qFebYear() {\n            const el = document.getElementById(\'q-year\');\n            const v = String(el ? el.value : \'\');\n            let m = v.match(/(\\d{4})\\s*[-\\/–]\\s*(\\d{2,4})/);\n            if (m) { let y2 = m[2]; if (y2.length === 2) y2 = m[1].slice(0, 2) + y2; return +y2; }\n            m = v.match(/(\\d{4})/);\n            if (m) return +m[1] + 1;\n            const t = new Date();\n            return t.getMonth() >= 3 ? t.getFullYear() + 1 : t.getFullYear();   // FY starts in April\n        }\n        function qSyncFeb() {\n            const y = qFebYear(), leap = (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;\n            const b = document.querySelector(\'.month-container[data-month="FEBRUARY"] .bubble[data-day="29"]\');\n            if (!b) return;\n            b.style.display = leap ? \'\' : \'none\';\n            if (!leap) b.classList.remove(\'bg-green\', \'bg-yellow\', \'bg-red\');\n            const lab = document.querySelector(\'.month-container[data-month="FEBRUARY"] .month-label\');\n            if (lab) lab.title = \'FY \' + (y - 1) + \'-\' + String(y).slice(2) + \': February \' + y + \' - \' + (leap ? 29 : 28) + \' days\';\n        }\n        const qYearBox = document.getElementById(\'q-year\');\n        if (qYearBox) qYearBox.addEventListener(\'input\', qSyncFeb);\n        qSyncFeb();'
js += FEB_JS
open(OUT, "w").write(js)
print("wrote", OUT, len(js), "chars; band ends:", f(Ucap), f(Lcap), f(Uend), f(Lend))
