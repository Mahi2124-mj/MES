        // 1. Q OUTLINE (2026-11-12, MES, as the operator's reference drawing): ring and
        // 12 month dividers; the Q tail is a band through DECEMBER that opens the ring
        // and goes on into the hole and out at the lower right.
        const cx = 500, cy = 530;
        const rxOuter = 455, ryOuter = 510;
        const rxInner = 205, ryInner = 230;

        let svg = `<ellipse cx="${cx}" cy="${cy}" rx="${rxInner}" ry="${ryInner}" fill="none" stroke="#000" stroke-width="2.5"/>`;
        svg += `<ellipse cx="${cx}" cy="${cy}" rx="${rxOuter}" ry="${ryOuter}" fill="none" stroke="#000" stroke-width="2.5"/>`;

        // Q tail band (drawn before the dividers so the NOV/DEC and DEC/JAN lines stay on top)
        svg += `<path d="M645.0,692.6 A205,230 0 0 1 553.1,752.2" fill="none" stroke="#fff" stroke-width="6"/><path d="M821.7,890.6 A455,510 0 0 1 713.6,980.3" fill="none" stroke="#fff" stroke-width="6"/><path d="M645.0,692.6 Q581.7,621.8 507.6,580.1 L438.8,658.1 Q499.8,692.5 553.1,752.2" fill="none" stroke="#000" stroke-width="2.5" stroke-linejoin="miter"/><path d="M821.7,890.6 Q871.7,946.6 937.0,983.4 L828.9,1073.1 Q763.6,1036.2 713.6,980.3" fill="none" stroke="#000" stroke-width="2.5" stroke-linejoin="miter"/>`;

        const divAngles = [-105, -75, -45, -15, 15, 45, 75, 105, 135, 165, 195, 225];
        divAngles.forEach(a => {
            let angle = a * Math.PI / 180;
            let x1 = cx + rxInner * Math.cos(angle);
            let y1 = cy + ryInner * Math.sin(angle);
            let x2 = cx + rxOuter * Math.cos(angle);
            let y2 = cy + ryOuter * Math.sin(angle);
            svg += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#000" stroke-width="2.5"/>`;
        });

        document.getElementById('svg-bg').innerHTML = svg;

        // 2. MONTHS: each pyramid placed inside its segment, clear of every line
        //    (positions from a clearance search, 2026-11-12): [name, days, centre x, top]
        const months = [["JULY", 31, 500.0, 36.0], ["AUGUST", 31, 683.8, 98.2], ["SEPTEMBER", 30, 815.0, 242.8], ["OCTOBER", 31, 855.0, 455.0], ["NOVEMBER", 30, 810.5, 648.0], ["DECEMBER", 31, 686.1, 802.2], ["JANUARY", 31, 500.0, 867.0], ["FEBRUARY", 29, 313.9, 802.2], ["MARCH", 31, 189.5, 648.0], ["APRIL", 30, 151.2, 455.0], ["MAY", 31, 187.1, 252.5], ["JUNE", 30, 316.2, 98.2]];

        let monthsHTML = '';
        months.forEach(m => {
            let html = `<div class="month-container" data-month="${m[0]}" style="left:${m[2] - 85}px; top:${m[3]}px; height:auto; justify-content:flex-start;">`;
            let day = 1;
            const rowSizes = [3, 4, 5, 6, 7, 6];
            rowSizes.forEach(size => {
                if (day > m[1]) return;
                html += `<div class="pyramid-row">`;
                for (let i = 0; i < size; i++) {
                    if (day <= m[1]) {
                        html += `<div class="bubble" data-day="${day}" onclick="toggleColor(this)">${day}</div>`;
                        day++;
                    }
                }
                html += `</div>`;
            });
            html += `<div class="month-label">${m[0]}</div>`;
            html += `</div>`;
            monthsHTML += html;
        });

        document.getElementById('months-layer').innerHTML = monthsHTML;

        // 3. FEBRUARY follows the YEAR box (2026-11-12, operator).  The chart is for a
        //    financial year, April -> March: FY 2026-27 -> February 2027.  "2026-27" (or
        //    "2026-2027") names both years; one year typed is the FY's first year, so
        //    "2026" -> February 2027.  Empty: the current FY (from April).  Only the 29th
        //    is shown or hidden, so the colours already marked stay.
        function qFebYear() {
            const el = document.getElementById('q-year');
            const v = String(el ? el.value : '');
            let m = v.match(/(\d{4})\s*[-\/–]\s*(\d{2,4})/);
            if (m) { let y2 = m[2]; if (y2.length === 2) y2 = m[1].slice(0, 2) + y2; return +y2; }
            m = v.match(/(\d{4})/);
            if (m) return +m[1] + 1;
            const t = new Date();
            return t.getMonth() >= 3 ? t.getFullYear() + 1 : t.getFullYear();   // FY starts in April
        }
        function qSyncFeb() {
            const y = qFebYear(), leap = (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
            const b = document.querySelector('.month-container[data-month="FEBRUARY"] .bubble[data-day="29"]');
            if (!b) return;
            b.style.display = leap ? '' : 'none';
            if (!leap) b.classList.remove('bg-green', 'bg-yellow', 'bg-red');
            const lab = document.querySelector('.month-container[data-month="FEBRUARY"] .month-label');
            if (lab) lab.title = 'FY ' + (y - 1) + '-' + String(y).slice(2) + ': February ' + y + ' - ' + (leap ? 29 : 28) + ' days';
        }
        const qYearBox = document.getElementById('q-year');
        if (qYearBox) qYearBox.addEventListener('input', qSyncFeb);
        qSyncFeb();