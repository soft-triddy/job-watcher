# -*- coding: utf-8 -*-
"""
Оценка вакансий под резюме: какое резюме подходит, «прохожу» и «уныло» из 10, флаги ремоута,
грейда, главного блокера и стоп-индустрии. LLM — Gemini API (бесплатный тариф), OpenAI-совместимый
эндпоинт, ключ в секрете GEMINI_API_KEY. Профиль кандидата — scoring_profile.md.
Никогда не ломает радар: нет ключа / лимит / таймаут -> вакансия уходит без оценки,
а причина — строкой ⚠️ в конце сообщения.

Публичное API:
  score_jobs(jobs, page=None)  -> проставляет job["score"] (dict) или None
  fmt_job(job)                 -> строки для Telegram
"""
import html as _html, json, os, re, time
import requests

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
TOKEN    = os.environ.get("GEMINI_API_KEY")
# цепочка моделей: если модель недоступна (404) или упёрлась в лимит (429) — пробуем следующую
# (gemini-2.5-flash убрана 06.10.2026: отвечает 404)
MODELS   = [m.strip() for m in os.environ.get(
    "SCORER_MODELS", "gemini-3.8-flash,gemini-3.5-flash,gemini-3.5-flash-lite").split(",") if m.strip()]
PROFILE  = "scoring_profile.md"
MAX_PER_RUN = int(os.environ.get("SCORER_MAX", "40"))  # потолок оценок за один запуск радара
BUDGET   = int(os.environ.get("SCORER_BUDGET", "900"))  # сек на ВСЕ оценки за запуск (15 мин), дальше шлём без оценки
PER_JOB  = int(os.environ.get("SCORER_PER_JOB", "120")) # сек на одну вакансию, включая повторы
BATCH    = int(os.environ.get("SCORER_BATCH", "8"))     # вакансий в одном запросе к модели
PER_BATCH = int(os.environ.get("SCORER_PER_BATCH", "240"))  # сек на одну пачку, включая повторы
BATCH_JD = 3000                                         # описание в пачке короче, чтобы пачка влезла
_DL = [0.0]                                            # дедлайн текущей вакансии
JD_CHARS = 9000                                         # обрезаем длинные описания — до сути хватает
TIMEOUT  = 30
from core import UA

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
  green: ONLY if the text explicitly says worldwide/anywhere/any location, contractors/B2B welcome, or allows
    CIS/Central Asia. Never green by default or because the role "sounds remote".
  yellow: remote but region-limited without explicit residency (e.g. "EMEA", "Europe", "CET overlap"), or the
    text is silent on where she can be hired from.
  red: needs residency/work permit in specific countries, US/UK/EU-only payroll, hybrid, office, relocation required.
  The Location field is a hard signal: if it names a specific city or country (e.g. "United States", "London",
  "Toronto") and the text does not explicitly say remote-worldwide, hire is red for US/UK/Canada/Australia and
  at best yellow for anywhere else.
grade: down = junior/associate/coordinator/specialist-level scope; up = Head/Director/VP owning a big team or
  budget beyond her scale; ok otherwise (manager/senior/lead, small team).
blocker: the single most important hard gap if one exists (e.g. "Meta/TikTok media buying", "SQL/BI engineering",
  "B2C retention CRM", "native English", "10+ years leadership"); "" if none.
stop: set only on explicit evidence in the text that the COMPANY's core business is crypto/web3 (tokens,
  blockchain, DeFi, exchange), betting/gambling/iGaming (casino, sportsbook) or game development (studio making
  games). AI, analytics, devtools, observability, adtech or fintech are NOT stop. When unsure, leave "".
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
        return _strip(_html.unescape(_get_json(("https://boards-api.eu.greenhouse.io/v1/boards/" + s[3:] if s.startswith("eu:") else "https://boards-api.greenhouse.io/v1/boards/" + s) + f"/jobs/{jid}").get("content", "")))
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
_LAST = {}                                 # модель -> последний код ответа (для диагностики)

