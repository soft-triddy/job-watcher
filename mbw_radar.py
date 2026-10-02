# -*- coding: utf-8 -*-
"""
Music Business Worldwide Jobs (musicbusinessworldwide.com/jobs): доска вакансий музиндустрии.
Сайт на WordPress + WP Job Manager, у него открытый REST API:
  /jobs/wp-json/wp/v2/job-listings  — название, ссылка, описание и meta (_company_name,
  _job_location, _remote_position, _filled).
Берём свежие страницы, закрытые (_filled) пропускаем, дальше — общий маркетинговый фильтр.
Запускается внутри music-radar (там же контекст оценщика для музтеха).
"""
import html, os, re, time

import requests
from core import UA, is_marketing, near_misses, load, save, report, Health

API     = "https://www.musicbusinessworldwide.com/jobs/wp-json/wp/v2/job-listings"
STATE   = os.environ.get("MBW_STATE", "state/seen_mbw.json")
HEALTH  = "state/health_mbw.json"
REJECTED = "state/rejected_mbw.json"
LABEL   = "🎵 MBW Jobs"
PAGES   = 3            # по 100 свежих вакансий на страницу — доска небольшая, этого с запасом
TIMEOUT = 30

def _text(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())

def fetch(page):
    for attempt in range(3):
        r = requests.get(API, params={"per_page": 100, "page": page, "orderby": "date", "order": "desc"},
                         headers=UA, timeout=TIMEOUT)
        if r.status_code == 400 and page > 1:      # WordPress: страница за пределами — 400
            return []
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(10 * (attempt + 1)); continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()

def to_job(it):
    m = it.get("meta") or {}
    loc = (m.get("_job_location") or "").strip()
    if m.get("_remote_position"):
        loc = f"{loc} · Remote" if loc else "Remote"
    return {"guid": f"mbw:{it.get('id')}", "title": _text((it.get("title") or {}).get("rendered")),
            "company": (m.get("_company_name") or "").strip(), "location": loc,
            "url": it.get("link") or "", "filled": bool(m.get("_filled")),
            "description": (it.get("content") or {}).get("rendered") or ""}

def main():
    seen = load(STATE, {})
    health = Health(HEALTH, LABEL, empty_is_bad=True)
    jobs = {}
    for page in range(1, PAGES + 1):
        try:
            items = fetch(page)
        except Exception as e:
            health.mark(f"страница {page}", err=f"{type(e).__name__} {str(e)[:60]}"); break
        if page == 1: health.mark("доска", jobs=len(items))
        if not items: break
        for it in items:
            j = to_job(it)
            if j["title"] and j["url"] and not j["filled"]: jobs[j["guid"]] = j
        if len(items) < 100: break
        time.sleep(1)
    alljobs = list(jobs.values())
    mk = [j for j in alljobs if is_marketing(j["title"], j["location"])]
    save(REJECTED, near_misses(alljobs))
    print(f"=== MBW Jobs | открытых: {len(alljobs)} | маркетинг: {len(mk)} ===")
    report(mk, seen, STATE, label=LABEL, key="guid")
    health.finish({"jobs_total": len(alljobs), "marketing": len(mk)})

if __name__ == "__main__":
    main()
