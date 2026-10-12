# -*- coding: utf-8 -*-
"""
Разовый сбор для коллеги (12.10): контент-роли уровня Senior+ на всех источниках радара.
Ничего не шлёт в Telegram, seen/health не трогает. Результат: discover/content_sweep.csv.

Источники: API-компании (основной + музтех), браузерные страницы (основной + музтех),
Himalayas (поиск по контент-запросам, все типы занятости), MBW, Хабр.
Фильтр по названию — список ролей коллеги; дальше по описанию вакансии размечаем
тип занятости, ремоут, US-only и уровень. Отсекаем только явный офис/гибрид и явный junior.
"""
import csv, os, re, sys, tempfile, time

import requests
from core import UA, TIMEOUT, load

OUT = "discover/content_sweep.csv"

# --- название: роли из списка коллеги ---
ROLE = re.compile(
    r"content[\s-]*(marketing|strateg\w*|manager|lead\b|leader|director)"
    r"|(head|director|vp|vice[\s-]*president)\s*(,|of|-|–)?\s*(content)"
    r"|контент[\s-]*(маркетолог|стратег|директор|лид)|руководитель\s+(отдела\s+)?контента", re.I)
NOT_ROLE = re.compile(r"\b(moderat\w*|design\w*|engineer\w*|creator|writer|producer|editor|video|ugc|"
                      r"localization|licens\w*|acquisition|partnerships?|operations|platform|safety|policy|"
                      r"intern(ship)?|junior|jr\.?|assistant|coordinator|trainee|entry)\b", re.I)

SENIOR_T = re.compile(r"\b(senior|sr\.?|lead|leader|head|director|vp|vice president|principal|chief|staff|"
                      r"руководитель|директор|ведущий|старший)\b", re.I)
SENIOR_D = re.compile(r"\b([5-9]|1[0-9])\+?\s*(-\s*\d+\s*)?years|senior[- ]level|seasoned", re.I)

TYPES = [("fractional", r"\bfractional\b"), ("freelance", r"\bfree[\s-]?lance\w*"),
         ("part-time", r"\bpart[\s-]?time\b"), ("interim", r"\binterim\b"),
         ("contract", r"\bcontract(or|ed)?\b(?![\s-]*(manag|negot|law|review))|fixed[\s-]term|\b1099\b|\bb2b contract"),
         ("consulting", r"\bconsult(ant|ing)\b")]
FULLTIME = re.compile(r"\bfull[\s-]?time\b|\bpermanent\b|\bfte\b", re.I)
REMOTE = re.compile(r"\bremote\b|\bwork from (home|anywhere)\b|\bdistributed\b|удал[её]нн?о|\banywhere\b", re.I)
ONSITE = re.compile(r"\b(hybrid|on[\s-]?site|in[\s-]office|office[\s-]based|days? (a|per) week in (the )?office)\b", re.I)
US = re.compile(r"\b(united states|usa|u\.s\.|us[\s-]based|us only|us citizens?|authori[sz]ed to work in the (us|united states)|"
                r"est|pst|cst|mst|eastern time|pacific time)\b", re.I)
WW = re.compile(r"worldwide|anywhere|global|any (location|country|timezone)|europe|emea|latam|apac|cet|gmt|utc", re.I)


def classify(j, text):
    blob = " ".join([j.get("title", ""), j.get("location", ""), j.get("etype", ""), text])
    types = [n for n, rx in TYPES if re.search(rx, blob, re.I)]
    loc = j.get("location", "") or ""
    remote = "да" if (REMOTE.search(loc) or j.get("remote") is True) else \
             "нет" if ONSITE.search(loc) else \
             ("да" if REMOTE.search(text[:4000]) and not ONSITE.search(text[:4000]) else
              "гибрид/офис" if ONSITE.search(text[:4000]) else "не указано")
    us = "US-only?" if (US.search(loc) and not WW.search(loc)) or \
                      re.search(r"authori[sz]ed to work in the (us|united states)|us citizens? only|must (be|reside) in the (us|united states)", text, re.I) \
         else ("мир/EU" if WW.search(loc + " " + text[:3000]) else "")
    level = "по названию" if SENIOR_T.search(j["title"]) else ("по описанию" if SENIOR_D.search(text) else "не указан")
    return {"Тип": ", ".join(types) or ("full-time" if FULLTIME.search(blob) else "не указан"),
            "Ремоут": remote, "Гео": us, "Уровень": level}


