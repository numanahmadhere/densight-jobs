"""Job market analysis PDF, emailed after every run that finds new jobs.

Run:  python -m pipeline.analysis
Reads data/jobs.csv (full history) and data/last_run.json (what the latest run found).
All numbers come from the data; the "key takeaways" are rule-based sentences that are only
written when the numbers are large enough to support them.
"""
import json
import os
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.platypus import (HRFlowable, Image, KeepTogether, PageBreak, Paragraph,  # noqa: E402
                                SimpleDocTemplate, Spacer, Table, TableStyle)
from reportlab.platypus.flowables import Flowable  # noqa: E402

from . import sheets  # noqa: E402
from .classify import CONFIG, ROOT  # noqa: E402

DATA = ROOT / "data" / "jobs.csv"
RUN = ROOT / "data" / "last_run.json"
OUT = ROOT / "out_report"

# ---------------------------------------------------------------- Densight palette
NAVY = colors.HexColor('#0f172a')
BLUE = colors.HexColor('#1D4ED8')
PURPLE = colors.HexColor('#6D28D9')
GREEN = colors.HexColor('#047857')
AMBER = colors.HexColor('#B45309')
SLATE = colors.HexColor('#64748b')
BORDER = colors.HexColor('#e2e8f0')
LIGHT = colors.HexColor('#f8fafc')
ACCENT = colors.HexColor('#EFF6FF')
WHITE = colors.white
ACCENTS = [BLUE, PURPLE, GREEN, AMBER]
HEX = {BLUE: '#1D4ED8', PURPLE: '#6D28D9', GREEN: '#047857', AMBER: '#B45309'}

SOURCE_NAMES = {"linkedin": "LinkedIn", "indeed": "Indeed", "greenhouse": "Company careers",
                "lever": "Company careers", "workable": "Company careers",
                "remoteok": "RemoteOK", "weworkremotely": "We Work Remotely"}
PK = "Pakistan"
REMOTE = "Remote (global)"
NO_CITY = "Pakistan (unspecified)"


def make_styles():
    def s(name, **kw):
        return ParagraphStyle(name, **kw)
    ink = colors.HexColor('#1e293b')
    return {
        'cover_label': s('cover_label', fontName='Helvetica-Bold', fontSize=10, textColor=colors.HexColor('#93c5fd'),
                         leading=14, spaceAfter=10),
        'cover_title': s('cover_title', fontName='Helvetica-Bold', fontSize=28, textColor=WHITE,
                         leading=34, spaceAfter=8, alignment=TA_LEFT),
        'cover_sub': s('cover_sub', fontName='Helvetica', fontSize=13, textColor=colors.HexColor('#cbd5e1'),
                       leading=18, spaceAfter=6, alignment=TA_LEFT),
        'cover_meta': s('cover_meta', fontName='Helvetica', fontSize=11, textColor=colors.HexColor('#94a3b8'),
                        leading=16, alignment=TA_LEFT),
        'tile_num': s('tile_num', fontName='Helvetica-Bold', fontSize=20, textColor=WHITE, leading=24,
                      alignment=TA_LEFT),
        'tile_lbl': s('tile_lbl', fontName='Helvetica', fontSize=8.5, textColor=colors.HexColor('#cbd5e1'),
                      leading=11, alignment=TA_LEFT),
        'h2': s('h2', fontName='Helvetica-Bold', fontSize=12, textColor=NAVY, leading=16, spaceBefore=12,
                spaceAfter=4),
        'body': s('body', fontName='Helvetica', fontSize=10, textColor=ink, leading=15, spaceAfter=6,
                  alignment=TA_JUSTIFY),
        'body_white': s('body_white', fontName='Helvetica', fontSize=10.5, textColor=WHITE, leading=16,
                        spaceAfter=2),
        'bullet': s('bullet', fontName='Helvetica', fontSize=10, textColor=ink, leading=15, spaceAfter=4,
                    leftIndent=14, firstLineIndent=-10),
        'cell': s('cell', fontName='Helvetica', fontSize=8.5, textColor=NAVY, leading=11),
        'cell_head': s('cell_head', fontName='Helvetica-Bold', fontSize=8.5, textColor=WHITE, leading=11),
        'note': s('note', fontName='Helvetica-Oblique', fontSize=9, textColor=SLATE, leading=13, spaceAfter=6),
        'caption': s('caption', fontName='Helvetica', fontSize=9, textColor=SLATE, leading=13, spaceAfter=4,
                     alignment=TA_CENTER),
        'footer': s('footer', fontName='Helvetica', fontSize=8, textColor=SLATE, leading=12, alignment=TA_CENTER),
    }


