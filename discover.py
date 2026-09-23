# -*- coding: utf-8 -*-
"""
Разовый разведчик карьерных страниц (запуск вручную в GitHub Actions).
Для каждой компании из discover_input.csv (Name, Site):
  1) открывает сайт, ищет ссылку на карьеру (careers/jobs/vacancies/join...) + пробует типовые пути;
  2) определяет ATS по HTML и финальному URL;
  3) если ATS не видно в статике — рендерит страницу Playwright и слушает сетевые запросы
     (там видно boards-api.greenhouse.io, api.ashbyhq.com и т.п.);
  4) для ATS с публичным API — подбирает слаг и ДЕЛАЕТ живой запрос через обработчики radar.py,
     считает вакансии и маркетинговые вакансии.
Выход: discover_result.csv — одна строка на компанию с корзиной:
  api      -> в radar_companies.csv
  browser  -> в browser_companies.csv
  linkedin -> карьерной страницы нет / только LinkedIn
  check    -> что-то нашлось, но надо глянуть руками
"""
import csv, re, sys, time
from urllib.parse import urljoin, urlparse
import requests
from radar import HANDLERS, candidates, is_marketing, _affinity, slug_from_url, UA

IN_FILE, OUT_FILE = "discover_input.csv", "discover_result.csv"
TIMEOUT = 20

# ---- сигнатуры ATS (подстрока в URL/HTML/сетевых запросах -> ATS) ----
API_ATS = [  # порядок = приоритет
 ("Greenhouse", ["greenhouse.io"]),
 ("Lever", ["lever.co"]),
 ("Ashby", ["ashbyhq.com"]),
 ("Workable", ["apply.workable.com", "workable.com/api", ".workable.com"]),
 ("Recruitee", ["recruitee.com"]),
 ("BambooHR", ["bamboohr.com"]),
 ("Breezy", ["breezy.hr"]),
 ("SmartRecruiters", ["smartrecruiters.com"]),
 ("Rippling", ["ats.rippling.com", "api.rippling.com/platform/api/ats"]),
 ("Teamtailor", ["teamtailor.com", "teamtailor-cdn"]),
 ("Pinpoint", ["pinpointhq.com"]),
 ("Workday", ["myworkdayjobs.com"]),
]
OTHER_ATS = [  # ATS без обработчика в radar.py -> браузерный радар
 ("Personio", ["jobs.personio"]),
 ("Comeet", ["comeet.co", "comeet.com"]), ("HiBob", ["careers.hibob.com"]),
 ("PeopleForce", ["peopleforce.io"]), ("Huntflow", ["huntflow"]),
 ("Zoho Recruit", ["zohorecruit"]), ("Join", ["join.com/companies"]),
 ("Freshteam", ["freshteam.com"]), ("JazzHR", ["applytojob.com"]),
 ("Gem", ["jobs.gem.com"]), ("Getro", ["getro.com"]), ("Wellfound", ["wellfound.com"]),
 ("YC", ["ycombinator.com/companies"]), ("iCIMS", ["icims.com"]),
 ("SuccessFactors", ["successfactors", "sapsf"]), ("Jobvite", ["jobvite.com"]),
 ("Taleo", ["taleo.net"]), ("UKG", ["ukg.net"]), ("Oracle", ["oraclecloud.com/hcmui"]),
 ("Avature", ["avature.net"]), ("Eightfold", ["eightfold.ai"]),
 ("Phenom", ["phenompeople", "phenom.com"]), ("Homerun", ["homerun.co"]),
 ("Manatal", ["careers-page.com"]), ("Skailer", ["skailer.com"]),
 ("Notion", ["notion.site"]), ("hh.ru", ["hh.ru/employer", "hh.ru/vacancy"]),
 ("Dover", ["app.dover.com"]), ("Factorial", ["factorialhr.com/job_posting", ".factorialhr.com"]),
]

CAREER_WORDS = re.compile(r"career|jobs?\b|vacanc|join[- ]?us|join[- ]the[- ]team|hiring|"
                          r"work[- ]with[- ]us|open[- ]positions|вакансии|карьера|работа у нас", re.I)
COMMON_PATHS = ["/careers", "/careers/", "/jobs", "/company/careers", "/about/careers",
                "/join-us", "/vacancies", "/career", "/en/careers", "/company/jobs"]
BAD_LINK = re.compile(r"linkedin\.com|facebook\.|twitter\.|x\.com|instagram\.|youtube\.|"
                      r"/blog/|/news/|mailto:|tel:", re.I)

S = requests.Session(); S.headers.update(UA)

def norm_site(s):
    s = (s or "").strip()
    if not s or "не нашла" in s: return ""
    if not s.startswith("http"): s = "https://" + s
    return s.rstrip("/")