# ---------- источники ----------
def api_jobs():
    import radar
    out = []
    for lst, slugs_f in [("lists/radar_companies.csv", "state/slugs.json"),
                         ("lists/music_radar_companies.csv", "state/slugs_music.json")]:
        slugs = load(slugs_f, {})
        for r in csv.DictReader(open(lst, encoding="utf-8-sig")):
            name, link, ats = r["Name"].strip(), r["Link"].strip(), (r.get("ATS") or "").strip()
            if ats not in radar.HANDLERS: continue
            try:
                slug, js = radar.fetch_company(link, ats, slugs, name)
            except Exception as e:
                print(f"  ⚠ {name}: {type(e).__name__}"); continue
            for j in js or []:
                if j.get("title") and j.get("url"):
                    out.append({**j, "company": name, "ats": ats, "slug": slug, "source": f"API ({ats})"})
            time.sleep(0.2)
    print(f"API: {len(out)} вакансий", flush=True)
    return out


def ats_extra(job):
    """Ashby/Lever/Workable/Recruitee/SmartRecruiters отдают тип занятости и ремоут отдельными полями."""
    ats, s, jid = job.get("ats"), job.get("slug"), job.get("id")
    try:
        if ats == "Ashby":
            for x in requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{s}", headers=UA, timeout=TIMEOUT).json().get("jobs", []):
                if x.get("id") == jid:
                    return {"etype": x.get("employmentType") or "", "remote": x.get("isRemote")}
        if ats == "Lever":
            d = requests.get(f"https://api.lever.co/v0/postings/{s}/{jid}", headers=UA, timeout=TIMEOUT).json()
            return {"etype": (d.get("categories") or {}).get("commitment") or "", "remote": d.get("workplaceType") == "remote"}
        if ats == "SmartRecruiters":
            d = requests.get(f"https://api.smartrecruiters.com/v1/companies/{s}/postings/{jid}", headers=UA, timeout=TIMEOUT).json()
            return {"etype": (d.get("typeOfEmployment") or {}).get("label") or "", "remote": (d.get("location") or {}).get("remote")}
    except Exception:
        pass
    return {}


def browser_jobs():
    rows = []
    for f in ["lists/browser_companies.csv", "lists/music_browser_companies.csv"]:
        rows += list(csv.DictReader(open(f, encoding="utf-8-sig")))
    tmp = os.path.join(tempfile.gettempdir(), "sweep_companies.csv")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh); w.writerow(["Name", "Link"]); [w.writerow([r["Name"], r["Link"]]) for r in rows]
    # воркеры браузерного радара читают список и фильтр текстового режима из окружения
    os.environ.update(BROWSER_COMPANIES=tmp, BROWSER_EXTRA="", TEXT_MATCH=ROLE.pattern)
    import browser_radar
    class _NoHealth:
        def mark(self, *a, **k): pass
    js = browser_radar.run_isolated(browser_radar.load_companies(), _NoHealth())
    out = [{**j, "source": "браузер"} for j in {j["url"] + j["title"]: j for j in js}.values()]
    print(f"браузер: {len(out)} вакансий", flush=True)
    return out


