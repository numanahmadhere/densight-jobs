"""Cleaning, classification and dedupe helpers shared by the daily and weekly jobs."""
import hashlib
import re
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text())

_CATS = [(c["name"], re.compile(c["title"], re.I)) for c in CONFIG["categories"]]
_EXCLUDE = re.compile(CONFIG["exclude_title"], re.I)
_ADJ = CONFIG.get("ai_adjacent") or {}
_ADJ_TITLE = re.compile(_ADJ["title"], re.I) if _ADJ else None
_ADJ_TERMS = [re.compile(t, re.I) for t in _ADJ.get("description_terms", [])]
_ADJ_MIN = _ADJ.get("min_hits", 2)
_SENIORITY = [(k, re.compile(v, re.I)) for k, v in CONFIG["seniority"].items()]
_SKILLS = [(k, re.compile(v, re.I)) for k, v in CONFIG["skills"].items()]
_CITIES = CONFIG["cities"]

_COMPANY_SUFFIX = re.compile(r"\b(pvt|private|ltd|limited|llc|inc|co|company|smc|\(pvt\)|\(private\))\b\.?", re.I)
_TITLE_NOISE = re.compile(r"[\(\[].*?[\)\]]|\b(urgent|hiring|immediate|remote|onsite|on-site|hybrid|lahore|karachi|islamabad|pakistan)\b", re.I)


def norm_company(s):
    s = str(s or "").lower()
    s = _COMPANY_SUFFIX.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_title(s):
    s = _TITLE_NOISE.sub(" ", str(s or "").lower())
    s = re.sub(r"[^a-z0-9/+ ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def category(title, description=""):
    t = str(title or "")
    if _EXCLUDE.search(t):
        return None
    for name, rx in _CATS:
        if rx.search(t):
            return name
    # Generic engineering title, but the description shows real AI work
    if _ADJ_TITLE is not None and _ADJ_TITLE.search(t):
        d = str(description or "")
        if sum(1 for rx in _ADJ_TERMS if rx.search(d)) >= _ADJ_MIN:
            return "AI-adjacent Engineering"
    return None


def seniority(title, job_level=None):
    # The title is the most reliable signal; LinkedIn's job_level is a fallback.
    # ("Mid-Senior level" is LinkedIn's default for most jobs, so it is ignored.)
    for name, rx in _SENIORITY:
        if rx.search(str(title or "")):
            return name
    lvl = str(job_level or "").strip().lower()
    mapping = {"internship": "Internship", "entry level": "Entry", "associate": "Entry",
               "director": "Leadership", "executive": "Leadership"}
    return mapping.get(lvl, "Mid / unspecified")


def city(location, is_remote=False):
    loc = str(location or "").lower()
    for name, keys in _CITIES.items():
        if any(k in loc for k in keys):
            return name
    if is_remote or "remote" in loc:
        return "Remote"
    return "Pakistan (unspecified)"


def skills(text):
    t = str(text or "")
    return ", ".join(name for name, rx in _SKILLS if rx.search(t))


def job_id(row):
    key = f"{row['company_norm']}|{row['title_norm']}|{row['city']}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def is_pakistan(location):
    loc = str(location or "").lower()
    if not loc or loc in ("nan", "none"):
        return True  # Indeed PK results often have empty location; they are PK by search scope
    if "pakistan" in loc or loc.endswith(", pk") or loc == "pk":
        return True
    return any(k in loc for keys in _CITIES.values() for k in keys)


def is_pakistan_strict(location):
    """For career pages: the location must actually name Pakistan or a Pakistani city."""
    loc = str(location or "").lower()
    if "pakistan" in loc or re.search(r"\bpk\b", loc):
        return True
    return any(k in loc for name, keys in _CITIES.items() if name != "Hyderabad" for k in keys)


def prepare(raw: pd.DataFrame) -> pd.DataFrame:
    """Turn raw JobSpy output into clean, classified rows (one per posting)."""
    if raw.empty:
        return raw

    def col(name, default=""):
        return raw[name] if name in raw.columns else pd.Series(default, index=raw.index)

    df = pd.DataFrame({
        "source": col("site"),
        "title": col("title").fillna("").astype(str).str.strip(),
        "company": col("company").fillna("").astype(str).str.strip(),
        "location": col("location").fillna("").astype(str),
        "is_remote": col("is_remote", False).fillna(False).astype(bool),
        "job_type": col("job_type").fillna("").astype(str),
        "job_level": col("job_level").fillna("").astype(str),
        "industry": col("company_industry").fillna("").astype(str),
        "min_salary": col("min_amount", None),
        "max_salary": col("max_amount", None),
        "currency": col("currency").fillna("").astype(str),
        "salary_interval": col("interval").fillna("").astype(str),
        "date_posted": pd.to_datetime(col("date_posted", None), errors="coerce").dt.date,
        "job_url": col("job_url").fillna("").astype(str),
        "company_url": col("company_url").fillna("").astype(str),
        "company_size": col("company_num_employees").fillna("").astype(str),
        "company_revenue": col("company_revenue").fillna("").astype(str),
        "description": col("description").fillna("").astype(str),
        "search_term": col("search_term"),
        "market": col("market", "Pakistan").fillna("Pakistan").astype(str).replace("", "Pakistan"),
    })

    df = df[(df["title"] != "") & (df["company"] != "") & (df["job_url"] != "")]
    remote_global = df["market"] == "Remote (global)"
    df = df[remote_global | df["location"].apply(is_pakistan)]
    df["category"] = [category(t, d) for t, d in zip(df["title"], df["description"])]
    df = df[df["category"].notna()].copy()

    df["company_norm"] = df["company"].apply(norm_company)
    df["title_norm"] = df["title"].apply(norm_title)
    df["city"] = [("Remote (global)" if m == "Remote (global)" else city(l, r))
                  for l, r, m in zip(df["location"], df["is_remote"], df["market"])]
    df["seniority"] = [seniority(t, l) for t, l in zip(df["title"], df["job_level"])]
    df["skills"] = (df["title"] + " " + df["description"]).apply(skills)
    df["job_id"] = df.apply(job_id, axis=1)

    # Same posting found by several search terms or on both boards: keep one,
    # preferring the row that has a description.
    df["has_desc"] = df["description"].str.len() > 50
    df = df.sort_values("has_desc", ascending=False)
    df = df.drop_duplicates("job_url").drop_duplicates("job_id")
    return df.drop(columns=["has_desc", "job_level"])
