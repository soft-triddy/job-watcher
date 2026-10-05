# -*- coding: utf-8 -*-
"""
Доски вакансий СНГ по маркетингу:
  Хабр Карьера — JSON, которым пользуется их собственная страница поиска;
  hh (HeadHunter) — официальный API api.hh.ru, только вакансии ВНЕ России
                    (все страны из справочника hh, кроме России и Украины).
DOU и Djinni отключены 06.10: украинские доски, с российским паспортом туда не пройти.
Дальше — общий маркетинговый фильтр, оценщик, seen.
"""
import html, os, re, time

import requests
from core import UA, is_marketing, near_misses, load, save, report, Health

STATE    = "state/seen_boards.json"
HEALTH   = "state/health_boards.json"
REJECTED = "state/rejected_boards.json"
LABEL    = "📋 Хабр · hh"
TIMEOUT  = 30

HABR   = ["маркетинг", "marketing", "growth", "CRM", "SEO"]     # поисковые запросы Хабр Карьеры
HABR_PAGES = 2                                                  # по 25 свежих на запрос

HH_API   = "https://api.hh.ru"
HH       = ["маркетинг", "маркетолог", "marketing", "growth", "CRM", "SEO", "performance"]
HH_SKIP  = {"Россия", "Украина"}         # страны, вакансии в которых не берём
HH_PAGES = 2                             # по 100 свежих на запрос (поиск только по названию)
HH_DAYS  = 30
# hh требует свой заголовок с названием приложения; токен — только если API начнёт требовать (секрет HH_TOKEN)
HH_HDR = {"HH-User-Agent": "job-radar/1.0 (github.com/soft-triddy/job-watcher)", "User-Agent": UA["User-Agent"]}
if os.environ.get("HH_TOKEN"): HH_HDR["Authorization"] = "Bearer " + os.environ["HH_TOKEN"]

def _get(url, headers=UA, **params):
    for attempt in range(3):
        r = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(10 * (attempt + 1)); continue
        if r.status_code == 403 and "hh.ru" in url:
            raise RuntimeError(f"HTTP 403 от hh: {r.text[:120]}")   # причина (токен / блок IP) — в отчёт здоровья
        r.raise_for_status()
        return r
    r.raise_for_status()

def _text(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())

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

_HH_AREAS = []
def hh_areas():
    """ID стран из справочника hh, кроме России и Украины. Город страны входит в её поиск сам."""
    if not _HH_AREAS:
        countries = _get(HH_API + "/areas/countries", headers=HH_HDR).json()
        _HH_AREAS.extend(c["id"] for c in countries if c.get("name") not in HH_SKIP)
        if not _HH_AREAS: raise RuntimeError("пустой справочник стран hh")
    return _HH_AREAS

def hh(query):
    out = []
    for page in range(HH_PAGES):
        d = _get(HH_API + "/vacancies", headers=HH_HDR, text=query, search_field="name", area=hh_areas(),
                 period=HH_DAYS, order_by="publication_time", per_page=100, page=page).json()
        for v in d.get("items") or []:
            fmt = [w.get("name", "") for w in (v.get("work_format") or [])] or \
                  [((v.get("schedule") or {}).get("name") or "")]
            loc = ", ".join(x for x in [(v.get("area") or {}).get("name", "")] + fmt if x)
            sn = v.get("snippet") or {}
            out.append({"guid": f"hh:{v.get('id')}", "title": (v.get("name") or "").strip(),
                        "company": ((v.get("employer") or {}).get("name") or "").strip(),
                        "location": loc, "url": v.get("alternate_url") or "",
                        "description": _text(f"{sn.get('responsibility') or ''} {sn.get('requirement') or ''}")})
        if page + 1 >= (d.get("pages") or 1): break
        time.sleep(1)
    return out

SOURCES = [(f"Хабр «{q}»", habr, q) for q in HABR] + [(f"hh «{q}»", hh, q) for q in HH]

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