def _post(model, body):
    """Один запрос с повторами: 5xx (перегрузка) и 429 (минутный лимит) — временные, ждём и повторяем."""
    r = None
    for attempt in range(2):
        if time.time() > _DL[0]: return r   # время на вакансию вышло — не ждём
        try:
            r = requests.post(ENDPOINT, timeout=40, json=body,
                              headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
        except requests.RequestException as e:
            r = None; _LAST[model] = type(e).__name__
        if r is not None:
            _LAST[model] = r.status_code
            if r.status_code < 500 and r.status_code != 429: return r
        if attempt < 1:
            wait = 10
            if r is not None and r.status_code == 429:
                m = re.search(r'retry(?:Delay|_delay)?\W+(\d+)', r.text or "", re.I)
                wait = min(int(m.group(1)) + 1, 30) if m else 20
            time.sleep(max(0, min(wait, _DL[0] - time.time())))
    return r

def _ask(system, user, max_tokens=2000):
    rounds = 0
    while True:
        if time.time() > _DL[0]:
            raise TimeoutError("время на вакансию вышло: " + ", ".join(f"{m}={_LAST.get(m,'?')}" for m in MODELS))
        if _M[0] >= len(MODELS):             # все в лимите — минута паузы и заново с первой модели
            rounds += 1
            if rounds > 1:
                raise RuntimeError("все модели в лимите: " + ", ".join(f"{m}={_LAST.get(m,'?')}" for m in MODELS))
            print("scorer: все модели в лимите, пауза 30с"); time.sleep(max(0, min(30, _DL[0]-time.time()))); _M[0] = 0
        model = MODELS[_M[0]]
        body = {"model": model, "temperature": 0, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}
        if _REASON[0]: body["reasoning_effort"] = _REASON[0]   # оценке «размышления» не нужны — так быстрее
        r = _post(model, body)
        if r is None or r.status_code >= 500 or r.status_code in (404, 429):
            print(f"scorer: {model} -> {_LAST.get(model)}, пробую следующую модель")
            _M[0] += 1; continue
        if r.status_code == 400 and _REASON[0]:
            print(f"scorer: {model} не принял reasoning_effort — убираю параметр")
            _REASON[0] = None; continue
        if r.status_code == 400 and "model" in r.text.lower():
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
    return parse_obj(d)

def parse_batch(raw, n):
    """Ответ на пачку: JSON-массив из n объектов с полем "n" (1..n). -> список длины n (None = не разобрали)."""
    m = re.search(r"\[.*\]", raw or "", re.S)
    out = [None] * n
    if not m: return out
    try: arr = json.loads(m.group(0))
    except Exception: return out
    for i, d in enumerate(arr if isinstance(arr, list) else []):
        if not isinstance(d, dict): continue
        k = d.get("n", i + 1)
        try: k = int(k) - 1
        except Exception: continue
        if 0 <= k < n and out[k] is None: out[k] = parse_obj(d)
    return out

def parse_obj(d):
    out = {"resume": d.get("resume") if d.get("resume") in RESUMES else None,
           "fit": _clamp(d.get("fit")), "dull": _clamp(d.get("dull")),
           "hire": d.get("hire") if d.get("hire") in ("green", "yellow", "red") else "yellow",
           "grade": d.get("grade") if d.get("grade") in ("down", "ok", "up") else "ok",
           "blocker": " ".join(str(d.get("blocker") or "").split()[:4]),
           "stop": d.get("stop") if d.get("stop") in ("crypto", "betting", "gamedev") else ""}
    if out["fit"] is None or out["dull"] is None: return None
    return out

WW = re.compile(r"worldwide|anywhere|global|any location|any timezone|remote\s*\(?ww|\bww\b", re.I)
HARD_RED = re.compile(r"\b(united states|usa|u\.s\.|us|united kingdom|uk|england|london|canada|toronto|"
                      r"vancouver|australia|sydney|new york|san francisco|seattle|bellevue|austin|boston|chicago)\b", re.I)

def _guard_remote(job, s):
    """Жёсткая проверка ремоута по полю локации — модели тут верим меньше, чем полю."""
    loc = (job.get("location") or "").strip()
    if re.search(r"kyrgyz|кыргыз|киргиз", loc, re.I):   # Кыргызстан прямо в списке разрешённых стран
        s["hire"] = "green"; return s
    if not loc or WW.search(loc): return s
    if re.match(r"\d+ стран", loc): return s            # длинный список стран — одному «UK» в нём не верим
    if HARD_RED.search(loc): s["hire"] = "red"
    elif s["hire"] == "green" and not re.search(r"\bremote\b", loc, re.I): s["hire"] = "yellow"
    return s

BATCH_NOTE = ("You will receive several vacancies, each starting with '### n'. Score each one independently "
              "by the rules above. Output ONLY a JSON array with one object per vacancy, in the same order, "
              'each object having the same fields plus "n" (the vacancy number). No prose.')

def _user(j, text):
    return (f"Company: {j.get('company','')}\nTitle: {j.get('title','')}\nLocation: {j.get('location','')}\n\n"
            + (f"Job description:\n{text}" if text else "Job description: NOT AVAILABLE (title only)"))

def _note(e):
    msg = f"{type(e).__name__} {str(e)[:200]}" if not isinstance(e, (TimeoutError, RuntimeError)) else str(e)[:200]
    if not DIAG: DIAG.append(msg)
    return msg

def _finish(j, s, full):
    if s: s["title_only"] = not full; _guard_remote(j, s)
    j["score"] = s

def score_jobs(jobs, page=None):
    """Оцениваем пачками по BATCH: один запрос к модели на пачку — бесплатный лимит Gemini
       считается в запросах. Пачка не разобралась — эти вакансии по одной (как раньше)."""
    for j in jobs: j["score"] = None
    if not TOKEN:
        DIAG.append("нет GEMINI_API_KEY — добавь секрет в репо")
        print("scorer: нет GEMINI_API_KEY — шлю без оценок"); return jobs
    system = RUBRIC + "\n\n=== CANDIDATE ===\n" + _profile()
    if os.environ.get("SCORER_CONTEXT"):
        system += "\n\n=== CONTEXT FOR THIS BATCH ===\n" + os.environ["SCORER_CONTEXT"]
    t_end = time.time() + BUDGET
    todo = jobs[:MAX_PER_RUN]
    if len(jobs) > MAX_PER_RUN: print(f"scorer: потолок {MAX_PER_RUN} за запуск — остальные без оценки")
    for b in range(0, len(todo), BATCH):
        if time.time() > t_end:
            DIAG.append(f"вышло время на оценки ({BUDGET // 60} мин) — остальные без оценки")
            print("scorer: бюджет времени исчерпан"); break
        batch = todo[b:b + BATCH]
        texts = [describe(j, page) for j in batch]
        # 1) пачкой
        _DL[0] = min(t_end, time.time() + PER_BATCH)
        user = "\n\n".join(f"### {k+1}\n" + _user(j, t[:BATCH_JD]) for k, (j, (t, _)) in enumerate(zip(batch, texts)))
        try:
            got = parse_batch(_ask(system + "\n\n" + BATCH_NOTE, user, max_tokens=600 * len(batch) + 1000), len(batch))
        except Exception as e:
            print(f"scorer: пачка {b // BATCH + 1}: {_note(e)}"); got = [None] * len(batch)
            if isinstance(e, ScoreError) and (" 401" in str(e) or " 403" in str(e)): break   # ключ/доступ — дальше бессмысленно
            _M[0] = 0
        for j, s, (t, full) in zip(batch, got, texts): _finish(j, s, full)
        # 2) что не разобралось — по одной, пока есть время
        for j, (t, full) in zip(batch, texts):
            if j["score"] or time.time() > t_end: continue
            _DL[0] = min(t_end, time.time() + PER_JOB)
            try: _finish(j, parse(_ask(system, _user(j, t))), full)
            except Exception as e:
                print(f"scorer: {j.get('company')}: {_note(e)}"); _M[0] = 0
                break                                # модель не отвечает — следующую пачку попробуем снова
        print(f"scorer: пачка {b // BATCH + 1}: оценено {sum(1 for j in batch if j['score'])}/{len(batch)}", flush=True)
        time.sleep(4)                                # бесплатный тариф: запас по запросам в минуту
    if any(j["score"] is None for j in todo) and not DIAG: DIAG.append("модель вернула не-JSON")
    return jobs

# ---------- формат ----------
HIRE  = {"green": "🟢 ремоут", "yellow": "🟡 ремоут", "red": "🔴 ремоут"}
GRADE = {"down": "⬇️ грейд", "ok": "✅ грейд", "up": "⬆️ грейд"}
STOP  = {"crypto": "крипта", "betting": "беттинг", "gamedev": "геймдев"}

def fmt_job(j, suffix=""):
    l = j.get("location") or ""
    if len(l) > 80: l = l[:77].rsplit(",", 1)[0] + "…"     # любые длинные локации — не простынёй
    loc = f" — {l}" if l else ""
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
