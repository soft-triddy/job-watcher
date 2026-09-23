# -*- coding: utf-8 -*-
"""Проверка оценщика: одна образцовая вакансия -> Telegram. Запускается при изменении scorer.py."""
import os
from radar import send
import scorer

job = {"company": "Test Co", "title": "Growth Marketing Manager (B2B SaaS)", "location": "Remote, worldwide",
       "url": "https://example.com/job",
       "description": ("We are a B2B SaaS company hiring a hands-on Growth Marketing Manager, fully remote, "
                       "contractors worldwide welcome. You will own inbound acquisition: Google Ads search, SEO, "
                       "landing pages, CRO experiments and HubSpot automation (lifecycle stages, lead routing). "
                       "Requirements: 5+ years in B2B SaaS growth, HubSpot, GA4/GTM, strong English.")}
scorer.score_jobs([job])
print("token:", "есть" if scorer.TOKEN else "НЕТ", "| model:", scorer.MODEL, "| score:", job["score"])
send("🧪 Тест оценщика\n\n" + scorer.fmt_job(job) + scorer.diag_line())
