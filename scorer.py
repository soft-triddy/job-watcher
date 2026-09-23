# -*- coding: utf-8 -*-
"""
Оценка новых вакансий под резюме. Общий модуль для radar / browser_radar / himalayas_radar.
LLM: GitHub Models (бесплатно, токен = встроенный GITHUB_TOKEN; в workflow нужно permissions: models: read).
Никогда не ломает радар: нет токена / лимит / ошибка -> вакансия уходит без оценки.

Публичное API:
  score_jobs(jobs, page=None)  -> проставляет job["score"] (dict) или None
  fmt_job(job)                 -> строки для Telegram
"""
import html as _html, json, os, re, time
import requests

MODEL    = os.environ.get("SCORER_MODEL", "openai/gpt-4.1-mini")
ENDPOINT = "https://models.github.ai/inference/chat/completions"
TOKEN    = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_MODELS_TOKEN")
PROFILE  = "scoring_profile.md"
MAX_PER_RUN = int(os.environ.get("SCORER_MAX", "40"))  # 150/день на всё — делим между радарами
JD_CHARS = 9000                                         # ~2.3K токенов; лимит модели 8K на вход
TIMEOUT  = 30
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124 Safari/537.36"}

RESUMES = {"growth": "Growth", "digital": "Digital", "gops": "Growth Ops",
           "mops": "MarOps", "crm": "CRM"}

RUBRIC = """You score a job vacancy for the candidate below. Output ONLY a JSON object, no prose:
{"resume": one of ["growth","digital","gops","mops","crm"],
 "fit": 0-10, "dull": 0-10,
 "hire": "green"|"yellow"|"red",
 "grade": "down"|"ok"|"up",
 "blocker": "" or max 4 words in English,
 "stop": "" or one of ["crypto","betting","gamedev"]}

fit = realistic chance to pass screening and do the job well, judged by hard requirements vs real experience.
  Missing a must-have skill/experience listed in "NOT in experience" costs a lot. 8-10 strong match, 5-7 partial,
  0-4 weak. Do not reward keyword overlap alone.
dull = how unappealing the role is for her (0 = dream, 10 = soul-crushing), per the Preferences section.
  Growth/Demand Gen/Inbound hands-on at B2B SaaS: 0-3. Digital Marketing Manager: 3-5.
  MarOps/CRM/Lifecycle: 4-6. Anything in the dull/unwanted list: 7-10.
hire = can she be hired from Kyrgyzstan as remote contractor/B2B or via worldwide EOR?
  green: worldwide/anywhere/any timezone, contractor OK, or CIS/Central Asia allowed.
  yellow: not stated, or region-limited without explicit residency (e.g. "EMEA", "CET overlap", "Europe").
  red: needs residency/work permit in specific countries, US/UK/EU-only payroll, hybrid, office, relocation required.
grade: down = junior/associate/coordinator/specialist-level scope; up = Head/Director/VP owning a big team or
  budget beyond her scale; ok otherwise (manager/senior/lead, small team).
blocker: the single most important hard gap if one exists (e.g. "Meta/TikTok media buying", "SQL/BI engineering",
  "B2C retention CRM", "native English", "10+ years leadership"); "" if none.
stop: set only if the COMPANY's core business is crypto/web3, betting/gambling/iGaming or game development.
resume: which resume version fits this vacancy best.
If only the title is available, still answer; be conservative (fit and hire toward the middle)."""

def _profile():
    txt = os.environ.get("SCORING_PROFILE")          # можно держать профиль в секрете репо
    if txt: return txt
    try: return open(PROFILE, encoding="utf-8").read()
    except Exception: return ""

# ---------- текст вакансии ----------
def _strip(h):
    h = h or ""
    if "&lt;" in h: h = _html.unescape(h)   # JSON-LD / Greenhouse отдают HTML экранированным
    h = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", h or "")
    h = re.sub(r"(?i)<br\s*/?>|</(p|li|div|h\d)>", "\n", h)
    t = _html.unescape(re.sub(r"<[^>]+>", " ", h))
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()

