# -*- coding: utf-8 -*-
"""
Himalayas как источник вакансий (Фаза 1 — слой 1).
Гейт: worldwide-friendly (hire-anywhere) + employment_type + твои роли.
Фильтр маркетинга и отправку берёт из radar.py (единый источник правил).
CIS-скоринг (слой 2) будет отдельной фазой — здесь его НЕТ.
Схема API из первоисточника: github.com/Himalayas-App/remote-jobs-api  (ключ не нужен)
"""
import json, os, sys, time
import requests
from radar import is_marketing, send, load

BASE  = "https://himalayas.app/jobs/api/search"
STATE = "seen_himalayas.json"

# --- настройки гейта (правишь тут) ---
QUERIES = ["marketing", "growth", "demand", "seo", "crm", "lifecycle"]  # широкие — охват; сужает уже фильтр
WORLDWIDE = True                 # hire-anywhere — твой жёсткий гейт
EMPLOYMENT_TYPES = "Full Time"   # строго фултайм (EOR/контракт-оформленные роли не попадут)
MAX_PAGES = 5                    # вежливый потолок пагинации на запрос
PAUSE = 1.0                      # пауза между запросами (rate limit 429)
TIMEOUT = 30
UA = {"User-Agent": "job-radar personal use (+telegram alerts)"}

def search(q, page):
    params = {"q": q, "sort": "recent", "page": page}
    if WORLDWIDE: params["worldwide"] = "true"
    if EMPLOYMENT_TYPES: params["employment_type"] = EMPLOYMENT_TYPES
    r = requests.get(BASE, params=params, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def loc_str(job):
    out = []
    for x in (job.get("locationRestrictions") or []):
        if isinstance(x, str): out.append(x)
        elif isinstance(x, dict): out.append(x.get("name") or x.get("country") or "")
    return ", ".join(p for p in out if p)

def collect():
    jobs = {}
    for q in QUERIES:
        page = 1
        while page <= MAX_PAGES:
            try:
                data = search(q, page)
            except Exception as e:
                print(f"  !! '{q}' page {page}: {type(e).__name__} {str(e)[:60]}")
                break
            batch = data.get("jobs", [])
            if not batch: break
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
    dump_all = "--all" in sys.argv
    print(f"=== himalayas | режим: {'ВСЕ текущие' if dump_all else 'только новые'} | "
          f"worldwide={WORLDWIDE} type={EMPLOYMENT_TYPES} ===")
    seen_existed = os.path.exists(STATE)
    seen = load(STATE, {})

    allj = collect()
    mk = [j for j in allj if j.get("title") and j.get("url")
          and not _is_crypto_company(j)
          and is_marketing(j["title"], j.get("location", ""))]
    new = [j for j in mk if j["guid"] not in seen]
    for j in mk: seen[j["guid"]] = j["title"]
    json.dump(seen, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"собрано (contractor+worldwide, до фильтра): {len(allj)} | маркетинговых: {len(mk)} | новых: {len(new)}")

    def fmt(items, header):
        from scorer import score_jobs, fmt_job, rank
        score_jobs(items)
        from scorer import diag_line
        return "\n\n".join([header.rstrip()] + [fmt_job(j, " (via Himalayas)") for j in rank(items)]) + diag_line()

    if dump_all:
        if mk: send(fmt(mk, f"🏔 Himalayas: все текущие ({len(mk)}):\n"))
        else:  send("🏔 Himalayas: маркетинговых worldwide-вакансий не найдено.")
        return
    if not seen_existed:
        send(f"🏔 Himalayas-радар включён. Слежу за {len(mk)} worldwide-вакансиями. Дальше только новые.")
        print("Первый запуск: сводка отправлена."); return
    if new:
        send(fmt(new, f"🏔 Новые вакансии (Himalayas, {len(new)}):\n")); print(f"Отправлено новых: {len(new)}")
    else:
        print("Новых нет.")

if __name__ == "__main__":
    main()
