"""Draws a fictional sales report screenshot, dense with numbers, for demoing Data Masker.

Every figure is made up and internally consistent (rows sum to totals, variances match).
"""
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
W, H = 1920, 1080
F = "C:/Windows/Fonts/segoeui.ttf"
FB = "C:/Windows/Fonts/segoeuib.ttf"
FS = "C:/Windows/Fonts/seguisb.ttf"

THEMES = {
    "light": dict(bg="#F3F4F6", card="#FFFFFF", text="#1F2328", muted="#6B7280", line="#E5E7EB", head="#1E3A5F",
                  headtext="#FFFFFF", band="#F8FAFC", bar="#2F6FED", bar2="#9DB8F5", pos="#15803D", neg="#B91C1C"),
    "dark": dict(bg="#111418", card="#1A1F26", text="#E6E8EB", muted="#8B95A1", line="#2A313A", head="#0E2238",
                 headtext="#E6E8EB", band="#1E242C", bar="#4C8DFF", bar2="#2D4C80", pos="#4ADE80", neg="#F87171"),
}


def font(path, size):
    return ImageFont.truetype(path, size)


def money(v, d=0):
    return f"${v:,.{d}f}"


def signed(v, d=0, pre=""):
    return f"{'-' if v < 0 else ''}{pre}{abs(v):,.{d}f}"


def pct(v, d=1, sign=False):
    return f"{'+' if sign and v > 0 else ''}{v:.{d}f}%"