class SectionHeader(Flowable):
    def __init__(self, number, title, badge_text, color=BLUE):
        super().__init__()
        self.number, self.title, self.badge, self.color = number, title, badge_text, color
        self._height = 56

    def wrap(self, availW, availH):
        self.avail_w = availW
        return availW, self._height

    def draw(self):
        c = self.canv
        c.setFillColor(self.color)
        c.roundRect(0, 0, self.avail_w, self._height, 5, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont('Helvetica-Bold', 9)
        c.drawString(14, self._height - 18, 'SECTION ' + self.number)
        c.setFont('Helvetica-Bold', 16)
        c.drawString(14, self._height - 36, self.title)
        bw = 92
        c.setFillColor(colors.Color(0, 0, 0, alpha=0.19))
        c.roundRect(self.avail_w - bw - 12, 14, bw, 22, 4, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont('Helvetica-Bold', 9)
        c.drawCentredString(self.avail_w - bw / 2 - 12, 21, self.badge)


# ---------------------------------------------------------------- helpers
def esc(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def pct(a, b):
    return None if not b else round((a - b) / b * 100)


def share(n, d):
    return 0 if not d else round(n / d * 100)


def skill_counts(df):
    c = Counter(s.strip() for row in df["skills"] for s in str(row).split(",") if s.strip())
    return pd.Series(c, dtype=int).sort_values(ascending=False)


def callout(story, text, ST, dark=False, border=BLUE):
    t = Table([[Paragraph(text, ST['body_white'] if dark else ST['body'])]], colWidths=['100%'])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), NAVY if dark else ACCENT),
        ('LEFTPADDING', (0, 0), (-1, -1), 14), ('RIGHTPADDING', (0, 0), (-1, -1), 14),
        ('TOPPADDING', (0, 0), (-1, -1), 10), ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('LINEBEFORE', (0, 0), (0, -1), 3, NAVY if dark else border),
        ('ROUNDEDCORNERS', [4, 4, 4, 4]),
    ]))
    story += [t, Spacer(1, 8)]


def table(rows, header, widths, ST):
    data = [[Paragraph(esc(h), ST['cell_head']) for h in header]]
    for r in rows:
        data.append([c if isinstance(c, Paragraph) else Paragraph(esc(c), ST['cell']) for c in r])
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, LIGHT]),
        ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 7),
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('ROUNDEDCORNERS', [4, 4, 4, 4]),
    ]))
    return t


def bar_chart(series, path, color, pct_labels=False, top=10, height=6.2):
    s = series.head(top)[::-1]
    fig, ax = plt.subplots(figsize=(17 / 2.54, height / 2.54), dpi=200)
    if s.empty:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", color="#64748b", transform=ax.transAxes)
        ax.axis("off")
    else:
        ax.barh(range(len(s)), s.values, height=0.6, color=color)
        ax.set_yticks(range(len(s)))
        ax.set_yticklabels([str(i)[:30] for i in s.index], fontsize=7.5, color="#0f172a")
        mx = s.max() or 1
        ax.set_xlim(0, mx * 1.18)
        for i, v in enumerate(s.values):
            ax.text(v + mx * 0.012, i, f"{v:.0f}%" if pct_labels else f"{int(v)}", va="center",
                    fontsize=7.5, color="#0f172a", fontweight="bold")
        for sp in ("top", "right", "left"):
            ax.spines[sp].set_visible(False)
        ax.spines["bottom"].set_color("#e2e8f0")
        ax.tick_params(axis="x", labelsize=7, colors="#64748b")
        ax.tick_params(axis="y", length=0)
        ax.xaxis.grid(True, color="#e2e8f0", linewidth=0.6)
        ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(path, facecolor="white")
    plt.close(fig)


