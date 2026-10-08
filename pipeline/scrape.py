"""Daily job: scrape, clean, dedupe against history, save and push new jobs to the master Sheet.

Run:  python -m pipeline.scrape
"""
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import sheets
from .classify import CONFIG, ROOT, prepare

DATA = ROOT / "data" / "jobs.csv"
PKT = timezone(timedelta(hours=5))

# Columns kept in the history file and pushed to the master Sheet (no descriptions: too large for git)
COLUMNS = [
    "job_id", "first_seen", "last_seen", "category", "title", "company", "city", "seniority",
    "skills", "source", "date_posted", "job_type", "is_remote", "industry",
    "min_salary", "max_salary", "currency", "salary_interval", "job_url", "company_url",
    "company_norm", "title_norm",
]
SHEET_COLUMNS = [c for c in COLUMNS if c not in ("company_norm", "title_norm", "last_seen")]


def scrape_all() -> tuple[pd.DataFrame, list[str]]:
    from jobspy import scrape_jobs

    frames, log = [], []
    for site, opts in CONFIG["sites"].items():
        failures = 0
        for term in CONFIG["search_terms"]:
            if failures >= 3:  # site is blocking us today; stop hammering it
                log.append(f"{site}: skipped remaining terms after 3 failures")
                break
            try:
                df = scrape_jobs(
                    site_name=[site],
                    search_term=term,
                    location=CONFIG["location"],
                    country_indeed=CONFIG["country_indeed"],
                    results_wanted=opts["results_per_term"],
                    hours_old=CONFIG["hours_old"],
                    fetch_description=CONFIG["fetch_description"],
                    verbose=0,
                )
                df["search_term"] = term
                frames.append(df)
                log.append(f"{site} | {term} | {len(df)}")
            except Exception as e:
                failures += 1
                log.append(f"{site} | {term} | FAILED: {str(e)[:120]}")
            print(log[-1], flush=True)
            time.sleep(CONFIG["pause_seconds"])
    raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return raw, log


def load_history() -> pd.DataFrame:
    if DATA.exists():
        return pd.read_csv(DATA, dtype=str, keep_default_na=False)
    return pd.DataFrame(columns=COLUMNS)


def merge(history: pd.DataFrame, fresh: pd.DataFrame, today: str):
    """Returns (updated_history, new_rows). A job seen within the repost window is not new."""
    window = (datetime.fromisoformat(today) - timedelta(days=CONFIG["repost_window_days"])).date().isoformat()
    recent = history[history["last_seen"] >= window]
    recent_ids = set(recent["job_id"])
    recent_urls = set(recent["job_url"])

    seen_mask = fresh["job_id"].isin(recent_ids) | fresh["job_url"].isin(recent_urls)
    new = fresh[~seen_mask].copy()
    new["first_seen"] = today
    new["last_seen"] = today

    # refresh last_seen for jobs still being listed
    still = set(fresh.loc[seen_mask, "job_id"])
    history.loc[history["job_id"].isin(still) & (history["last_seen"] >= window), "last_seen"] = today

    new = new.reindex(columns=COLUMNS).astype(str).replace({"nan": "", "None": "", "NaT": ""})
    return pd.concat([history, new], ignore_index=True), new


def main():
    today = datetime.now(PKT).date().isoformat()
    raw, log = scrape_all()
    fresh = prepare(raw)
    history = load_history()
    updated, new = merge(history, fresh, today) if len(fresh) else (history, fresh.reindex(columns=COLUMNS))

    DATA.parent.mkdir(exist_ok=True)
    updated.reindex(columns=COLUMNS).to_csv(DATA, index=False)

    print(f"\nraw={len(raw)}  ai_relevant={len(fresh)}  new={len(new)}  total_history={len(updated)}")

    if len(new):
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