def get(url):
    try:
        r = S.get(url, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code >= 400: return None, url
        ct = r.headers.get("content-type", "")
        if "html" not in ct and "text" not in ct: return None, r.url
        return r.text, r.url
    except Exception:
        return None, url

def detect(blob):
    """-> (ats, is_api) по первому совпадению; API-ATS приоритетнее."""
    low = (blob or "").lower()
    for ats, sigs in API_ATS:
        if any(s in low for s in sigs): return ats, True
    for ats, sigs in OTHER_ATS:
        if any(s in low for s in sigs): return ats, False
    return "", False

def career_links(html, base):
    out = []
    for href, text in re.findall(r'<a[^>]+href=["\']([^"\'#]+)[^>]*>(.*?)</a>', html or "", re.I | re.S):
        text = re.sub(r"<[^>]+>", " ", text)
        if BAD_LINK.search(href): continue
        if CAREER_WORDS.search(href) or CAREER_WORDS.search(text):
            u = urljoin(base, href)
            if u not in out: out.append(u)
    # ATS-хосты прямо на главной — лучший кандидат
    out.sort(key=lambda u: 0 if detect(u)[0] else 1)
    return out[:6]

def render(url):
    """Playwright: финальный URL + HTML + все сетевые запросы. None, если браузера нет."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    reqs = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(args=["--no-sandbox"])
            pg = b.new_context(user_agent=UA["User-Agent"]).new_page()
            pg.on("request", lambda r: reqs.append(r.url))
            try: pg.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception: pass
            pg.wait_for_timeout(5000)
            html = pg.content(); final = pg.url
            frames = " ".join(f.url for f in pg.frames)
            b.close()
        return final, html, " ".join(reqs) + " " + frames
    except Exception:
        return None

def probe_api(ats, url, html, name):
    """Подбираем слаг и проверяем живым запросом. -> (slug, jobs, mk) или (None,..)."""
    own = None; foreign = None
    from_url = (slug_from_url(url, ats) or "").lower()
    for slug in candidates(url, ats, html, name)[:10]:
        try: js = HANDLERS[ats](slug)
        except Exception: continue
        mine = slug.lower() == from_url or _affinity(slug, url, name) > 0
        mk = [j for j in js if j.get("title") and is_marketing(j["title"], j.get("location", ""))]
        hit = (slug, len(js), len(mk))
        if mine and js: return hit
        if mine and own is None: own = hit
        if not mine and foreign is None: foreign = hit
    return own or foreign or (None, 0, 0)

def analyse(name, site):
    row = {"Name": name, "Site": site, "Careers": "", "ATS": "", "Slug": "",
           "Bucket": "", "Jobs": "", "Marketing": "", "Note": ""}
    if not site:
        row.update(Bucket="linkedin", Note="нет сайта"); return row
    home, home_final = get(site)
    if home is None:
        home, home_final = get(site.replace("https://", "https://www."))
    if home is None:
        row.update(Bucket="check", Note="сайт не открылся"); return row

    # кандидаты карьерной страницы: ссылки с главной + типовые пути
    cands = career_links(home, home_final)
    root = f"{urlparse(home_final).scheme}://{urlparse(home_final).netloc}"
    cands += [root + p for p in COMMON_PATHS if root + p not in cands]

    careers = careers_html = None
    for c in cands:
        if detect(c)[0]:                         # ссылка прямо на ATS
            h, f = get(c); careers, careers_html = (f or c), (h or "")
            break
        h, f = get(c)
        if not h: continue
        if detect(f + " " + h)[0]:               # страница с ATS — лучше не будет
            careers, careers_html = f, h; break
        # SPA отдают главную на любой путь -> берём первую подходящую, дальше не перетираем
        if careers is None and f.rstrip("/") != home_final.rstrip("/") and CAREER_WORDS.search(f + " " + h[:5000]):
            careers, careers_html = f, h
    if not careers:
        # на главной мог сидеть эмбед
        ats, _ = detect(home_final + " " + home)
        if ats: careers, careers_html = home_final, home
        else:
            row.update(Bucket="linkedin", Note="карьерная страница не найдена"); return row
    row["Careers"] = careers

    ats, is_api = detect(careers + " " + careers_html)
    if not ats:                                   # JS-страница — слушаем сеть
        r = render(careers)
        if r:
            final, html2, net = r
            ats, is_api = detect(final + " " + net + " " + html2)
            if ats: careers_html = html2 + " " + net
    row["ATS"] = ats or "custom"

    if is_api:
        slug, n, mk = probe_api(ats, careers, careers_html, name)
        if slug is not None:
            row.update(Slug=slug, Jobs=n, Marketing=mk, Bucket="api")
            if _affinity(slug, careers, name) == 0 and slug.lower() != (slug_from_url(careers, ats) or "").lower():
                row["Note"] = "слаг не похож на компанию — проверить"
            return row
        row.update(Bucket="browser", Note=f"{ats}: слаг не подобрался")
        return row
    row["Bucket"] = "browser"
    return row

def main():
    rows = list(csv.DictReader(open(IN_FILE, encoding="utf-8-sig")))
    only = sys.argv[1:]                       # можно передать имена для точечного перезапуска
    out = []
    for i, r in enumerate(rows, 1):
        name, site = r["Name"].strip(), norm_site(r.get("Site"))
        if only and name not in only: continue
        try: res = analyse(name, site)
        except Exception as e:
            res = {"Name": name, "Site": site, "Bucket": "check", "Note": f"{type(e).__name__}"}
        out.append(res)
        print(f"[{i}/{len(rows)}] {name}: {res.get('Bucket')} {res.get('ATS','')} "
              f"{res.get('Slug','')} jobs={res.get('Jobs','')} mk={res.get('Marketing','')} {res.get('Note','')}")
        time.sleep(0.3)
    cols = ["Name", "Site", "Careers", "ATS", "Slug", "Bucket", "Jobs", "Marketing", "Note"]
    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(out)
    from collections import Counter
    print("\nИтого:", dict(Counter(r["Bucket"] for r in out)))

if __name__ == "__main__":
    main()
