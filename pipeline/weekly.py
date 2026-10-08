"""Weekly job: build the public weekly tab, update master company/stats tabs,
render carousel charts and write a summary for the email and posts.

Covers the previous Monday to Sunday (Pakistan time) by default.
Override with WEEK=2026-W41 to rebuild a specific week.

Run:  python -m pipeline.weekly
"""
import os
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from . import charts, sheets
from .classify import CONFIG, ROOT

PKT = timezone(timedelta(hours=5))
DATA = ROOT / "data" / "jobs.csv"


def week_bounds():
    w = os.environ.get("WEEK")
    if w:
        y, n = w.split("-W")
        start = date.fromisocalendar(int(y), int(n), 1)
    else:
        today = datetime.now(PKT).date()
        start = today - timedelta(days=today.weekday() + 7)  # previous Monday
    end = start + timedelta(days=6)
    iso = start.isocalendar()
    return start, end, f"{iso[0]}-W{iso[1]:02d}"


def label(start, end):
    if start.month == end.month:
        return f"{start.day}-{end.day} {end:%b %Y}"
    return f"{start.day} {start:%b} - {end.day} {end:%b %Y}"


def skill_counts(df):
    c = Counter(s.strip() for row in df["skills"] for s in str(row).split(",") if s.strip())
    return pd.Series(c).sort_values(ascending=False) if c else pd.Series(dtype=int)


def pct_change(cur, prev):
    if not prev:
        return None
    return round((cur - prev) / prev * 100)


