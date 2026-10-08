"""Daily job: scrape, clean, dedupe against history, save and push new jobs to the master Sheet.

Run:  python -m pipeline.scrape
"""
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import sheets, sources
from .classify import CONFIG, ROOT, prepare

DATA = ROOT / "data" / "jobs.csv"
PKT = timezone(timedelta(hours=5))

# Columns kept in the history file and pushed to the master Sheet (no descriptions: too large for git)
COLUMNS = [
    "job_id", "first_seen", "last_seen", "category", "title", "company", "city", "seniority",
    "skills", "source", "date_posted", "job_type", "is_remote", "industry",
    "min_salary", "max_salary", "currency", "salary_interval", "job_url", "company_url",
    "company_norm", "title_norm", "company_size", "company_revenue", "market",
]
# Columns in the master Sheet's all_jobs tab. A backfill run rewrites the whole tab (with headers);
# daily runs append rows in this same order.
SHEET_COLUMNS = [
    "job_id", "first_seen", "category", "title", "company", "city", "seniority",
    "skills", "source", "date_posted", "job_type", "is_remote", "industry",
    "min_salary", "max_salary", "currency", "salary_interval", "job_url", "company_url", "market",
]
LAST_RUN = ROOT / "data" / "last_run.txt"


def lookback_days():
    """LOOKBACK_DAYS env var = one-time backfill (e.g. 14). Empty = normal daily run."""
    v = os.environ.get("LOOKBACK_DAYS", "").strip()
    return int(v) if v.isdigit() and int(v) > 0 else None


def _proxies():
    raw = os.environ.get("PROXIES", "").strip()
    return [p.strip() for p in raw.split(",") if p.strip()] or None


def _searches():
    """Yields (site, label, kwargs) for every search configured in config.yaml."""
    for site, opts in CONFIG["sites"].items():
        for q in opts.get("queries", []):
            yield site, q, {"search_term": q, "results_wanted": opts["results_per_query"]}
        ids = opts.get("company_ids") or []
        if site == "linkedin" and ids:
            yield site, f"{len(ids)} tracked employers", {
                "linkedin_company_ids": [int(i) for i in ids],
                "results_wanted": opts.get("company_results", 100),
            }


def scrape_all() -> tuple[pd.DataFrame, list[str]]:
    from jobspy import scrape_jobs

    proxies = _proxies()
    frames, log, failures = [], [], {}
    for site, label, kwargs in _searches():
        if failures.get(site, 0) >= 3:  # site is blocking us today; stop hammering it
            if f"{site}: skipped" not in " ".join(log):
                log.append(f"{site}: skipped remaining searches after 3 failures")
            continue
        try:
            df = scrape_jobs(
                site_name=[site],
                location=CONFIG["location"],
                country_indeed=CONFIG["country_indeed"],
                hours_old=(lookback_days() or 0) * 24 or CONFIG["hours_old"],
                fetch_description=CONFIG["fetch_description"],
                proxies=proxies,
                verbose=0,
                **kwargs,
            )
            df["search_term"] = label
            frames.append(df)
            log.append(f"{site} | {label[:70]} | {len(df)}")
        except Exception as e:
            failures[site] = failures.get(site, 0) + 1
            log.append(f"{site} | {label[:70]} | FAILED: {str(e)[:120]}")
        print(log[-1], flush=True)
        time.sleep(CONFIG["pause_seconds"])
    raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return raw, log


def load_history() -> pd.DataFrame:
    if DATA.exists():
        return pd.read_csv(DATA, dtype=str, keep_default_na=False)
    return pd.DataFrame(columns=COLUMNS)


def _clamp(d, lo, hi):
    return d if d and lo <= d <= hi else (lo if d and d < lo else hi)


