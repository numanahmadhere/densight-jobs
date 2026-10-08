"""Extra job sources that publish official public feeds (no scraping):

  - Company career pages on Greenhouse, Lever and Workable  -> Pakistan jobs only
  - Remote boards RemoteOK and We Work Remotely             -> remote jobs open worldwide / Asia

Each fetcher returns rows shaped like JobSpy output, so classify.prepare() handles them the same way.
Rows carry a "market" field: "Pakistan" or "Remote (global)".
"""
import html
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from .classify import CONFIG, is_pakistan_strict

UA = {"User-Agent": "Mozilla/5.0 (compatible; DensightJobsBot/1.0; +https://densight.com)"}
_TAGS = re.compile(r"<[^>]+>")


def _text(h):
    """HTML (possibly entity-escaped) -> plain text."""
    t = html.unescape(html.unescape(str(h or "")))
    t = _TAGS.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def _get(url, as_json=True):
    for attempt in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=45)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            r.encoding = "utf-8"
            return r.json() if as_json else r.text
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def _row(site, title, company, location, url, description, date_posted, is_remote=False,
         market="Pakistan", company_url=""):
    return {
        "site": site, "title": title, "company": company, "location": location,
        "job_url": url, "description": description, "date_posted": date_posted,
        "is_remote": is_remote, "market": market, "company_url": company_url,
    }


def _date(v):
    """ISO string or epoch (s or ms) -> YYYY-MM-DD, or None."""
    if v in (None, "", 0):
        return None
    try:
        if isinstance(v, (int, float)):
            v = v / 1000 if v > 1e11 else v
            return datetime.fromtimestamp(v, tz=timezone.utc).date().isoformat()
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return None


# ---------------------------------------------------------------- career pages
def greenhouse(token, name):
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true") or {}
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        offices = " | ".join(o.get("name", "") for o in j.get("offices") or [])
        where = f"{loc} | {offices}" if offices else loc
        if not is_pakistan_strict(where):
            continue
        out.append(_row("greenhouse", j.get("title", ""), j.get("company_name") or name, loc or offices,
                        j.get("absolute_url", ""), _text(j.get("content")),
                        _date(j.get("first_published") or j.get("updated_at")),
                        is_remote="remote" in where.lower()))
    return out


def lever(slug, name):
    data = _get(f"https://api.lever.co/v0/postings/{slug}?mode=json") or []
    out = []
    for j in data:
        cats = j.get("categories") or {}
        locs = [cats.get("location") or ""] + list(cats.get("allLocations") or [])
        where = " | ".join(l for l in locs if l) + (" | Pakistan" if j.get("country") == "PK" else "")
        if not is_pakistan_strict(where):
            continue
        out.append(_row("lever", j.get("text", ""), name, cats.get("location") or "Pakistan",
                        j.get("hostedUrl", ""), j.get("descriptionPlain") or _text(j.get("description")),
                        _date(j.get("createdAt")), is_remote=j.get("workplaceType") == "remote"))
    return out


def workable(account, name):
    data = _get(f"https://apply.workable.com/api/v1/widget/accounts/{account}?details=true") or {}
    out = []
    for j in data.get("jobs", []):
        locs = [", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)]
        for l in j.get("locations") or []:
            locs.append(", ".join(x for x in (l.get("city"), l.get("region"), l.get("country")) if x))
        where = " | ".join(l for l in locs if l)
        if not is_pakistan_strict(where):
            continue
        out.append(_row("workable", j.get("title", ""), data.get("name") or name, locs[0] or where,
                        j.get("url", ""), _text(j.get("description")),
                        _date(j.get("published_on") or j.get("created_at")),
                        is_remote=bool(j.get("telecommuting"))))
    return out


# ---------------------------------------------------------------- remote boards
_OPEN = re.compile(CONFIG["remote_boards"]["open_to"], re.I)


def remoteok():
    data = _get("https://remoteok.com/api") or []
    out = []
    for j in data:
        if not isinstance(j, dict) or "position" not in j:
            continue  # first element is RemoteOK's legal notice
        loc = j.get("location") or ""
        if not _OPEN.search(loc):
            continue  # only jobs open worldwide / Asia / Pakistan
        out.append(_row("remoteok", j.get("position", ""), j.get("company", ""), loc,
                        j.get("url", ""), _text(j.get("description")) + " " + " ".join(j.get("tags") or []),
                        _date(j.get("date") or j.get("epoch")), is_remote=True, market="Remote (global)"))
    return out


def weworkremotely():
    xml = _get("https://weworkremotely.com/remote-jobs.rss", as_json=False)
    if not xml:
        return []
    out = []
    for it in ET.fromstring(xml).iter("item"):
        g = lambda tag: (it.findtext(tag) or "").strip()
        region, country = g("region"), g("country")
        if not _OPEN.search(f"{region} {country}"):
            continue
        if country and not re.search(r"pakistan", country, re.I) and not _OPEN.search(region):
            continue
        company, _, title = g("title").partition(": ")
        if not title:
            company, title = "", g("title")
        try:
            posted = datetime.strptime(g("pubDate"), "%a, %d %b %Y %H:%M:%S %z").date().isoformat()
        except ValueError:
            posted = None
        out.append(_row("weworkremotely", html.unescape(title), html.unescape(company), region,
                        g("link"), _text(g("description")) + " " + g("skills"), posted,
                        is_remote=True, market="Remote (global)"))
    return out


# ---------------------------------------------------------------- runner
def fetch_all():
    """Returns (rows, log_lines). One failing source never stops the others."""
    rows, log = [], []
    cp = CONFIG.get("career_pages") or {}
    jobs = [("greenhouse", greenhouse, cp.get("greenhouse") or {}),
            ("lever", lever, cp.get("lever") or {}),
            ("workable", workable, cp.get("workable") or {})]
    for kind, fn, companies in jobs:
        for slug, name in companies.items():
            try:
                r = fn(slug, name)
                rows += r
                log.append(f"{kind} | {name} | {len(r)}")
            except Exception as e:
                log.append(f"{kind} | {name} | FAILED: {str(e)[:120]}")
            print(log[-1], flush=True)
    for name, fn in (("remoteok", remoteok), ("weworkremotely", weworkremotely)):
        if not CONFIG["remote_boards"].get(name, True):
            continue
        try:
            r = fn()
            rows += r
            log.append(f"{name} | open to Pakistan | {len(r)}")
        except Exception as e:
            log.append(f"{name} | FAILED: {str(e)[:120]}")
        print(log[-1], flush=True)
    return rows, log
