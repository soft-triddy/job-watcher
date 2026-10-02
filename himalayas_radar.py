# -*- coding: utf-8 -*-
"""
Himalayas: публичный API вакансий (без ключа, схема — github.com/Himalayas-App/remote-jobs-api).
Берём только worldwide + Full Time по широким запросам, дальше — общий фильтр маркетинга.
Крипто-компании отсекаются по категориям Himalayas.
"""
import os, time

import requests
from core import is_marketing, near_misses, load, save, report, Health

BASE  = "https://himalayas.app/jobs/api/search"
STATE = os.environ.get("HIMALAYAS_STATE", "state/seen_himalayas.json")
HEALTH = "state/health_himalayas.json"
REJECTED = "state/rejected_himalayas.json"
LABEL = "🏔 Himalayas"

# --- настройки гейта (правишь тут) ---
QUERIES = ["marketing", "growth", "demand", "seo", "crm", "lifecycle"]  # широкие — охват; сужает уже фильтр
WORLDWIDE = True                 # hire-anywhere — твой жёсткий гейт
EMPLOYMENT_TYPES = "Full Time"   # строго фултайм (EOR/контракт-оформленные роли не попадут)
MAX_PAGES = 5                    # вежливый потолок пагинации на запрос
PAUSE = 1.0                      # пауза между запросами (rate limit 429)
TIMEOUT = 30
UA = {"User-Agent": "job-radar personal use (+telegram alerts)"}

def search(q, page):
    """429 (лимит) и 5xx — ждём и повторяем; раньше запрос молча обрывался на первой же 429,
       и всё, что глубже первой страницы, не доходило."""
    params = {"q": q, "sort": "recent", "page": page}
    if WORLDWIDE: params["worldwide"] = "true"
    if EMPLOYMENT_TYPES: params["employment_type"] = EMPLOYMENT_TYPES
    for attempt in range(4):
        r = requests.get(BASE, params=params, headers=UA, timeout=TIMEOUT)
        if r.status_code == 429 or r.status_code >= 500:
            wait = int(r.headers.get("Retry-After") or 0) or 15 * (attempt + 1)
            print(f"  … '{q}' p{page}: HTTP {r.status_code}, жду {min(wait, 60)}с")
            time.sleep(min(wait, 60)); continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    raise RuntimeError(f"HTTP {r.status_code} после повторов")

def loc_str(job):
    out = []
    for x in (job.get("locationRestrictions") or []):
        if isinstance(x, str): out.append(x)
        elif isinstance(x, dict): out.append(x.get("name") or x.get("country") or "")
    return ", ".join(p for p in out if p)

def collect(health):
    jobs = {}
    for q in QUERIES:
        page = 1; got_q = 0; err = ""
        while page <= MAX_PAGES:
            try:
                data = search(q, page)
            except Exception as e:
                err = f"стр. {page}: {type(e).__name__} {str(e)[:60]}"
                print(f"  !! '{q}' {err}")
                break
            batch = data.get("jobs", [])
            if not batch: break
            got_q += len(batch)
            for j in batch:
                g = j.get("guid")
                if not g: continue
                cats = (j.get("categories") or []) + (j.get("parentCategories") or [])
                jobs[g] = {"title": j.get("title"), "company": j.get("companyName"),
                           "url": j.get("applicationLink"), "location": loc_str(j),
                           "slug": j.get("companySlug"), "guid": g,
                           "cats": " ".join(str(c) for c in cats),
                           "description": j.get("description") or j.get("excerpt") or ""}
            total = data.get("totalCount", 0)
            got = (page * (data.get("limit") or len(batch)))
            if got >= total: break
            page += 1; time.sleep(PAUSE)
        health.mark(f"запрос «{q}»", jobs=got_q, err=err)
        time.sleep(PAUSE)
    return list(jobs.values())

# крипта на уровне КОМПАНИИ (титул чистый, а контора крипта) — Himalayas даёт categories
CRYPTO_TERMS = ["crypto","web3","blockchain","defi","stablecoin","ethereum","bitcoin","nft","token"]
CRYPTO_COMPANIES = {"tether","tether operations limited","filecoin","filecoin foundation",
                    "chainstack","immunefi"}

def _is_crypto_company(job):
    name=(job.get("company") or "").lower().strip()
    if name in CRYPTO_COMPANIES: return True
    blob=(name+" "+(job.get("cats") or "")).lower()
    return any(t in blob for t in CRYPTO_TERMS)

def main():
    seen = load(STATE, {})
    health = Health(HEALTH, LABEL, empty_is_bad=True)
    allj = collect(health)
    mk = [j for j in allj if j.get("title") and j.get("url")
          and not _is_crypto_company(j) and is_marketing(j["title"], j.get("location", ""))]
    save(REJECTED, near_misses([j for j in allj if not _is_crypto_company(j)]))
    print(f"=== Himalayas | собрано (worldwide, {EMPLOYMENT_TYPES}): {len(allj)} | маркетинг: {len(mk)} ===")
    report(mk, seen, STATE, label=LABEL, key="guid", suffix=" (via Himalayas)")
    health.finish({"jobs_total": len(allj), "marketing": len(mk)})

if __name__ == "__main__":
    main()