def line_chart(series, path, color, ylabel, height=6.0, pct_axis=False):
    fig, ax = plt.subplots(figsize=(17 / 2.54, height / 2.54), dpi=200)
    if series.empty:
        ax.text(0.5, 0.5, "Not enough history yet", ha="center", va="center", color="#64748b",
                transform=ax.transAxes)
        ax.axis("off")
    else:
        x = range(len(series))
        ax.plot(x, series.values, color=color, linewidth=2, marker="o", markersize=5)
        last = series.values[-1]
        ax.annotate(f"{last:.0f}{'%' if pct_axis else ''}", (len(series) - 1, last), textcoords="offset points",
                    xytext=(0, 8), ha="center", fontsize=8, fontweight="bold", color="#0f172a")
        ax.set_xticks(list(x))
        ax.set_xticklabels(series.index, fontsize=7, color="#64748b")
        ax.set_ylim(0, max(series.max() * 1.3, 1))
        ax.set_ylabel(ylabel, fontsize=7.5, color="#64748b")
        ax.tick_params(axis="y", labelsize=7, colors="#64748b")
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color("#e2e8f0")
        ax.yaxis.grid(True, color="#e2e8f0", linewidth=0.6)
        ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(path, facecolor="white")
    plt.close(fig)


def img(path, W, height_cm):
    return Image(str(path), width=W, height=height_cm * cm)