def _jsonld_desc(page_html):
    for m in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page_html or "", re.S | re.I):
        try: data = json.loads(m.strip())
        except Exception: continue
        stack = [data]
        while stack:
            n = stack.pop()
            if isinstance(n, list): stack.extend(n); continue
            if not isinstance(n, dict): continue
            t = n.get("@type", ""); t = ",".join(t) if isinstance(t, list) else str(t)
            if "JobPosting" in t and n.get("description"):
                return _strip(n["description"])
            stack.extend(v for v in n.values() if isinstance(v, (list, dict)))
    return ""

def _get_json(url):
    return requests.get(url, headers=UA, timeout=TIMEOUT).json()

def _from_api(job):
    """Точные API описаний там, где они есть (ats/slug/id проставляет radar.py)."""
    ats, s, jid = job.get("ats"), job.get("slug"), job.get("id")
    if not (ats and s and jid): return ""
    if ats == "Greenhouse":
        return _strip(_html.unescape(_get_json(f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs/{jid}").get("content", "")))
    if ats == "Lever":
        d = _get_json(f"https://api.lever.co/v0/postings/{s}/{jid}")
        parts = [d.get("descriptionPlain", "")] + [
            f"{l.get('text','')}: {_strip(l.get('content',''))}" for l in (d.get("lists") or [])] + [d.get("additionalPlain", "")]
        return "\n".join(p for p in parts if p)
    if ats == "Ashby":
        for j in _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{s}").get("jobs", []):
            if j.get("id") == jid:
                return j.get("descriptionPlain") or _strip(j.get("descriptionHtml", ""))
    if ats == "SmartRecruiters":
        d = _get_json(f"https://api.smartrecruiters.com/v1/companies/{s}/postings/{jid}")
        sec = (d.get("jobAd") or {}).get("sections") or {}
        return "\n".join(_strip((v or {}).get("text", "")) for v in sec.values() if isinstance(v, dict))
    if ats == "BambooHR":
        d = _get_json(f"https://{s}.bamboohr.com/careers/{jid}/detail")
        jo = ((d.get("result") or {}).get("jobOpening") or {})
        return _strip(jo.get("description", "")) + "\n" + str(jo.get("locationType", ""))
    if ats == "Workday":
        host, site = s.split("/", 1); tenant = host.split(".")[0]
        d = _get_json(f"https://{host}/wday/cxs/{tenant}/{site}{jid}")
        info = d.get("jobPostingInfo") or {}
        return _strip(info.get("jobDescription", "")) + "\n" + str(info.get("location", "")) + " " + str(info.get("remoteType", ""))
    return ""

def describe(job, page=None):
    """-> (текст, полный_ли). Порядок: готовое описание -> API ATS -> HTML страницы -> браузер."""
    if job.get("description"):
        return _strip(job["description"])[:JD_CHARS], True
    try:
        t = _from_api(job)
        if len(t) > 300: return t[:JD_CHARS], True
    except Exception:
        pass
    url = job.get("url") or ""
    try:
        h = requests.get(url, headers=UA, timeout=TIMEOUT).text
        t = _jsonld_desc(h) or _strip(h)
        if len(t) > 600: return t[:JD_CHARS], True
    except Exception:
        pass
    if page is not None:                     # JS-страница: рендерим тем же браузером
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            h = page.content()
            t = _jsonld_desc(h) or _strip(h)
            if len(t) > 600: return t[:JD_CHARS], True
        except Exception:
            pass
    return "", False

# ---------- LLM ----------
DIAG = []          # первая причина сбоя — уходит строкой в Telegram

class ScoreError(Exception): pass

_JSON_MODE = [True]
def _ask(system, user):
    body = {"model": MODEL, "temperature": 0, "max_tokens": 200,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}
    if _JSON_MODE[0]: body["response_format"] = {"type": "json_object"}
    r = requests.post(ENDPOINT, timeout=60, json=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                 "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    if r.status_code == 400 and _JSON_MODE[0] and "response_format" in r.text:
        _JSON_MODE[0] = False                  # модель не умеет json-режим — просим JSON текстом
        return _ask(system, user)
    if r.status_code == 429:
        raise RuntimeError("rate-limit: " + r.text[:150])
    if r.status_code >= 400:
        raise ScoreError(f"HTTP {r.status_code}: {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"]

def diag_line():
    return f"\n\n⚠️ оценщик: {DIAG[0]}" if DIAG else ""

def _clamp(v):
    try: return max(0, min(10, int(round(float(v)))))
    except Exception: return None

def parse(raw):
    """Строгий разбор ответа модели. Кривой ответ -> None (вакансия уйдёт без оценки)."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m: return None
    try: d = json.loads(m.group(0))
    except Exception: return None
    out = {"resume": d.get("resume") if d.get("resume") in RESUMES else None,
           "fit": _clamp(d.get("fit")), "dull": _clamp(d.get("dull")),
           "hire": d.get("hire") if d.get("hire") in ("green", "yellow", "red") else "yellow",
           "grade": d.get("grade") if d.get("grade") in ("down", "ok", "up") else "ok",
           "blocker": " ".join(str(d.get("blocker") or "").split()[:4]),
           "stop": d.get("stop") if d.get("stop") in ("crypto", "betting", "gamedev") else ""}
    if out["fit"] is None or out["dull"] is None: return None
    return out

def score_jobs(jobs, page=None):
    for j in jobs: j["score"] = None
    if not TOKEN:
        DIAG.append("нет GITHUB_TOKEN в env workflow")
        print("scorer: нет GITHUB_TOKEN — шлю без оценок"); return jobs
    system = RUBRIC + "\n\n=== CANDIDATE ===\n" + _profile()
    done = 0
    for j in jobs:
        if done >= MAX_PER_RUN:
            print(f"scorer: потолок {MAX_PER_RUN} за запуск — остальные без оценки"); break
        text, full = describe(j, page)
        user = (f"Company: {j.get('company','')}\nTitle: {j.get('title','')}\n"
                f"Location: {j.get('location','')}\n\n"
                + (f"Job description:\n{text}" if text else "Job description: NOT AVAILABLE (title only)"))
        try:
            s = parse(_ask(system, user))
        except RuntimeError as e:
            DIAG.append(str(e)[:200])
            print("scorer: лимит GitHub Models исчерпан — остальные без оценки"); break
        except Exception as e:
            msg = f"{type(e).__name__} {str(e)[:200]}"
            print(f"scorer: {j.get('company')}: {msg}"); s = None
            if not DIAG: DIAG.append(msg)
            if isinstance(e, ScoreError) and (" 401" in msg or " 403" in msg or " 404" in msg):
                break                          # доступ/модель — дальше бессмысленно
        else:
            if s is None and not DIAG: DIAG.append("модель вернула не-JSON")
        if s: s["title_only"] = not full
        j["score"] = s; done += 1
        time.sleep(4.5)                       # 15 запросов/мин на бесплатном уровне
    return jobs

# ---------- формат ----------
HIRE  = {"green": "🟢 найм", "yellow": "🟡 найм", "red": "🔴 найм"}
GRADE = {"down": "⬇️ грейд", "ok": "✅ грейд", "up": "⬆️ грейд"}
STOP  = {"crypto": "крипта", "betting": "беттинг", "gamedev": "геймдев"}

def fmt_job(j, suffix=""):
    loc = f" — {j['location']}" if j.get("location") else ""
    head = f"• {j['company']}: {j['title']}{loc}"
    s = j.get("score")
    if not s:
        return f"{head}\n  (без оценки)\n{j['url']}{suffix}"
    l1 = f"  📄 {RESUMES.get(s['resume'], '?')} · прохожу {s['fit']}/10 · уныло {s['dull']}/10"
    if s.get("title_only"): l1 += " · по тайтлу"
    flags = [HIRE[s["hire"]], GRADE[s["grade"]]]
    if s.get("blocker"): flags.append(f"⚠️ {s['blocker']}")
    if s.get("stop"): flags.append(f"🚫 {STOP[s['stop']]}")
    return f"{head}\n{l1}\n  {' · '.join(flags)}\n{j['url']}{suffix}"

def rank(jobs):
    """Сначала оценённые: стоп-индустрия и красный найм вниз, дальше по fit-dull."""
    def key(j):
        s = j.get("score")
        if not s: return (1, 0)
        pen = (10 if s.get("stop") else 0) + (5 if s["hire"] == "red" else 0)
        return (0, -(s["fit"] - s["dull"] - pen))
    return sorted(jobs, key=key)