def draw(theme: str) -> Image.Image:
    t = THEMES[theme]
    rnd = random.Random(7)
    im = Image.new("RGB", (W, H), t["bg"])
    d = ImageDraw.Draw(im)

    def card(x, y, w, h, title=None):
        d.rounded_rectangle([x, y, x + w, y + h], 8, fill=t["card"], outline=t["line"])
        if title:
            d.text((x + 18, y + 14), title, fill=t["text"], font=font(FS, 17))

    def rtext(x, y, s, f, fill):
        d.text((x - d.textlength(s, font=f), y), s, fill=fill, font=f)

    # header
    d.rectangle([0, 0, W, 76], fill=t["head"])
    d.text((28, 16), "Harbor & Pine Co.", fill=t["headtext"], font=font(FB, 28))
    d.text((300, 24), "Sales Performance Overview", fill=t["headtext"], font=font(F, 22))
    for i, (lab, val) in enumerate([("Fiscal year", "FY2026"), ("Quarter", "Q3"), ("Channel", "All")]):
        x = 1290 + i * 205
        d.text((x, 10), lab, fill="#B9C6D6", font=font(F, 13))
        d.rounded_rectangle([x, 32, x + 185, 62], 4, fill="#FFFFFF" if theme == "light" else "#1A2B40")
        d.text((x + 10, 37), val, fill="#1F2328" if theme == "light" else t["text"], font=font(F, 15))

    # regional figures first, so the revenue card matches the table total
    regions = ["North", "South", "East", "West", "Central", "Online"]
    rows = []
    for r in regions:
        rev = rnd.randint(1_100_000, 3_200_000)
        bud = int(rev * rnd.uniform(0.86, 1.12))
        units = rev // rnd.randint(180, 320)
        mg = rnd.uniform(29, 46)
        rows.append((r, rev, bud, rev - bud, (rev - bud) / bud * 100, units, mg))

    # KPI cards
    kpis = [("Total Revenue", money(sum(r[1] for r in rows)), 6.2), ("Gross Margin", "38.4%", 1.8), ("Orders", "48,213", -2.4),
            ("Avg Order Value", money(259.00, 2), 8.7), ("Returns", "1,284", -11.3), ("Active Customers", "17,906", 4.1)]
    kw = (W - 56 - 5 * 16) // 6
    for i, (lab, val, dlt) in enumerate(kpis):
        x = 28 + i * (kw + 16)
        card(x, 96, kw, 128)
        d.text((x + 18, 110), lab, fill=t["muted"], font=font(F, 15))
        d.text((x + 18, 134), val, fill=t["text"], font=font(FB, 36))
        col = t["pos"] if dlt > 0 else t["neg"]
        d.text((x + 18, 188), f"{'▲' if dlt > 0 else '▼'} {pct(abs(dlt))} vs PY", fill=col, font=font(F, 14))

    # regional table
    tot = ("Total", sum(r[1] for r in rows), sum(r[2] for r in rows), 0, 0, sum(r[5] for r in rows), 0)
    tot = (tot[0], tot[1], tot[2], tot[1] - tot[2], (tot[1] - tot[2]) / tot[2] * 100, tot[5],
           sum(r[1] * r[6] for r in rows) / tot[1])
    tx, ty, tw, th = 28, 244, 930, 380
    card(tx, ty, tw, th, "Revenue vs Budget by Region")
    cols = [("Region", 46, "l"), ("Revenue", 330, "r"), ("Budget", 470, "r"), ("Variance", 610, "r"), ("Var %", 700, "r"),
            ("Units", 800, "r"), ("Margin %", 905, "r")]
    hy = ty + 54
    d.rectangle([tx + 1, hy - 6, tx + tw - 1, hy + 28], fill=t["band"])
    for name, x, a in cols:
        f = font(FS, 15)
        (rtext(tx + x, hy, name, f, t["muted"]) if a == "r" else d.text((tx + x - 18, hy), name, fill=t["muted"], font=f))
    for i, r in enumerate(rows + [tot]):
        y = hy + 44 + i * 42
        bold = r[0] == "Total"
        if bold:
            d.line([tx + 18, y - 10, tx + tw - 18, y - 10], fill=t["muted"], width=1)
        f = font(FB if bold else F, 16)
        vals = [r[0], money(r[1]), money(r[2]), signed(r[3], pre="$"), pct(r[4], sign=True), f"{r[5]:,}", pct(r[6])]
        for (name, x, a), v in zip(cols, vals):
            fill = t["text"]
            if name in ("Variance", "Var %"):
                fill = t["pos"] if r[3] >= 0 else t["neg"]
            (rtext(tx + x, y, v, f, fill) if a == "r" else d.text((tx + x - 18, y), v, fill=fill, font=f))
        if not bold and i < len(rows) - 1:
            d.line([tx + 18, y + 31, tx + tw - 18, y + 31], fill=t["line"])

    # monthly bars
    bx, by, bw, bh = 974, 244, 918, 380
    card(bx, by, bw, bh, "Monthly Revenue (FY2026)")
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    vals = [rnd.uniform(0.72, 1.38) for _ in months]
    py = [v * rnd.uniform(0.82, 1.05) for v in vals]
    top, base = by + 80, by + bh - 46
    vmax = 1.5
    for k in range(4):
        gv = vmax * k / 3
        gy = base - (base - top) * gv / vmax
        d.line([bx + 70, gy, bx + bw - 20, gy], fill=t["line"])
        rtext(bx + 58, gy - 9, f"{gv:.1f}M", font(F, 13), t["muted"])
    slot = (bw - 100) / 12
    for i, (m, v, p) in enumerate(zip(months, vals, py)):
        x = bx + 80 + i * slot
        for j, (val, colr) in enumerate([(p, t["bar2"]), (v, t["bar"])]):
            h = (base - top) * val / vmax
            d.rectangle([x + j * 26, base - h, x + j * 26 + 22, base], fill=colr)
        lab = f"{v:.2f}M"
        f = font(F, 12)
        d.text((x + 37 - d.textlength(lab, font=f) / 2, base - (base - top) * v / vmax - 20), lab, fill=t["text"], font=f)
        d.text((x + 24 - d.textlength(m, font=font(F, 13)) / 2, base + 8), m, fill=t["muted"], font=font(F, 13))
    d.rectangle([bx + bw - 250, by + 20, bx + bw - 238, by + 32], fill=t["bar"])
    d.text((bx + bw - 232, by + 16), "FY2026", fill=t["muted"], font=font(F, 13))
    d.rectangle([bx + bw - 160, by + 20, bx + bw - 148, by + 32], fill=t["bar2"])
    d.text((bx + bw - 142, by + 16), "FY2025", fill=t["muted"], font=font(F, 13))

    # top products
    px, pyy, pw, ph = 28, 644, 620, 412
    card(px, pyy, pw, ph, "Top Products by Revenue")
    prods = ["Trail Pack 40L", "Summit Jacket", "Ridge Boots", "Alpine Tent 2P", "Canyon Bottle", "Drift Kayak", "Pine Hammock"]
    pv = sorted([rnd.randint(380_000, 1_640_000) for _ in prods], reverse=True)
    for i, (n, v) in enumerate(zip(prods, pv)):
        y = pyy + 62 + i * 49
        d.text((px + 18, y + 4), n, fill=t["text"], font=font(F, 15))
        L = (pw - 330) * v / pv[0]
        d.rectangle([px + 170, y + 3, px + 170 + L, y + 27], fill=t["bar"])
        d.text((px + 180 + L, y + 4), money(v), fill=t["text"], font=font(F, 14))

    # category matrix
    mx, my, mw, mh = 668, 644, 760, 412
    card(mx, my, mw, mh, "Quarterly Units by Category")
    cats = ["Apparel", "Footwear", "Camping", "Water", "Accessories"]
    qs = ["Q1", "Q2", "Q3", "Q4", "Total"]
    xs = [mx + 230, mx + 340, mx + 450, mx + 560, mx + 700]
    hy = my + 56
    d.rectangle([mx + 1, hy - 6, mx + mw - 1, hy + 28], fill=t["band"])
    d.text((mx + 28, hy), "Category", fill=t["muted"], font=font(FS, 15))
    for q, x in zip(qs, xs):
        rtext(x, hy, q, font(FS, 15), t["muted"])
    colt = [0] * 4
    for i, c in enumerate(cats):
        y = hy + 44 + i * 44
        q = [rnd.randint(2_100, 9_800) for _ in range(4)]
        colt = [a + b for a, b in zip(colt, q)]
        d.text((mx + 28, y), c, fill=t["text"], font=font(F, 16))
        for v, x in zip(q + [sum(q)], xs):
            rtext(x, y, f"{v:,}", font(FB if x == xs[-1] else F, 16), t["text"])
    y = hy + 44 + len(cats) * 44 + 6
    d.line([mx + 18, y - 10, mx + mw - 18, y - 10], fill=t["muted"])
    d.text((mx + 28, y), "Total", fill=t["text"], font=font(FB, 16))
    for v, x in zip(colt + [sum(colt)], xs):
        rtext(x, y, f"{v:,}", font(FB, 16), t["text"])

    # small facts
    sx, sy, sw, sh = 1448, 644, 444, 412
    card(sx, sy, sw, sh, "Fulfilment")
    facts = [("Orders shipped", "46,927"), ("On-time delivery", "94.6%"), ("Avg days to ship", "1.8"),
             ("Backorders", "312"), ("Freight cost", "$418,205"), ("Cost per order", "$8.91"), ("NPS", "62")]
    for i, (k, v) in enumerate(facts):
        y = sy + 62 + i * 48
        d.text((sx + 20, y), k, fill=t["muted"], font=font(F, 16))
        rtext(sx + sw - 22, y - 2, v, font(FB, 19), t["text"])
        if i < len(facts) - 1:
            d.line([sx + 20, y + 34, sx + sw - 20, y + 34], fill=t["line"])

    d.text((28, H - 22), "Data refreshed 09/10/2026 06:00  ·  Fictional data for demonstration", fill=t["muted"], font=font(F, 12))
    return im


if __name__ == "__main__":
    for th in ("light", "dark"):
        p = HERE / f"Showcase Report ({th}).png"
        draw(th).save(p)
        print(p.name)