# ---------------------------------------------------------------- analysis
def analyse(hist, run):
    today = date.fromisoformat(run["date"])
    hist = hist.copy()
    hist["market"] = hist.get("market", PK).replace("", PK) if "market" in hist else PK
    hist["d"] = pd.to_datetime(hist["first_seen"], errors="coerce").dt.date
    hist = hist[hist["d"].notna()]
    pk = hist[hist["market"] == PK]
    rem = hist[hist["market"] == REMOTE]

    def win(df, a, b):  # inclusive day offsets back from today
        return df[(df["d"] >= today - timedelta(days=a)) & (df["d"] <= today - timedelta(days=b))]

    ts = date.fromisoformat(str(CONFIG.get("tracking_start") or "2000-01-01"))
    first_data = pk["d"].min() if len(pk) else today
    cur, prev = win(pk, 6, 0), win(pk, 13, 7)
    prior28 = win(pk, 34, 7)
    last28, before28 = win(pk, 27, 0), win(pk, 55, 28)
    new_ids = set(run.get("new_ids", []))
    new = hist[hist["job_id"].isin(new_ids)]

    # weekly series (Monday-start weeks, last 8). Drop a first week that data only partly covers.
    wk_start = pk["d"].apply(lambda d: d - timedelta(days=d.weekday()))
    weekly = pk.groupby(wk_start).size().sort_index()
    genai_w = (pk.assign(g=pk["category"] == "GenAI / LLM").groupby(wk_start)["g"].mean() * 100).sort_index()
    full = [w for w in weekly.index if w >= first_data]
    weekly, genai_w = weekly.loc[full].tail(8), genai_w.loc[full].tail(8)
    backfilled_weeks = [w for w in weekly.index if w < ts]
    weekly.index = [f"{d.day} {d:%b}" for d in weekly.index]
    genai_w.index = [f"{d.day} {d:%b}" for d in genai_w.index]

    sk_cur = skill_counts(cur)
    sk_cur_pct = (sk_cur / max(len(cur), 1) * 100).round()
    movers = pd.DataFrame()
    if len(prior28) >= 30 and len(cur) >= 20 and first_data <= today - timedelta(days=34):
        sk_prior = skill_counts(prior28) / len(prior28) * 100
        idx = sorted(set(sk_cur.index) | set(sk_prior.index))
        m = pd.DataFrame({"now": [sk_cur_pct.get(i, 0) for i in idx],
                          "before": [round(sk_prior.get(i, 0)) for i in idx],
                          "count": [int(sk_cur.get(i, 0)) for i in idx]}, index=idx)
        m["change"] = m["now"] - m["before"]
        movers = m[(m["count"] >= 5) & (m["change"].abs() >= 5)].sort_values("change", ascending=False)

    enough_history = first_data <= today - timedelta(days=34)
    first_ever = pk.groupby("company_norm")["d"].min()
    entrants = cur[cur["company_norm"].isin(first_ever[first_ever >= today - timedelta(days=6)].index)]
    entrants = entrants.groupby("company").size().sort_values(ascending=False)
    if not enough_history:
        entrants = entrants.iloc[0:0]

    ramp = pd.DataFrame()
    if len(before28) >= 30:
        a = last28.groupby("company").size()
        b = before28.groupby("company").size()
        r = pd.DataFrame({"last_28d": a, "previous_28d": b}).fillna(0).astype(int)
        ramp = r[(r["last_28d"] >= 3) & (r["previous_28d"] >= 1) & (r["last_28d"] >= 2 * r["previous_28d"])]
        ramp = ramp.sort_values("last_28d", ascending=False).head(10)

    g = last28.groupby("company")
    leads = pd.DataFrame({
        "roles_7d": cur.groupby("company").size(),
        "roles_28d": g.size(),
        "categories": g["category"].agg(lambda s: ", ".join(sorted(set(s)))),
        "cities": g["city"].agg(lambda s: ", ".join(sorted(set(c for c in s if c != NO_CITY))) or "Not listed"),
        "co_size": g["company_size"].agg(lambda s: next((x for x in s if x), "")) if "company_size" in last28 else "",
        "co_revenue": g["company_revenue"].agg(lambda s: next((x for x in s if x), "")) if "company_revenue" in last28 else "",
    }).fillna({"roles_7d": 0}).dropna(subset=["roles_28d"])
    leads["roles_7d"] = leads["roles_7d"].astype(int)
    leads["roles_28d"] = leads["roles_28d"].astype(int)
    leads["signal"] = pd.cut(leads["roles_28d"], [0, 2, 4, 1e9], labels=["Warm", "Hot", "Very hot"]).astype(str)
    leads = leads[leads["roles_28d"] >= 3].sort_values(["roles_28d", "roles_7d"], ascending=False)

    cities_all = cur["city"].value_counts()
    cities = cities_all.drop(labels=[NO_CITY], errors="ignore")
    return dict(today=today, pk=pk, cur=cur, prev=prev, new=new, rem_cur=win(rem, 6, 0),
                weekly=weekly, genai_w=genai_w, sk_cur=sk_cur, sk_cur_pct=sk_cur_pct, movers=movers,
                entrants=entrants, ramp=ramp, leads=leads, cities=cities,
                no_city=int(cities_all.get(NO_CITY, 0)), cats=cur["category"].value_counts(),
                levels=cur["seniority"].value_counts(), companies=cur.groupby("company").size()
                .sort_values(ascending=False),
                enough_prev=len(prev) >= 10 and today - timedelta(days=13) >= ts,
                enough_history=enough_history, ts=ts,
                backfilled_weeks=[f"{w.day} {w:%b}" for w in backfilled_weeks])