def himalayas_jobs():
    out = {}
    for q in ["content marketing", "content strategy", "content strategist", "head of content",
              "content director", "content lead", "content manager", "director of content", "vp content"]:
        for page in range(1, 6):
            r = None
            for attempt in range(4):
                r = requests.get("https://himalayas.app/jobs/api/search", headers=UA, timeout=TIMEOUT,
                                 params={"q": q, "sort": "recent", "page": page})
                if r.status_code in (429, 500, 502, 503): time.sleep(15 * (attempt + 1)); continue
                break
            if r is None or r.status_code != 200: print(f"  ⚠ Himalayas «{q}» p{page}: {getattr(r,'status_code','?')}"); break
            d = r.json(); batch = d.get("jobs", [])
            for j in batch:
                locs = [x if isinstance(x, str) else (x.get("name") or "") for x in (j.get("locationRestrictions") or [])]
                loc = ", ".join(locs) if len(locs) <= 6 else f"{len(locs)} стран: " + ", ".join(locs[:4]) + "…"
                out[j.get("guid")] = {"title": j.get("title") or "", "company": j.get("companyName") or "",
                                      "url": j.get("applicationLink") or "", "location": (loc or "Worldwide") + " · Remote",
                                      "etype": j.get("employmentType") or "", "remote": True,
                                      "seniority": ", ".join(j.get("seniority") or []),
                                      "description": j.get("description") or j.get("excerpt") or "", "source": "Himalayas"}
            if not batch or page * (d.get("limit") or len(batch)) >= d.get("totalCount", 0): break
            time.sleep(1.2)
    print(f"Himalayas: {len(out)} вакансий", flush=True)
    return list(out.values())


def mbw_jobs():
    import mbw_radar
    out = []
    for page in range(1, 4):
        try: items = mbw_radar.fetch(page)
        except Exception: break
        out += [{**mbw_radar.to_job(it), "source": "MBW"} for it in items if not (it.get("meta") or {}).get("_filled")]
        if len(items) < 100: break
    print(f"MBW: {len(out)}", flush=True)
    return out


def habr_jobs():
    import boards_radar
    out = []
    for q in ["content", "контент"]:
        try: out += [{**j, "source": "Хабр"} for j in boards_radar.habr(q)]
        except Exception as e: print("  ⚠ Хабр:", e)
    return out


def main():
    steps = [("api", api_jobs), ("himalayas", himalayas_jobs), ("mbw", mbw_jobs), ("habr", habr_jobs), ("browser", browser_jobs)]
    only = set(sys.argv[1:])
    alljobs = []
    for name, fn in steps:
        if only and name not in only: continue
        try: alljobs += fn()
        except Exception as e: print(f"!! {name}: {type(e).__name__} {e}", flush=True)
    hits = [j for j in alljobs if ROLE.search(j.get("title") or "") and not NOT_ROLE.search(j.get("title") or "")]
    print(f"всего вакансий: {len(alljobs)} | контент-ролей по названию: {len(hits)}", flush=True)

    from scorer import describe
    rows, seen = [], set()
    for j in hits:
        key = (j["title"].lower().strip(), (j.get("company") or "").lower().strip())
        if key in seen: continue
        seen.add(key)
        j.update(ats_extra(j))
        try: text, _ = describe(j)
        except Exception: text = ""
        c = classify(j, text or "")
        if j.get("seniority") and c["Уровень"] == "не указан": c["Уровень"] = "Himalayas: " + j["seniority"]
        rows.append({"Компания": j.get("company", ""), "Вакансия": j["title"], "Локация": (j.get("location") or "")[:120],
                     **c, "Тип (поле ATS)": j.get("etype", ""), "Источник": j["source"], "Ссылка": j["url"],
                     "Описание (начало)": " ".join((text or "").split())[:300]})
        time.sleep(0.3)
    os.makedirs("discover", exist_ok=True)
    cols = ["Компания", "Вакансия", "Локация", "Тип", "Ремоут", "Гео", "Уровень", "Тип (поле ATS)", "Источник", "Ссылка", "Описание (начало)"]
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    print(f"записано: {len(rows)} -> {OUT}")


if __name__ == "__main__":
    main()
