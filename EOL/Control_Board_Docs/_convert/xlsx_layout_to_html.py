"""Excel line-layout drawing -> self-contained HTML (SVG), for the MES Control Board.

2026-11-04.  "SS Line Layout.xlsx" (Seat Slider lines except YRA-SS) draws each
layout with Excel shapes (boxes, arrows, connectors, text, operator icons), not
as a picture, and the server has no LibreOffice.  This renders the DrawingML of
the chosen anchors to SVG — group transforms, preset shapes (rect, ellipse,
diamond, triangle, plus, left/right arrow, straight connector), solid fills incl.
theme colours with lumMod/lumOff, dashes, arrow heads, rotation/flip, text
(size, bold, colour, alignment, vertical text) and PNG pictures.  EMF pictures
(the copied legend strip) are skipped; the symbol legend is drawn as one row
with the same symbols and the workbook's own operator icon.

usage: python3 xlsx_layout_to_html.py <Control_Board_Docs dir> <out_dir>
       (the workbooks and what to draw from each are listed in JOBS below)
"""
import base64, colorsys, html, re, sys, zipfile
import os
import xml.etree.ElementTree as ET

NS = {
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
A, XDR = "{%s}" % NS["a"], "{%s}" % NS["xdr"]
R_EMBED = "{%s}embed" % NS["r"]
EMU_PX = 9525.0
# 2026-11-06: company logo in the header box (same TB mark as the Hourly Report
# and the breakdown slip, mes-frontend/public/logo.jpg trimmed to 200 px).
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tb_logo.png"), "rb") as _f:
    LOGO_URI = "data:image/png;base64," + base64.b64encode(_f.read()).decode()


def px(v):
    return float(v) / EMU_PX


class Book:
    def __init__(self, path, drawing="drawing1.xml"):
        self.z = zipfile.ZipFile(path)
        self.theme = self._theme()
        self.rels = self._rels("xl/drawings/_rels/%s.rels" % drawing)
        self.drawing = ET.fromstring(self.z.read("xl/drawings/" + drawing))
        self.markers = {}

    def _theme(self):
        t = ET.fromstring(self.z.read("xl/theme/theme1.xml"))
        out = {}
        cs = t.find(".//a:clrScheme", NS)
        for el in cs:
            tag = el.tag.replace(A, "")
            c = el[0]
            out[tag] = c.get("lastClr") or c.get("val")
        out["tx1"], out["bg1"] = out.get("dk1"), out.get("lt1")
        out["tx2"], out["bg2"] = out.get("dk2"), out.get("lt2")
        # line widths of the theme's style matrix (lnRef idx 1..3)
        self.ln_widths = [px(ln.get("w", "9525")) for ln in t.findall(".//a:fmtScheme/a:lnStyleLst/a:ln", NS)]
        return out

    def _rels(self, name):
        x = ET.fromstring(self.z.read(name))
        return {r.get("Id"): r.get("Target") for r in x}

    def image_uri(self, rid):
        tgt = self.rels.get(rid, "")
        if not tgt.lower().endswith((".png", ".jpg", ".jpeg", ".gif")):
            return None
        data = self.z.read("xl/" + tgt.replace("../", ""))
        mime = "image/png" if tgt.lower().endswith(".png") else "image/jpeg"
        return "data:%s;base64,%s" % (mime, base64.b64encode(data).decode())

    # ── colours ──────────────────────────────────────────────────────────
    def color(self, parent):
        """Colour of the first colour child of a fill element, or None."""
        if parent is None:
            return None
        for c in parent:
            tag = c.tag.replace(A, "")
            if tag == "srgbClr":
                base = c.get("val")
            elif tag == "schemeClr":
                base = self.theme.get(c.get("val"), "000000")
            elif tag == "sysClr":
                base = c.get("lastClr") or "000000"
            elif tag == "prstClr":
                base = {"black": "000000", "white": "FFFFFF", "red": "FF0000"}.get(c.get("val"), "000000")
            else:
                continue
            return self._mods(base, c)
        return None

    @staticmethod
    def _mods(hexv, el):
        r, g, b = (int(hexv[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        h, l, s = colorsys.rgb_to_hls(r, g, b)
        lm = el.find("a:lumMod", NS)
        lo = el.find("a:lumOff", NS)
        if lm is not None or lo is not None:
            l = l * (int(lm.get("val")) / 100000.0 if lm is not None else 1)
            l += int(lo.get("val")) / 100000.0 if lo is not None else 0
            l = max(0, min(1, l))
            r, g, b = colorsys.hls_to_rgb(h, l, s)
        sh = el.find("a:shade", NS)
        if sh is not None:
            k = int(sh.get("val")) / 100000.0
            r, g, b = r * k, g * k, b * k
        return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))

    def marker(self, kind, color):
        key = (kind, color)
        if key not in self.markers:
            mid = "m%d" % len(self.markers)
            if kind == "triangle":
                body = '<path d="M0,0 L10,5 L0,10 z" fill="%s"/>' % color
            else:   # open "arrow"
                body = '<path d="M0,0 L10,5 L0,10" fill="none" stroke="%s" stroke-width="1.6"/>' % color
            self.markers[key] = (mid, '<marker id="%s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="4" '
                                      'markerHeight="4" orient="auto-start-reverse" markerUnits="strokeWidth">%s</marker>'
                                 % (mid, body))
        return self.markers[key][0]


def xfrm_of(sppr):
    x = sppr.find("a:xfrm", NS) if sppr is not None else None
    if x is None:
        return None
    off, ext = x.find("a:off", NS), x.find("a:ext", NS)
    d = {"x": px(off.get("x")), "y": px(off.get("y")), "w": px(ext.get("cx")), "h": px(ext.get("cy")),
         "rot": int(x.get("rot", "0")) / 60000.0, "fh": x.get("flipH") == "1", "fv": x.get("flipV") == "1"}
    co, ce = x.find("a:chOff", NS), x.find("a:chExt", NS)
    if co is not None and ce is not None:
        d.update(cx=px(co.get("x")), cy=px(co.get("y")), cw=px(ce.get("cx")), ch=px(ce.get("cy")))
    return d


def place(t):
    """translate + rotate about the centre + flips, for a box of t['w'] x t['h']."""
    s = "translate(%.2f,%.2f)" % (t["x"], t["y"])
    if t["rot"]:
        s += " rotate(%.3f %.2f %.2f)" % (t["rot"], t["w"] / 2, t["h"] / 2)
    if t["fh"]:
        s += " translate(%.2f,0) scale(-1,1)" % t["w"]
    if t["fv"]:
        s += " translate(0,%.2f) scale(1,-1)" % t["h"]
    return s


def adj(geom, name, default):
    for gd in geom.findall("a:avLst/a:gd", NS):
        if gd.get("name") == name:
            m = re.search(r"val\s+(-?\d+)", gd.get("fmla", ""))
            if m:
                return int(m.group(1))
    return default


def geometry(prst, w, h, geom):
    ss = min(w, h)
    if prst in ("rect", None):
        return '<rect x="0" y="0" width="%.2f" height="%.2f"' % (w, h)
    if prst == "roundRect":
        r = ss * adj(geom, "adj", 16667) / 100000.0
        return '<rect x="0" y="0" width="%.2f" height="%.2f" rx="%.2f"' % (w, h, r)
    if prst == "ellipse":
        return '<ellipse cx="%.2f" cy="%.2f" rx="%.2f" ry="%.2f"' % (w / 2, h / 2, w / 2, h / 2)
    if prst == "diamond":
        pts = [(w / 2, 0), (w, h / 2), (w / 2, h), (0, h / 2)]
    elif prst == "triangle":
        ax = w * adj(geom, "adj", 50000) / 100000.0
        pts = [(ax, 0), (w, h), (0, h)]
    elif prst == "plus":
        a = ss * adj(geom, "adj", 25000) / 100000.0
        pts = [(a, 0), (w - a, 0), (w - a, a), (w, a), (w, h - a), (w - a, h - a), (w - a, h), (a, h),
               (a, h - a), (0, h - a), (0, a), (a, a)]
    elif prst in ("rightArrow", "leftArrow"):
        a1 = adj(geom, "adj1", 50000) / 100000.0
        a2 = adj(geom, "adj2", 50000) / 100000.0
        head = ss * a2
        y1, y2 = h / 2 - h * a1 / 2, h / 2 + h * a1 / 2
        if prst == "rightArrow":
            pts = [(0, y1), (w - head, y1), (w - head, 0), (w, h / 2), (w - head, h), (w - head, y2), (0, y2)]
        else:
            pts = [(w, y1), (head, y1), (head, 0), (0, h / 2), (head, h), (head, y2), (w, y2)]
    else:
        return '<rect x="0" y="0" width="%.2f" height="%.2f"' % (w, h)
    return '<polygon points="%s"' % " ".join("%.2f,%.2f" % p for p in pts)


def stroke_of(book, sppr, node):
    ln = sppr.find("a:ln", NS) if sppr is not None else None
    idx, scol = style_ref(book, node, "lnRef")
    styled = idx not in (None, "0", "")
    if ln is None:
        if not styled:
            return None
        ln = ET.Element(A + "ln")
    if ln.find("a:noFill", NS) is not None:
        return None
    col = book.color(ln.find("a:solidFill", NS))
    if col is None:
        if styled:
            col = scol or "#000000"
        else:
            return None
    sw = book.ln_widths[int(idx) - 1] * EMU_PX if styled and 0 < int(idx) <= len(book.ln_widths) else 9525
    w = max(0.75, px(ln.get("w", str(sw))))
    dash = ln.find("a:prstDash", NS)
    da = ""
    if dash is not None and dash.get("val") not in (None, "solid"):
        v = dash.get("val")
        pat = {"sysDash": (3, 1), "dash": (4, 3), "sysDot": (1, 1), "dot": (1, 3), "lgDash": (8, 3),
               "dashDot": (4, 3, 1, 3), "sysDashDot": (3, 1, 1, 1)}.get(v, (3, 1))
        da = ' stroke-dasharray="%s"' % ",".join("%.2f" % (p * w) for p in pat)
    heads = {}
    for end in ("headEnd", "tailEnd"):
        e = ln.find("a:" + end, NS)
        if e is not None and e.get("type") not in (None, "none"):
            heads[end] = e.get("type")
    return {"color": col, "w": w, "dash": da, "heads": heads}


def text_html(book, txbody, w, h, default_color=None, scl=(1.0, 1.0)):
    """Text of a shape.  Excel draws text at its own point size even when the
    group around the shape was resized, so the text box is laid out at the
    box's on-sheet size (w*sx x h*sy) and scaled back by 1/sx, 1/sy — the group
    transforms then stretch the box but not the letters."""
    if txbody is None:
        return ""
    body = txbody.find("a:bodyPr", NS)
    ins = lambda k, d: px(body.get(k, d)) if body is not None else px(d)
    l, t, r, b = ins("lIns", "91440"), ins("tIns", "45720"), ins("rIns", "91440"), ins("bIns", "45720")
    anchor = (body.get("anchor") if body is not None else None) or "t"
    vert = body.get("vert") if body is not None else None
    paras, any_text = [], False
    for p in txbody.findall("a:p", NS):
        ppr = p.find("a:pPr", NS)
        algn = (ppr.get("algn") if ppr is not None else None) or "l"
        spans, psz = [], 0.0
        for run in p:
            tag = run.tag.replace(A, "")
            if tag == "br":
                spans.append("<br>")
                continue
            if tag != "r":
                continue
            rpr = run.find("a:rPr", NS)
            tx = run.findtext("a:t", default="", namespaces=NS)
            if tx.strip():
                any_text = True
            sz = int(rpr.get("sz", "1100")) / 100.0 if rpr is not None else 11.0
            psz = max(psz, sz)
            bold = rpr is not None and rpr.get("b") == "1"
            ital = rpr is not None and rpr.get("i") == "1"
            col = book.color(rpr.find("a:solidFill", NS)) if rpr is not None else None
            face = rpr.find("a:latin", NS) if rpr is not None else None
            face = face.get("typeface") if face is not None and not str(face.get("typeface", "")).startswith("+") else None
            st = "font-size:%.2fpx;%s%s%scolor:%s" % (sz * 96 / 72, "font-weight:700;" if bold else "",
                                                     "font-style:italic;" if ital else "",
                                                     ("font-family:'%s',Calibri,Carlito,Arial,sans-serif;" % face) if face else "",
                                                     col or default_color or "#000")
            spans.append('<span style="%s">%s</span>' % (st, html.escape(tx).replace(" ", "&#32;")))
        if not spans:
            end = p.find("a:endParaRPr", NS)
            sz = int(end.get("sz", "1100")) / 100.0 if end is not None else 11.0
            spans.append('<span style="font-size:%.2fpx">&#8203;</span>' % (sz * 96 / 72))
            psz = max(psz, sz)
        # the paragraph's own size sets the line box (a 16 px default strut
        # spread 7 pt text over twice its height and pushed it out of the box)
        paras.append('<div style="text-align:%s;font-size:%.2fpx">%s</div>'
                     % ({"ctr": "center", "r": "right", "just": "justify"}.get(algn, "left"),
                        (psz or 11.0) * 96 / 72, "".join(spans)))
    if not any_text:
        return ""
    jc = {"t": "flex-start", "ctr": "center", "b": "flex-end"}.get(anchor, "flex-start")
    wm = ""
    if vert in ("vert", "eaVert"):
        wm = "writing-mode:vertical-rl;"
    elif vert == "vert270":
        wm = "writing-mode:vertical-rl;transform:rotate(180deg);"
    elif vert == "wordArtVert":
        wm = "writing-mode:vertical-rl;text-orientation:upright;letter-spacing:-1px;"
    sx, sy = abs(scl[0]) or 1.0, abs(scl[1]) or 1.0
    fit = "" if (abs(sx - 1) < 0.01 and abs(sy - 1) < 0.01) else \
        "transform:scale(%.5f,%.5f);transform-origin:0 0;" % (1 / sx, 1 / sy)
    box = ('<div xmlns="http://www.w3.org/1999/xhtml" style="box-sizing:border-box;width:%.2fpx;height:%.2fpx;'
           'padding:%.2fpx %.2fpx %.2fpx %.2fpx;display:flex;flex-direction:column;justify-content:%s;'
           'overflow:hidden;font-family:Calibri,Carlito,Arial,sans-serif;line-height:1.12;%s">%s</div>'
           % (w * sx, h * sy, t, r, b, l, jc, wm, "".join(paras)))
    inner = ('<div xmlns="http://www.w3.org/1999/xhtml" style="width:%.2fpx;height:%.2fpx;%s">%s</div>'
             % (w * sx, h * sy, fit, box)) if fit else box
    return '<foreignObject x="0" y="0" width="%.2f" height="%.2f">%s</foreignObject>' % (w, h, inner)


def style_ref(book, node, ref):
    """(idx, colour) of a shape-style reference (lnRef / fillRef / fontRef).
    Shapes drawn with Excel's default style take their fill, outline and text
    colour from here (e.g. accent1 fill, darker accent1 outline, white text)."""
    st = node.find("xdr:style", NS)
    e = st.find("a:" + ref, NS) if st is not None else None
    if e is None:
        return None, None
    return e.get("idx"), book.color(e)


def render(book, node, out, scl=(1.0, 1.0)):
    tag = node.tag.replace(XDR, "")
    if tag == "grpSp":
        t = xfrm_of(node.find("xdr:grpSpPr", NS))
        if t is None:
            for ch in node:
                render(book, ch, out, scl)
            return
        sx = t["w"] / t["cw"] if t.get("cw") else 1
        sy = t["h"] / t["ch"] if t.get("ch") else 1
        tr = "translate(%.2f,%.2f)" % (t["x"], t["y"])
        if t["rot"]:
            tr += " rotate(%.3f %.2f %.2f)" % (t["rot"], t["w"] / 2, t["h"] / 2)
        if t["fh"]:
            tr += " translate(%.2f,0) scale(-1,1)" % t["w"]
        if t["fv"]:
            tr += " translate(0,%.2f) scale(1,-1)" % t["h"]
        tr += " scale(%.6f,%.6f) translate(%.2f,%.2f)" % (sx, sy, -t.get("cx", t["x"]), -t.get("cy", t["y"]))
        out.append('<g transform="%s">' % tr)
        for ch in node:
            if ch.tag.replace(XDR, "") in ("grpSp", "sp", "cxnSp", "pic"):
                render(book, ch, out, (scl[0] * sx, scl[1] * sy))
        out.append("</g>")
    elif tag in ("sp", "cxnSp"):
        sppr = node.find("xdr:spPr", NS)
        t = xfrm_of(sppr)
        if t is None:
            return
        geom = sppr.find("a:prstGeom", NS)
        prst = geom.get("prst") if geom is not None else None
        fill_idx, fill_col = style_ref(book, node, "fillRef")
        stroke = stroke_of(book, sppr, node)
        if sppr.find("a:noFill", NS) is not None:
            fill = "none"
        else:
            fill = (book.color(sppr.find("a:solidFill", NS))
                    or ((fill_col or "#ffffff") if fill_idx not in (None, "0", "") else "none"))
        out.append('<g transform="%s">' % place(t))
        w, h = t["w"], t["h"]
        if prst and prst.endswith("Connector1") or tag == "cxnSp":
            if stroke:
                m = ""
                if "tailEnd" in stroke["heads"]:
                    m += ' marker-end="url(#%s)"' % book.marker(stroke["heads"]["tailEnd"], stroke["color"])
                if "headEnd" in stroke["heads"]:
                    m += ' marker-start="url(#%s)"' % book.marker(stroke["heads"]["headEnd"], stroke["color"])
                out.append('<line x1="0" y1="0" x2="%.2f" y2="%.2f" stroke="%s" stroke-width="%.2f"%s%s/>'
                           % (w, h, stroke["color"], stroke["w"], stroke["dash"], m))
        else:
            sattr = (' stroke="%s" stroke-width="%.2f"%s' % (stroke["color"], stroke["w"], stroke["dash"])
                     if stroke else ' stroke="none"')
            out.append('%s fill="%s"%s/>' % (geometry(prst, w, h, geom if geom is not None else ET.Element("x")),
                                            fill, sattr))
        out.append("</g>")
        # Text: upright text keeps the box unrotated; otherwise it turns with the shape.
        tb = node.find("xdr:txBody", NS)
        if tb is not None:
            body = tb.find("a:bodyPr", NS)
            upright = body is not None and body.get("upright") == "1"
            tt = dict(t, fh=False, fv=False)
            if upright:
                tt["rot"] = 0
            frag = text_html(book, tb, w, h, style_ref(book, node, "fontRef")[1], scl)
            if frag:
                out.append('<g transform="%s">%s</g>' % (place(tt), frag))
    elif tag == "pic":
        sppr = node.find("xdr:spPr", NS)
        t = xfrm_of(sppr)
        blip = node.find(".//a:blip", NS)
        uri = book.image_uri(blip.get(R_EMBED)) if blip is not None else None
        if t is None or uri is None:
            return
        out.append('<g transform="%s"><image x="0" y="0" width="%.2f" height="%.2f" preserveAspectRatio="none" '
                   'href="%s"/></g>' % (place(t), t["w"], t["h"], uri))


def anchors(book):
    res = []
    for a in book.drawing:
        kind = a.tag.replace(XDR, "")
        fr = a.find("xdr:from", NS)
        row = int(fr.findtext("xdr:row", namespaces=NS)) if fr is not None else -1
        col = int(fr.findtext("xdr:col", namespaces=NS)) if fr is not None else -1
        shape = next((c for c in a if c.tag.replace(XDR, "") in ("grpSp", "sp", "cxnSp", "pic")), None)
        res.append((kind, row, col, shape))
    return res


def bbox_of(node):
    """Top-level box (px) of a shape/group."""
    tag = node.tag.replace(XDR, "")
    pr = node.find("xdr:grpSpPr" if tag == "grpSp" else "xdr:spPr", NS)
    return xfrm_of(pr)


def svg_for(book, nodes, pad=16, extra=""):
    book.markers = {}
    body = []
    boxes = []
    for n in nodes:
        render(book, n, body)
        b = bbox_of(n)
        if b:
            boxes.append(b)
    x0 = min(b["x"] for b in boxes) - pad
    y0 = min(b["y"] for b in boxes) - pad
    x1 = max(b["x"] + b["w"] for b in boxes) + pad
    y1 = max(b["y"] + b["h"] for b in boxes) + pad
    defs = "".join(m[1] for m in book.markers.values())
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="%.1f %.1f %.1f %.1f" width="%.0f" height="%.0f">'
            '<defs>%s</defs><rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="#fff"/>%s%s</svg>'
            % (x0, y0, x1 - x0, y1 - y0, x1 - x0, y1 - y0, defs, x0, y0, x1 - x0, y1 - y0, "".join(body), extra))


LEGEND_TEXT = [("SAFETY", 1), ("CRITICAL PROCESS", 3), ("IN PROCESS STOCK", 5), ("QUALITY CHECK", 7), ("OPERATOR", 9)]


def page(title, caption, layout_svg, legend_svg, scope="Seat Slider (except YRA-SS)"):
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>%(title)s - TOYOTA BOSHOKU DEVICE INDIA PVT. LTD.</title>
<style>
  * { box-sizing: border-box; font-family: "Calibri", "Carlito", Arial, sans-serif; }
  body { background: #e9ecef; margin: 0; padding: 12px; }
  .toolbar { max-width: 1960px; margin: 0 auto 10px auto; display: flex; justify-content: space-between;
             align-items: center; background: #fff; padding: 8px 15px; border: 1px solid #bbb; font-size: 13px; }
  .toolbar button { padding: 6px 14px; font-size: 13px; font-weight: bold; cursor: pointer; border: 1px solid #004085;
                    background: #0056b3; color: #fff; }
  .sheet { max-width: 1960px; margin: 0 auto; background: #fff; padding: 12px; box-shadow: 0 2px 8px rgba(0,0,0,.15); }
  .head { display: flex; align-items: stretch; border: 2px solid #000; border-bottom: none; }
  .head .logo { width: 150px; display: flex; align-items: center; justify-content: center; border-right: 2px solid #000; padding: 4px; }
  .head .logo .tb { text-align: center; line-height: 1; }
  .head .logo img { display: block; margin: 0 auto; height: 46px; max-width: 100%%; object-fit: contain; }
  .head .logo span { display: block; margin-top: 3px; font-size: 9px; font-weight: bold; color: #000; letter-spacing: .3px; white-space: nowrap; }
  .head .ttl { flex: 1; text-align: center; padding: 6px; }
  .head .ttl .c { font-size: 22px; font-weight: bold; }
  .head .ttl .s { font-size: 17px; font-weight: bold; }
  .head .meta { width: 220px; border-left: 2px solid #000; font-size: 13px; font-weight: bold; display: flex; flex-direction: column; }
  .head .meta div { flex: 1; display: flex; align-items: center; padding: 0 8px; }
  .head .meta div + div { border-top: 1px solid #000; }
  .frame { border: 2px solid #000; padding: 8px; overflow-x: auto; }
  .frame svg { display: block; width: 100%%; height: auto; }
  .legend { margin-top: 8px; }
  .lg { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 22px; border: 2px solid #000; padding: 8px 12px;
        font-size: 13px; font-weight: bold; }
  .lg-title { padding-right: 14px; border-right: 2px solid #000; margin-right: 4px; }
  .lg-item { display: flex; align-items: center; gap: 8px; white-space: nowrap; }
  .lg-item svg, .lg-item img { display: block; }
  @media print { body { background: #fff; padding: 0; } .toolbar { display: none; } .sheet { box-shadow: none; padding: 0; }
                 * { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; } }
</style>
</head>
<body>
<div class="toolbar"><div><strong>TOYOTA BOSHOKU - %(title)s</strong></div>
<div><button class="cb-pdf-btn" onclick="cbSavePdf()">Save PDF</button> <button onclick="window.print()">Print</button></div></div>
<div class="sheet">
  <div class="head">
    <div class="logo"><div class="tb"><img src="%(logo)s" alt="Toyota Boshoku"><span>TOYOTA BOSHOKU</span></div></div>
    <div class="ttl"><div class="c">TOYOTA BOSHOKU DEVICE INDIA PVT. LTD.</div><div class="s">%(title)s</div></div>
    <div class="meta"><div>%(caption)s</div><div>%(scope)s</div></div>
  </div>
  <div class="frame">%(layout)s</div>
  <div class="legend">%(legend)s</div>
</div>
<script src="cb-pdf.js?v=%(pdfv)s"></script>
</body>
</html>
""" % {"title": html.escape(title), "caption": (html.escape(caption) if caption else "&nbsp;"), "layout": layout_svg,
       "legend": legend_svg, "scope": html.escape(scope), "logo": LOGO_URI,
       "pdfv": _pdf_scripts()[2]}


def _pdf_scripts():
    """Save PDF scripts as installed (same as install_sheets.pdf_scripts): the
    render version goes into cb-pdf.js, and cb-pdf.js's own version into the tag,
    so browsers (scripts cached 1 h by serve_prod) load a changed one at once."""
    import hashlib
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "cb-pdf-render.js"), encoding="utf-8") as f:
        render = f.read()
    with open(os.path.join(here, "cb-pdf.js"), encoding="utf-8") as f:
        side = f.read().replace("__RENDER_V__", hashlib.md5(render.encode()).hexdigest()[:8])
    return side, render, hashlib.md5(side.encode()).hexdigest()[:8]


def legend_html(book, op=None):
    """Symbol legend as one tidy row (the sheet's own legend cells are too
    narrow for their labels).  Same symbols and colours as the layout; the
    operator icon is the workbook's own picture."""
    if op is None:
        op = book.image_uri(next((k for k, v in book.rels.items() if v.endswith("image2.png")), "")) or ""
    sym = {
        "SAFETY": '<svg width="22" height="22" viewBox="0 0 22 22"><polygon points="7,1 15,1 15,7 21,7 21,15 15,15 15,21 7,21 7,15 1,15 1,7 7,7" fill="#00b050" stroke="#006b30" stroke-width="1"/></svg>',
        "CRITICAL PROCESS": '<svg width="24" height="22" viewBox="0 0 24 22"><polygon points="1,1 23,1 12,21" fill="#ffff00" stroke="#000" stroke-width="1"/></svg>',
        "IN PROCESS STOCK": '<svg width="22" height="22" viewBox="0 0 22 22"><circle cx="11" cy="11" r="10" fill="#ff0000"/></svg>',
        "QUALITY CHECK": '<svg width="22" height="22" viewBox="0 0 22 22"><polygon points="11,1 21,11 11,21 1,11" fill="#fff" stroke="#ff0000" stroke-width="1.6"/></svg>',
        "OPERATOR": '<img src="%s" alt="" style="height:22px">' % op if op else "",
        "WALK": '<svg width="56" height="12" viewBox="0 0 56 12"><line x1="2" y1="6" x2="50" y2="6" stroke="#000" stroke-width="2"/><path d="M44,1 L54,6 L44,11" fill="none" stroke="#000" stroke-width="2"/></svg>',
        "RETURN WALK": '<svg width="56" height="12" viewBox="0 0 56 12"><line x1="6" y1="6" x2="54" y2="6" stroke="#000" stroke-width="2" stroke-dasharray="5,3"/><path d="M12,1 L2,6 L12,11" fill="none" stroke="#000" stroke-width="2"/></svg>',
        "PROCESS FLOW": '<svg width="56" height="22" viewBox="0 0 56 22"><polygon points="1,6 38,6 38,1 55,11 38,21 38,16 1,16" fill="#fff" stroke="#000" stroke-width="1" stroke-dasharray="3,1.5"/></svg>',
    }
    items = "".join('<div class="lg-item">%s<span>%s</span></div>' % (sym[k], k) for k in sym)
    return '<div class="lg"><div class="lg-title">SYMBOL LEGEND</div>%s</div>' % items


def sheet_texts(z, sheet):
    """{(row, col): text} of a worksheet (shared + inline strings), 1-based."""
    import os
    ss = []
    try:
        x = ET.fromstring(z.read("xl/sharedStrings.xml"))
        main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        ss = ["".join(t.text or "" for t in si.iter(main + "t")) for si in x.findall(main + "si")]
    except KeyError:
        pass
    raw = z.read("xl/worksheets/" + sheet).decode("utf-8")
    out = {}
    for ref, attrs, body in re.findall(r'<c r="([A-Z]+\d+)"([^>]*?)(?:/>|>(.*?)</c>)', raw, re.S):
        if not body:
            continue
        v = re.search(r"<v>([^<]*)</v>", body)
        t = re.search(r"<t[^>]*>([^<]*)</t>", body)
        txt = ss[int(v.group(1))] if ('t="s"' in attrs and v) else (t.group(1) if t else None)
        if txt:
            col = 0
            for ch in re.match(r"[A-Z]+", ref).group(0):
                col = col * 26 + ord(ch) - 64
            out[(int(re.search(r"\d+", ref).group(0)), col)] = html.unescape(txt)
    return out


# The Control Board layouts (operator files in Control_Board_Docs).  rows =
# 0-based anchor rows of the groups to draw (None = every shape on the sheet);
# the "As on" text is read from the sheet near those rows.
JOBS = [
    {"file": "SS Line Layout(1).xlsx", "drawing": "drawing1.xml", "sheet": "sheet1.xml", "rows": (40, 90),
     "title": "SEAT SLIDER LINE LAYOUT - WITHOUT ADVANCE BENDING", "scope": "Seat Slider (except YRA-SS)",
     "out": "seat-slider-line-layout.html"},
    {"file": "SS Line Layout(1).xlsx", "drawing": "drawing1.xml", "sheet": "sheet1.xml", "rows": (90, 140),
     "title": "SEAT SLIDER LINE LAYOUT - WITH ADVANCE BENDING", "scope": "Seat Slider (except YRA-SS)",
     "out": "seat-slider-line-layout-advance-bending.html"},
    {"file": "SS Line Layout(1).xlsx", "drawing": "drawing2.xml", "sheet": "sheet2.xml", "rows": None,
     "title": "YRA-SS LINE LAYOUT", "scope": "YRA-SS", "out": "yra-ss-line-layout.html"},
    {"file": "Rec line layout.xlsx", "drawing": "drawing1.xml", "sheet": "sheet1.xml", "rows": None,
     "title": "RECLINER LINE LAYOUT", "scope": "Recliner", "out": "recliner-line-layout.html"},
]
ICON_FROM = ("SS Line Layout(1).xlsx", "drawing1.xml")   # operator icon for the legend


def build(docs_dir, out_dir, jobs=JOBS):
    ib = Book("%s/%s" % (docs_dir, ICON_FROM[0]), ICON_FROM[1])
    icon = ib.image_uri(next((k for k, v in ib.rels.items() if v.endswith("image2.png")), "")) or ""
    for j in jobs:
        book = Book("%s/%s" % (docs_dir, j["file"]), j["drawing"])
        nodes = []
        for kind, row, col, shp in anchors(book):
            if shp is None:
                continue
            if shp.tag.endswith("pic"):          # skip non-raster pictures (EMF legend strips)
                blip = shp.find(".//a:blip", NS)
                if blip is None or book.image_uri(blip.get(R_EMBED)) is None:
                    continue
            if j["rows"] is None or (j["rows"][0] <= row < j["rows"][1] and shp.tag.endswith("grpSp")):
                nodes.append(shp)
        texts = sheet_texts(book.z, j["sheet"])
        lo, hi = (j["rows"] or (0, 10 ** 6))
        cap = next((t for (r, c), t in sorted(texts.items()) if lo - 5 <= r <= hi and t.lower().startswith("as on")), "")
        svg = svg_for(book, nodes)
        with open("%s/%s" % (out_dir, j["out"]), "w", encoding="utf-8") as f:
            f.write(page(j["title"], cap, svg, legend_html(book, icon), j["scope"]))
        print("wrote %s  (%d shapes, caption %r)" % (j["out"], len(nodes), cap))
    # Save PDF script shared with the operator sheets (2026-11-06)
    side, render, _v = _pdf_scripts()
    for js_name, js in (("cb-pdf.js", side), ("cb-pdf-render.js", render)):
        with open("%s/%s" % (out_dir, js_name), "w", encoding="utf-8") as f:
            f.write(js)


if __name__ == "__main__":
    # python3 xlsx_layout_to_html.py <Control_Board_Docs dir> <out dir>
    build(sys.argv[1], sys.argv[2])