def main():
    start, end, wk = week_bounds()
    nice = label(start, end)
    print(f"Building report for {wk} ({nice})")

    hist = pd.read_csv(DATA, dtype=str, keep_default_na=False)
    for c in ("company_size", "company_revenue"):  # history saved before these fields existed
        if c not in hist.columns:
            hist[c] = ""
    if "market" not in hist.columns:
        hist["market"] = "Pakistan"
    hist["market"] = hist["market"].replace("", "Pakistan")
    hist["first_seen_d"] = pd.to_datetime(hist["first_seen"]).dt.date
    # Remote (global) jobs get their own section; all Pakistan stats use Pakistan jobs only
    remote_hist = hist[hist["market"] == "Remote (global)"]
    remote_week = remote_hist[(remote_hist["first_seen_d"] >= start) & (remote_hist["first_seen_d"] <= end)]
    hist = hist[hist["market"] == "Pakistan"].copy()
    week = hist[(hist["first_seen_d"] >= start) & (hist["first_seen_d"] <= end)].copy()
    prev = hist[(hist["first_seen_d"] >= start - timedelta(days=7)) & (hist["first_seen_d"] < start)]

    out_dir = ROOT / "reports" / wk
    chart_dir = out_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)

    n = len(week)
    n_companies = week["company_norm"].nunique()
    NAMES = {"linkedin": "LinkedIn", "indeed": "Indeed", "glassdoor": "Glassdoor", "rozee": "Rozee.pk",
             "greenhouse": "Company careers", "lever": "Company careers", "workable": "Company careers",
             "remoteok": "RemoteOK", "weworkremotely": "We Work Remotely"}
    sources = ", ".join(sorted(NAMES.get(s, s.title()) for s in week["source"].unique() if s)) or "LinkedIn, Indeed"
    foot = f"Source: {CONFIG['brand']} analysis of {n} AI and data job postings in Pakistan ({sources}), {nice}"

    # ---------------- Public weekly tab ----------------
    order = {c["name"]: i for i, c in enumerate(CONFIG["categories"])}
    order["AI-adjacent Engineering"] = len(order)
    pub = pd.concat([
        week.assign(_m=0, _o=week["category"].map(order)),
        remote_week.assign(_m=1, _o=remote_week["category"].map(order)),
    ]).sort_values(["_m", "_o", "company", "title"])
    pub_headers = ["Role", "Company", "City", "Category", "Level", "Skills mentioned", "Found on", "Posted", "Apply link"]
    pub = pub.assign(source=pub["source"].map(lambda s: NAMES.get(s, s.title())))
    pub_rows = pub[["title", "company", "city", "category", "seniority", "skills", "source",
                    "date_posted", "job_url"]].values.tolist()
    banner = (f"{n} AI & data roles in Pakistan + {len(remote_week)} remote roles open to Pakistan, {nice}  |  "
              f"Compiled by {CONFIG['brand']}  |  "
              f"Get this list every Monday: {CONFIG['signup_url']}")
    sheets.write("public", f"{wk} ({nice})", pub_headers, pub_rows, mode="replace", banner=banner, first=True)

    # ---------------- Master: companies (lead list) ----------------
    hist["first_seen_d"] = pd.to_datetime(hist["first_seen"]).dt.date
    last4 = hist[hist["first_seen_d"] > end - timedelta(days=28)]
    g = hist.groupby("company_norm")
    comp = pd.DataFrame({
        "company": g["company"].agg(lambda s: s.value_counts().index[0]),
        "roles_this_week": week.groupby("company_norm").size(),
        "roles_last_4_weeks": last4.groupby("company_norm").size(),
        "roles_all_time": g.size(),
        "categories": g["category"].agg(lambda s: ", ".join(sorted(set(s)))),
        "cities": g["city"].agg(lambda s: ", ".join(sorted(set(s)))),
        "latest_role": g.apply(lambda d: d.sort_values("first_seen").iloc[-1]["title"], include_groups=False),
        "first_seen": g["first_seen"].min(),
        "last_seen": g["last_seen"].max(),
        "industry": g["industry"].agg(lambda s: next((x for x in s if x), "")),
        "company_size": g["company_size"].agg(lambda s: next((x for x in s if x), "")),
        "company_revenue": g["company_revenue"].agg(lambda s: next((x for x in s if x), "")),
        "company_url": g["company_url"].agg(lambda s: next((x for x in s if x), "")),
    }).fillna({"roles_this_week": 0, "roles_last_4_weeks": 0})
    comp["lead_signal"] = pd.cut(comp["roles_last_4_weeks"], [-1, 0, 2, 4, 1e9],
                                 labels=["Cold", "Warm", "Hot", "Very hot"]).astype(str)
    comp = comp.sort_values(["roles_last_4_weeks", "roles_all_time"], ascending=False)
    comp_cols = ["company", "lead_signal", "roles_this_week", "roles_last_4_weeks", "roles_all_time",
                 "categories", "cities", "latest_role", "industry", "company_size", "company_revenue",
                 "company_url", "first_seen", "last_seen"]
    sheets.write("master", "companies", comp_cols, comp[comp_cols].values.tolist(), mode="replace")

    # ---------------- Charts ----------------
    sk = skill_counts(week)
    sk_pct = (sk / max(n, 1) * 100).round()
    top_co = week.groupby("company").size().sort_values(ascending=False)
    cities_all = week["city"].value_counts()
    cities = cities_all.drop(labels=["Pakistan (unspecified)"], errors="ignore")
    no_city = int(cities_all.get("Pakistan (unspecified)", 0))
    cats = week["category"].value_counts()
    levels = week["seniority"].value_counts()

    hist_weeks = hist.copy()
    hist_weeks["wk_start"] = hist_weeks["first_seen_d"].apply(lambda d: d - timedelta(days=d.weekday()))
    trend = hist_weeks[hist_weeks["wk_start"] <= start].groupby("wk_start").size().tail(12)
    trend.index = [f"{d.day} {d:%b}" for d in trend.index]

    charts.hbar(top_co, chart_dir / "01_top_companies.png", "Who is hiring for AI",
                f"Companies with the most new AI & data roles, {nice}", foot)
    charts.hbar(sk_pct, chart_dir / "02_skills.png", "Skills employers ask for",
                "% of postings that mention each skill", foot, pct=True, top=15)
    charts.hbar(cities, chart_dir / "03_cities.png", "Where the jobs are",
                "New AI & data roles by city" + (f" ({no_city} listed no city)" if no_city else ""), foot)
    charts.hbar(cats, chart_dir / "04_role_types.png", "What kind of AI roles",
                "New roles by category", foot)
    charts.hbar(levels, chart_dir / "05_seniority.png", "Which levels are hiring",
                "New roles by seniority (from job titles)", foot)
    charts.trend(trend, chart_dir / "06_weekly_trend.png", "AI hiring, week by week",
                 "New AI & data roles first seen each week", foot)

    # ---------------- Master: weekly stats ----------------
    change = pct_change(n, len(prev))
    stats_headers = ["week", "dates", "new_roles", "change_vs_prev_week_pct", "hiring_companies",
                     "top_city", "top_category", "top_skill", "genai_llm_roles", "remote_roles", "remote_global_open_to_pk", "entry_or_intern_roles"]
    stats_row = [wk, nice, n, "" if change is None else change, n_companies,
                 cities.index[0] if len(cities) else "", cats.index[0] if len(cats) else "",
                 sk.index[0] if len(sk) else "", int((week["category"] == "GenAI / LLM").sum()),
                 int((week["city"] == "Remote").sum()), len(remote_week),
                 int(week["seniority"].isin(["Entry", "Internship"]).sum())]
    sheets.write("master", "weekly_stats", stats_headers, [stats_row])

    # ---------------- Summary for email and posts ----------------
    pick = week.assign(_o=week["category"].map(order)).sort_values(["_o", "date_posted"], ascending=[True, False])
    top10 = pick.drop_duplicates("company_norm").head(10)
    chg = "" if change is None else f" ({'+' if change >= 0 else ''}{change}% vs last week)"
    lines = [
        f"# AI Jobs in Pakistan: {nice}",
        "",
        "## Headline numbers",
        f"- New AI & data roles: **{n}**{chg}",
        f"- Companies hiring: **{n_companies}**",
        f"- Top city: **{cities.index[0] if len(cities) else 'n/a'}** ({cities.iloc[0] if len(cities) else 0} roles)",
        f"- Most-requested skill: **{sk.index[0] if len(sk) else 'n/a'}** ({int(sk_pct.iloc[0]) if len(sk) else 0}% of postings)",
        f"- GenAI / LLM roles: **{stats_row[8]}**",
        f"- Pakistan-based remote roles: **{stats_row[9]}**  |  Entry-level or internships: **{stats_row[11]}**",
        f"- Remote roles open to Pakistan (global boards, not in the numbers above): **{stats_row[10]}**",
        "",
        "## Top hiring companies",
        *[f"- {c}: {k} roles" for c, k in top_co.head(5).items()],
        "",
        "## Top 10 picks for the email",
        *[f"{i}. **{r.title}**, {r.company} ({r.city})  \n   {r.job_url}" for i, r in enumerate(top10.itertuples(), 1)],
        "",
        "## Remote picks open to Pakistan",
        *[f"- **{r.title}**, {r.company}  \n  {r.job_url}" for r in remote_week.drop_duplicates("company_norm").head(5).itertuples()],
        "",
        "## Role mix",
        *[f"- {c}: {k}" for c, k in cats.items()],
        "",
        f"_Charts: reports/{wk}/charts/_",
    ]
    (out_dir / "summary.md").write_text("\n".join(lines))
    week.drop(columns=["first_seen_d"]).to_csv(out_dir / "jobs.csv", index=False)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
