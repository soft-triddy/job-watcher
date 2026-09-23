# -*- coding: utf-8 -*-
"""
Оценка новых вакансий под резюме. Общий модуль для radar / browser_radar / himalayas_radar.
LLM: Gemini API (бесплатный тариф Google AI Studio), OpenAI-совместимый эндпоинт.
Ключ — секрет репо GEMINI_API_KEY. (GitHub Models закрыт 30.07.2026.)
Никогда не ломает радар: нет ключа / лимит / ошибка -> вакансия уходит без оценки.

Публичное API:
  score_jobs(jobs, page=None)  -> проставляет job["score"] (dict) или None
  fmt_job(job)                 -> строки для Telegram
"""
import html as _html, json, os, re, time
import requests

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
TOKEN    = os.environ.get("GEMINI_API_KEY")
# цепочка моделей: если модель недоступна (404) или упёрлась в лимит (429) — пробуем следующую
MODELS   = [m.strip() for m in os.environ.get(
    "SCORER_MODELS", "gemini-3.8-flash,gemini-3.5-flash,gemini-3.5-flash-lite,gemini-2.5-flash").split(",") if m.strip()]
MODEL    = MODELS[0]
PROFILE  = "scoring_profile.md"
MAX_PER_RUN = int(os.environ.get("SCORER_MAX", "40"))  # потолок оценок за один запуск радара
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

_M = [0]                                   # индекс текущей модели в цепочке
_REASON = ["none"]
def _ask(system, user):
    while _M[0] < len(MODELS):
        model = MODELS[_M[0]]
        body = {"model": model, "temperature": 0, "max_tokens": 2000,   # запас: думающие модели тратят токены на рассуждение
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}
        if _REASON[0]: body["reasoning_effort"] = _REASON[0]   # оценке «размышления» не нужны — так в разы быстрее
        for attempt in range(3):          # 5xx (перегрузка) — временное: две повторные попытки с паузой
            try:
                r = requests.post(ENDPOINT, timeout=45, json=body,
                                  headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
            except requests.RequestException as e:
                r = None; err = e
            if r is not None and r.status_code < 500: break
            if attempt < 2: time.sleep((5, 15)[attempt])
        if r is None or r.status_code >= 500:
            code = r.status_code if r is not None else type(err).__name__
            print(f"scorer: {model} -> {code} (перегрузка), пробую следующую модель")
            _M[0] += 1; continue
        if r.status_code == 400 and "reasoning" in r.text.lower() and _REASON[0]:
            _REASON[0] = "low" if _REASON[0] == "none" else None     # модель не принимает — ослабляем/убираем
            continue
        if r.status_code in (404, 429) or (r.status_code == 400 and "model" in r.text.lower()):
            print(f"scorer: {model} -> HTTP {r.status_code}, пробую следующую модель")
            _M[0] += 1; continue
        if r.status_code >= 400:
            raise ScoreError(f"HTTP {r.status_code} [{model}]: {' '.join(r.text[:200].split())}")
        try:
            content = r.json()["choices"][0]["message"]["content"]
        except Exception:
            raise ScoreError(f"HTTP {r.status_code} [{model}] не-JSON: {' '.join((r.text or '<пусто>')[:160].split())}")
        if not content:
            raise ScoreError(f"[{model}] пустой ответ (finish: {r.json()['choices'][0].get('finish_reason')})")
        return content
    raise RuntimeError("все модели из цепочки недоступны или в лимите: " + ", ".join(MODELS))

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
        DIAG.append("нет GEMINI_API_KEY — добавь секрет в репо")
        print("scorer: нет GEMINI_API_KEY — шлю без оценок"); return jobs
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
            print("scorer: лимиты исчерпаны — остальные без оценки"); break
        except Exception as e:
            msg = f"{type(e).__name__} {str(e)[:200]}"
            print(f"scorer: {j.get('company')}: {msg}"); s = None
            if not DIAG: DIAG.append(msg)
            if isinstance(e, ScoreError) and (" 401" in msg or " 403" in msg):
                break                          # доступ/модель — дальше бессмысленно
        else:
            if s is None and not DIAG: DIAG.append("модель вернула не-JSON")
        if s: s["title_only"] = not full
        j["score"] = s; done += 1
        time.sleep(4)                         # бесплатный тариф: запас по запросам в минуту
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