def merge(history: pd.DataFrame, fresh: pd.DataFrame, today: str, backfill_days=None):
    """Returns (updated_history, new_rows). A job seen within the repost window is not new.

    Normal run: first_seen = today.
    Backfill run: first_seen = the job's posting date (kept inside the backfill window), so older
    jobs land in the week they were posted instead of all landing in this week.
    """
    window = (datetime.fromisoformat(today) - timedelta(days=CONFIG["repost_window_days"])).date().isoformat()
    recent = history[history["last_seen"] >= window]
    recent_ids = set(recent["job_id"])
    recent_urls = set(recent["job_url"])

    seen_mask = fresh["job_id"].isin(recent_ids) | fresh["job_url"].isin(recent_urls)
    new = fresh[~seen_mask].copy()
    new["first_seen"] = today
    new["last_seen"] = today
    if backfill_days:
        lo = (datetime.fromisoformat(today) - timedelta(days=backfill_days)).date().isoformat()
        posted = new["date_posted"].astype(str).str[:10].replace({"None": "", "nan": "", "NaT": ""})
        new["first_seen"] = [_clamp(p, lo, today) if p else today for p in posted]
        # jobs already in history: move first_seen back to the posting date if it is earlier
        dp = dict(zip(fresh["job_id"], fresh["date_posted"].astype(str).str[:10]))
        for i, row in history.iterrows():
            p = dp.get(row["job_id"])
            if p and p not in ("None", "nan", "NaT") and lo <= p < row["first_seen"]:
                history.at[i, "first_seen"] = p

    # refresh last_seen for jobs still being listed
    still = set(fresh.loc[seen_mask, "job_id"])
    history.loc[history["job_id"].isin(still) & (history["last_seen"] >= window), "last_seen"] = today

    new = new.reindex(columns=COLUMNS).astype(str).replace({"nan": "", "None": "", "NaT": ""})
    return pd.concat([history, new], ignore_index=True), new


def _within_window(df, today, days):
    """Drop feed jobs posted before the window (feeds list every open job, however old)."""
    if df.empty or "date_posted" not in df.columns:
        return df
    lo = (datetime.fromisoformat(today) - timedelta(days=days)).date().isoformat()
    d = df["date_posted"].astype(str).str[:10]
    keep = (d >= lo) | ~d.str.match(r"\d{4}-\d{2}-\d{2}")
    return df[keep]


def main():
    today = datetime.now(PKT).date().isoformat()
    backfill = lookback_days()
    print(f"Mode: {'BACKFILL ' + str(backfill) + ' days' if backfill else 'daily'}", flush=True)

    raw_js, log = scrape_all()
    feed_rows, feed_log = sources.fetch_all()
    log += feed_log
    feeds = _within_window(pd.DataFrame(feed_rows), today, backfill or CONFIG.get("feed_window_days", 3))
    raw = pd.concat([raw_js, feeds], ignore_index=True) if len(feeds) else raw_js

    fresh = prepare(raw)
    history = load_history()
    if "market" in history.columns:
        history["market"] = history["market"].replace("", "Pakistan")
    else:
        history["market"] = "Pakistan"
    updated, new = (merge(history, fresh, today, backfill) if len(fresh)
                    else (history, fresh.reindex(columns=COLUMNS)))

    DATA.parent.mkdir(exist_ok=True)
    updated = updated.reindex(columns=COLUMNS).fillna("")
    updated.to_csv(DATA, index=False)

    by_source = fresh["source"].value_counts().to_dict() if len(fresh) else {}
    summary = (f"raw={len(raw)}  ai_relevant={len(fresh)}  new={len(new)}  total_history={len(updated)}\n"
               f"AI-relevant by source: {by_source}")
    print("\n" + summary)
    LAST_RUN.write_text(f"Run at {datetime.now(PKT):%Y-%m-%d %H:%M} PKT  "
                        f"({'backfill ' + str(backfill) + ' days' if backfill else 'daily'})\n"
                        + summary + "\n\n" + "\n".join(log) + "\n")

    if backfill:
        # rewrite the whole tab so first_seen dates and the new column are consistent
        rows = updated.sort_values(["first_seen", "company"], ascending=[False, True])
        sheets.write("master", "all_jobs", SHEET_COLUMNS, rows[SHEET_COLUMNS].values.tolist(), mode="replace")
    elif len(new):
        sheets.write("master", "all_jobs", SHEET_COLUMNS, new[SHEET_COLUMNS].values.tolist())
    failed = sum("FAILED" in l for l in log)
    sheets.write("master", "runs_log",
                 ["run_at_pkt", "raw_rows", "ai_relevant", "new_jobs", "failed_searches", "notes"],
                 [[datetime.now(PKT).strftime("%Y-%m-%d %H:%M"), len(raw), len(fresh), len(new), failed,
                   "; ".join(l for l in log if "FAILED" in l or "skipped" in l)[:4000]]])

    if len(raw) == 0:
        raise SystemExit("No jobs scraped at all: every source failed. Check the run log.")


if __name__ == "__main__":
    main()