def takeaways(a):
    """Rule-based sentences. Each is only written when the data clearly supports it."""
    out = []
    n, p = len(a["cur"]), len(a["prev"])
    if a["enough_prev"]:
        ch = pct(n, p)
        word = "up" if ch > 0 else "down" if ch < 0 else "flat"
        out.append(f"<b>{n}</b> new AI and data roles in the last 7 days, <b>{word} {abs(ch)}%</b> "
                   f"on the previous 7 days ({p}).")
    else:
        out.append(f"<b>{n}</b> new AI and data roles in the last 7 days. Tracking is new, so there is "
                   f"not yet enough history for a reliable week-on-week comparison.")
    if n >= 20:
        g_now = share((a["cur"]["category"] == "GenAI / LLM").sum(), n)
        if len(a["prev"]) >= 20 and a["enough_prev"]:
            g_before = share((a["prev"]["category"] == "GenAI / LLM").sum(), len(a["prev"]))
            if abs(g_now - g_before) >= 5:
                out.append(f"GenAI and LLM roles made up <b>{g_now}%</b> of postings, "
                           f"{'up' if g_now > g_before else 'down'} from {g_before}% the week before.")
            else:
                out.append(f"GenAI and LLM roles held steady at about <b>{g_now}%</b> of postings.")
        else:
            out.append(f"GenAI and LLM roles made up <b>{g_now}%</b> of postings.")
    if len(a["cities"]):
        top, cnt = a["cities"].index[0], int(a["cities"].iloc[0])
        named = int(a["cities"].sum())
        if named >= 10:
            out.append(f"<b>{top}</b> led hiring with {cnt} roles, {share(cnt, named)}% of roles that listed a city.")
    if len(a["sk_cur"]) and n >= 15:
        out.append(f"<b>{a['sk_cur'].index[0]}</b> was the most requested skill, named in "
                   f"{int(a['sk_cur_pct'].iloc[0])}% of postings.")
    if len(a["movers"]):
        up = a["movers"][a["movers"]["change"] > 0].head(1)
        if len(up):
            out.append(f"Fastest rising skill: <b>{up.index[0]}</b>, now in {int(up['now'].iloc[0])}% of "
                       f"postings vs {int(up['before'].iloc[0])}% over the prior 4 weeks.")
    if len(a["companies"]) and int(a["companies"].iloc[0]) >= 3:
        out.append(f"<b>{a['companies'].index[0]}</b> was the most active employer with "
                   f"{int(a['companies'].iloc[0])} new roles.")
    if len(a["entrants"]) >= 3:
        out.append(f"<b>{len(a['entrants'])}</b> companies posted AI or data roles for the first time since "
                   f"tracking began. These are fresh sales leads.")
    entry = int(a["cur"]["seniority"].isin(["Entry", "Internship"]).sum())
    if n >= 20:
        out.append(f"Entry-level and internship roles were <b>{share(entry, n)}%</b> of postings ({entry} roles).")
    return out[:6]


