# -*- coding: utf-8 -*-
"""
Хабр Карьера по маркетингу: JSON, которым пользуется их собственная страница поиска.
Дальше — общий маркетинговый фильтр, оценщик, seen.
(DOU и Djinni отключены 06.10 — украинские доски; hh убран 01.11 — без токена отвечает серверам GitHub 403.)
"""
import time

import requests
from core import UA, is_marketing, near_misses, load, save, report, Health

STATE    = "state/seen_boards.json"
HEALTH   = "state/health_boards.json"
REJECTED = "state/rejected_boards.json"
LABEL    = "📋 Хабр"
TIMEOUT  = 30

HABR   = ["маркетинг", "marketing", "growth", "CRM", "SEO"]     # поисковые запросы Хабр Карьеры
HABR_PAGES = 2                                                  # по 25 свежих на запрос

def _get(url, **params):
    for attempt in range(3):
        r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(10 * (attempt + 1)); continue
        r.raise_for_status()
        return r
    r.raise_for_status()

def habr(query):
    out = []
    for page in range(1, HABR_PAGES + 1):
        d = _get("https://career.habr.com/api/frontend/vacancies", q=query, type="all",
                 sort="date", per_page=25, page=page).json()
        for v in d.get("list") or []:
            locs = ", ".join(l.get("title", "") for l in (v.get("locations") or []) if l.get("title"))
            if v.get("remoteWork"): locs = f"{locs} · Remote" if locs else "Remote"
            out.append({"guid": f"habr:{v.get('id')}", "title": (v.get("title") or "").strip(),
                        "company": ((v.get("company") or {}).get("title") or "").strip(),
                        "location": locs, "url": "https://career.habr.com" + (v.get("href") or "")})
        if page >= ((d.get("meta") or {}).get("totalPages") or 1): break
        time.sleep(1)
    return out

SOURCES = [(f"Хабр «{q}»", habr, q) for q in HABR]

def main():
    seen = load(STATE, {})
    health = Health(HEALTH, LABEL, empty_is_bad=True)
    jobs = {}
    for name, fn, arg in SOURCES:
        try:
            got = [j for j in fn(arg) if j["title"] and j["url"]]
            health.mark(name, jobs=len(got))
            for j in got: jobs.setdefault(j["guid"], j)
        except Exception as e:
            print(f"  ⚠ {name}: {type(e).__name__} {str(e)[:120]}")
            health.mark(name, err=f"{type(e).__name__} {str(e)[:60]}")
        time.sleep(1)
    alljobs = list(jobs.values())
    mk = [j for j in alljobs if is_marketing(j["title"], j["location"])]
    save(REJECTED, near_misses(alljobs))
    print(f"=== доски | вакансий: {len(alljobs)} | маркетинг: {len(mk)} ===")
    report(mk, seen, STATE, label=LABEL, key="guid")
    health.finish({"jobs_total": len(alljobs), "marketing": len(mk)})

if __name__ == "__main__":
    main()
