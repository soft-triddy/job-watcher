#!/usr/bin/env bash
# Коммит и пуш состояния радара из GitHub Actions.
#   bash save_state.sh "сообщение коммита" путь [путь...]
# Пуш повторяется до 4 раз: 07.10 браузерный радар отработал 40 минут, а шаг сохранения упал
# с первой же попытки — seen не записался, и вакансии того дня пришли бы повторно.
# При конфликте в state/ побеждает версия этого запуска (-X theirs при rebase = наш коммит).
set -u
msg="$1"; shift
git config user.name "radar-bot"
git config user.email "radar-bot@users.noreply.github.com"
git add "$@" 2>/dev/null || true
if git diff --cached --quiet; then echo "no changes to save"; exit 0; fi
git commit -q -m "$msg"
for i in 1 2 3 4; do
  if git pull -q --rebase --autostash -X theirs origin main && git push -q; then
    echo "saved (попытка $i)"; exit 0
  fi
  git rebase --abort 2>/dev/null || true
  echo "!! сохранение: попытка $i не удалась, жду $((i * 15))с"; sleep $((i * 15))
done
echo "!! состояние не сохранено после 4 попыток"; exit 1