# ---------------------------------------------------------------- PDF
def build_pdf(a, run, path):
    ST = make_styles()
    W = A4[0] - 4 * cm
    charts = OUT / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    nice = f"{a['today'].day} {a['today']:%b %Y}"
    n7 = len(a["cur"])

    def cover_bg(c, doc):
        c.saveState()
        c.setFillColor(NAVY)
        c.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
        c.restoreState()

    def later(c, doc):
        c.saveState()
        c.setFont('Helvetica', 8)
        c.setFillColor(SLATE)
        c.drawString(2 * cm, 1.2 * cm, f"AI Jobs in Pakistan | Market analysis | {nice}")
        c.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Densight Labs | Page {doc.page}")
        c.restoreState()

    story = []
    # ---- cover
    story += [Spacer(1, 3 * cm),
              Paragraph("AI JOBS IN PAKISTAN | MARKET ANALYSIS", ST['cover_label']),
              Paragraph("The AI hiring market,<br/>this week", ST['cover_title']),
              Paragraph(f"Rolling 7-day view to {nice}, with trends and leads", ST['cover_sub']),
              Spacer(1, 6), HRFlowable(width='40%', thickness=2, color=BLUE, hAlign='LEFT', spaceAfter=18)]
    remote_n = len(a["rem_cur"])
    tiles = [(len(a["new"]), "new roles found in this run"),
             (n7, "new roles in the last 7 days"),
             (a["cur"]["company_norm"].nunique(), "companies hiring (7 days)"),
             (remote_n, "remote roles open to Pakistan")]
    ch = pct(n7, len(a["prev"])) if a["enough_prev"] else None
    if ch is not None:
        tiles[1] = (n7, f"new roles in 7 days ({'+' if ch >= 0 else ''}{ch}% vs prior week)")
    tt = Table([[[Paragraph(str(v), ST['tile_num']), Paragraph(esc(l), ST['tile_lbl'])] for v, l in tiles]],
               colWidths=[W / 4] * 4)
    tt.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#1e293b')),
        ('LINEBEFORE', (1, 0), (-1, -1), 6, NAVY),
        ('LEFTPADDING', (0, 0), (-1, -1), 10), ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROUNDEDCORNERS', [6, 6, 6, 6]),
    ]))
    story += [tt, Spacer(1, 22),
              Paragraph(f"Run: {esc(run['run_at'])} PKT ({esc(run['mode'])})", ST['cover_meta']),
              Paragraph("Prepared by Densight Labs | densight.com", ST['cover_meta']),
              Paragraph("Sources: LinkedIn, Indeed, company career pages, RemoteOK, We Work Remotely",
                        ST['cover_meta']),
              PageBreak()]

    # ---- 1 takeaways
    story += [SectionHeader("1", "Key takeaways", "LAST 7 DAYS", BLUE), Spacer(1, 16)]
    for t in takeaways(a):
        story.append(Paragraph('<bullet>&bull;</bullet> ' + t, ST['bullet']))
    story.append(Spacer(1, 8))
    callout(story, "All figures cover roles based in Pakistan. Remote roles from global boards are reported "
                   "separately in Section 8 and are not included in the Pakistan numbers.", ST)
    story.append(PageBreak())

    # ---- 2 demand trend
    line_chart(a["weekly"], charts / "weekly.png", HEX[PURPLE], "new roles", height=6.0)
    line_chart(a["genai_w"].round(), charts / "genai.png", HEX[PURPLE], "% of roles", pct_axis=True, height=6.0)
    story += [SectionHeader("2", "Demand trend", "WEEKLY", PURPLE), Spacer(1, 16),
              KeepTogether([Paragraph("New AI and data roles per week", ST['h2']), img(charts / "weekly.png", W, 6.0)]),
              Paragraph("Weeks start on Monday. The latest week may still be in progress."
                        + (f" Weeks starting {', '.join(a['backfilled_weeks'])} include days from a one-time backfill, which "
                           f"undercounts jobs that had already closed, so compare them with care."
                           if a['backfilled_weeks'] else ""), ST['note']),
              KeepTogether([Paragraph("GenAI and LLM roles as a share of all roles", ST['h2']),
                            img(charts / "genai.png", W, 6.0)])]
    if len(a["weekly"]) < 3:
        story.append(Paragraph("Trend lines become meaningful after about 4 weeks of tracking.", ST['note']))
    story.append(PageBreak())

    # ---- 3 role mix and skills
    bar_chart(a["cats"], charts / "cats.png", HEX[GREEN], height=5.0)
    bar_chart(a["sk_cur_pct"], charts / "skills.png", HEX[GREEN], pct_labels=True, top=12, height=7.0)
    story += [SectionHeader("3", "Roles and skills", "LAST 7 DAYS", GREEN), Spacer(1, 16),
              KeepTogether([Paragraph("Roles by category", ST['h2']), img(charts / "cats.png", W, 5.0)]),
              KeepTogether([Paragraph("Top skills (% of postings that mention each)", ST['h2']),
                            img(charts / "skills.png", W, 7.0)])]
    story.append(Paragraph("Skill movers vs the prior 4 weeks", ST['h2']))
    if len(a["movers"]):
        rows = [[k, f"{int(r.now)}%", f"{int(r.before)}%", f"{'+' if r.change > 0 else ''}{int(r.change)} pts"]
                for k, r in a["movers"].head(6).iterrows()]
        story.append(table(rows, ["Skill", "Now", "Prior 4 weeks", "Change"], [W * .4, W * .2, W * .2, W * .2], ST))
    else:
        story.append(Paragraph("Not enough history yet to measure skill movement reliably. This appears once "
                               "about 4 weeks of data are collected.", ST['note']))
    story.append(PageBreak())

    # ---- 4 geography and seniority
    bar_chart(a["cities"], charts / "cities.png", HEX[AMBER], height=5.5)
    order = ["Internship", "Entry", "Mid / unspecified", "Senior", "Lead / Manager", "Leadership"]
    lv = a["levels"].reindex([o for o in order if o in a["levels"].index])
    bar_chart(lv, charts / "levels.png", HEX[AMBER], height=5.0)
    story += [SectionHeader("4", "Where and at what level", "LAST 7 DAYS", AMBER), Spacer(1, 16),
              KeepTogether([Paragraph("Roles by city", ST['h2']), img(charts / "cities.png", W, 5.5)]),
              Paragraph(f"{a['no_city']} roles did not list a city and are not shown.", ST['note']),
              KeepTogether([Paragraph("Roles by seniority", ST['h2']), img(charts / "levels.png", W, 5.0)]),
              Paragraph("Seniority is read from job titles, so 'Mid / unspecified' includes titles with no level.",
                        ST['note']),
              PageBreak()]

    # ---- 5 who's hiring
    story += [SectionHeader("5", "Who is hiring", "LAST 7 DAYS", BLUE), Spacer(1, 16),
              Paragraph("Most active employers", ST['h2'])]
    rows = [[c, str(int(k))] for c, k in a["companies"].head(12).items()]
    story.append(table(rows, ["Company", "New roles (7 days)"], [W * .75, W * .25], ST) if rows
                 else Paragraph("No roles this week.", ST['note']))
    story.append(Paragraph("New entrants", ST['h2']))
    if len(a["entrants"]):
        story.append(Paragraph("Companies hiring for AI or data roles for the first time since tracking began.",
                               ST['body']))
        rows = [[c, str(int(k))] for c, k in a["entrants"].head(15).items()]
        story.append(table(rows, ["Company", "Roles"], [W * .75, W * .25], ST))
    else:
        story.append(Paragraph("No new entrants this week." if a["enough_history"] else
                               "Shown once about 5 weeks of history exist, so 'first time' is meaningful.",
                               ST['note']))
    story.append(Paragraph("Ramping up", ST['h2']))
    if len(a["ramp"]):
        rows = [[c, str(r.last_28d), str(r.previous_28d)] for c, r in a["ramp"].iterrows()]
        story.append(table(rows, ["Company", "Last 28 days", "Previous 28 days"], [W * .5, W * .25, W * .25], ST))
    else:
        story.append(Paragraph("Measured once about 8 weeks of history exist (last 28 days vs the 28 before).",
                               ST['note']))
    story.append(PageBreak())

    # ---- 6 leads
    story += [SectionHeader("6", "Sales leads for Densight", "LAST 28 DAYS", PURPLE), Spacer(1, 16),
              Paragraph("Companies with 3 or more AI or data roles in the last 28 days. Active AI hiring signals "
                        "budget and intent: good timing for Elevate and consulting conversations.", ST['body'])]
    if len(a["leads"]):
        rows = [[c, r.signal, str(r.roles_7d), str(r.roles_28d), r.categories, r.cities,
                 " / ".join(x for x in (r.co_size, r.co_revenue) if x) or "-"]
                for c, r in a["leads"].head(25).iterrows()]
        story.append(table(rows, ["Company", "Signal", "7d", "28d", "Role types", "Cities", "Size / revenue"],
                           [W * .17, W * .1, W * .06, W * .07, W * .24, W * .17, W * .19], ST))
    else:
        story.append(Paragraph("No company has 3 or more roles yet.", ST['note']))
    story.append(PageBreak())

    # ---- 7 new roles in this run
    new_pk = a["new"][a["new"]["market"] == PK] if "market" in a["new"] else a["new"]
    story += [SectionHeader("7", "New roles found in this run", f"{len(new_pk)} ROLES", GREEN), Spacer(1, 16)]
    if len(new_pk):
        rows = [[Paragraph(f'<link href="{esc(r.job_url)}" color="#1D4ED8">{esc(r.title)}</link>', ST['cell']),
                 r.company, r.city, r.category]
                for r in new_pk.sort_values(["category", "company"]).head(40).itertuples()]
        story.append(table(rows, ["Role (click to open)", "Company", "City", "Category"],
                           [W * .38, W * .24, W * .16, W * .22], ST))
        if len(new_pk) > 40:
            story.append(Paragraph(f"Showing 40 of {len(new_pk)}. The full list is in the master Sheet "
                                   f"(all_jobs tab).", ST['note']))
    else:
        story.append(Paragraph("No new Pakistan roles in this run.", ST['note']))
    story.append(PageBreak())

    # ---- 8 remote
    story += [SectionHeader("8", "Remote roles open to Pakistan", "GLOBAL BOARDS", AMBER), Spacer(1, 16),
              Paragraph("Remote AI and data roles from global boards that accept applicants worldwide or from Asia. "
                        "Not counted in the Pakistan figures.", ST['body'])]
    if len(a["rem_cur"]):
        rows = [[Paragraph(f'<link href="{esc(r.job_url)}" color="#1D4ED8">{esc(r.title)}</link>', ST['cell']),
                 r.company, SOURCE_NAMES.get(r.source, r.source)]
                for r in a["rem_cur"].head(25).itertuples()]
        story.append(table(rows, ["Role (click to open)", "Company", "Source"], [W * .5, W * .3, W * .2], ST))
    else:
        story.append(Paragraph("No remote roles open to Pakistan in the last 7 days.", ST['note']))

    # ---- 9 method
    story += [Spacer(1, 14), SectionHeader("9", "Method and data quality", "THIS RUN", BLUE), Spacer(1, 16)]
    src = Counter()
    for k, v in run.get("by_source", {}).items():
        src[SOURCE_NAMES.get(k, k)] += v
    rows = [[k, str(v)] for k, v in src.most_common()]
    story.append(Paragraph("AI and data roles found in this run, by source", ST['h2']))
    if rows:
        story.append(table(rows, ["Source", "Roles"], [W * .7, W * .3], ST))
    fails = run.get("failures", [])
    story.append(Paragraph("Issues in this run", ST['h2']))
    if fails:
        for f in fails[:10]:
            story.append(Paragraph('<bullet>&bull;</bullet> ' + esc(f), ST['bullet']))
    else:
        story.append(Paragraph("None. Every source responded.", ST['body']))
    story.append(Paragraph("How the numbers are built", ST['h2']))
    for t in ["Jobs are collected daily from LinkedIn, Indeed, company career pages (Greenhouse, Lever, Workable) "
              "and two remote boards, then filtered to AI and data roles by job title, plus generic engineering "
              "titles whose description shows clear AI work.",
              "Duplicates are removed by link and by company + title + city; a job seen again within 30 days is "
              "not counted again.",
              "Figures describe roles we tracked, not every AI role in Pakistan. Jobs posted only on other sites, "
              "social media or by referral are not included.",
              "Seniority and skills are read from titles and descriptions with keyword rules, so they are "
              "approximate. Takeaways are generated by fixed rules and only shown when the data supports them."]:
        story.append(Paragraph('<bullet>&bull;</bullet> ' + t, ST['bullet']))
    story += [Spacer(1, 18), HRFlowable(width='100%', thickness=1, color=BORDER, spaceAfter=16),
              Paragraph('Densight Labs | densight.com', ST['footer']),
              Paragraph('Numan Ahmad | numan.ahmad@densight.com', ST['footer'])]

    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm,
                            bottomMargin=2.2 * cm, title=f"AI Jobs in Pakistan: market analysis {nice}",
                            author='Numan Ahmad | Densight Labs')
    doc.build(story, onFirstPage=cover_bg, onLaterPages=later)


def main():
    if not RUN.exists() or not DATA.exists():
        print("No run data yet; skipping analysis.")
        return
    run = json.loads(RUN.read_text())
    n_new = len(run.get("new_ids", []))
    if n_new == 0 and os.environ.get("FORCE_REPORT") != "1":
        print("This run found no new jobs, so no analysis email is sent.")
        return
    hist = pd.read_csv(DATA, dtype=str, keep_default_na=False)
    for c in ("company_size", "company_revenue", "market"):
        if c not in hist.columns:
            hist[c] = ""
    a = analyse(hist, run)
    OUT.mkdir(exist_ok=True)
    path = OUT / f"AI_Jobs_Pakistan_Analysis_{run['date']}.pdf"
    build_pdf(a, run, path)
    print(f"PDF written: {path} ({path.stat().st_size // 1024} KB)")

    nice = f"{a['today'].day} {a['today']:%b %Y}"
    subject = f"AI Jobs in Pakistan: {n_new} new roles, market analysis {nice}"
    lines = "".join(f"<li>{t}</li>" for t in takeaways(a))
    html = (f"<p>Today's run found <b>{n_new}</b> new roles. The full analysis is attached.</p>"
            f"<p><b>Key takeaways (last 7 days)</b></p><ul>{lines}</ul>"
            f"<p style='color:#64748b;font-size:12px'>Sent automatically by the Densight AI jobs pipeline.</p>")
    res = sheets.send_email(subject, html, path, to=CONFIG.get("report_email", ""))
    print("Email:", res)


if __name__ == "__main__":
    main()
