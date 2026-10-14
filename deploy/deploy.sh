#!/usr/bin/env bash
# Выкатка коммита из main: вызывается GitHub Actions по SSH (job deploy в tests.yml).
# Ключ деплоя может только запустить этот скрипт; аргумент — полный SHA коммита,
# прошедшего CI, приходит в SSH_ORIGINAL_COMMAND.
#
# Установка на сервере (пользователь бота, vbondarev):
#   1. Ключ без пароля: ssh-keygen -t ed25519 -N '' -C flight-bot-deploy -f ~/flight-bot-deploy
#      В ~/.ssh/authorized_keys одной строкой:
#        command="/opt/flight-bot/deploy/deploy.sh",restrict ssh-ed25519 AAAA… flight-bot-deploy
#      Закрытый ключ — в GitHub Secrets (DEPLOY_SSH_KEY) и удалить с сервера.
#   2. sudo только на рестарт (путь — из `command -v systemctl`):
#        echo 'vbondarev ALL=(root) NOPASSWD: /usr/bin/systemctl restart flight-bot' \
#          | sudo tee /etc/sudoers.d/flight-bot-deploy && sudo chmod 440 /etc/sudoers.d/flight-bot-deploy
#        sudo visudo -cf /etc/sudoers.d/flight-bot-deploy
#
# Smoke: сервис active и PID не меняется SMOKE_SEC секунд (Restart=on-failure через
# RestartSec=10 сменил бы PID). Не прошёл — откат на прежний коммит и рестарт.
# Вывод уходит в публичный лог Actions: только SHA и состояния, журнал бота не печатаем.
set -euo pipefail

main() {
  local dir="${FLIGHT_BOT_DIR:-/opt/flight-bot}" sha="${SSH_ORIGINAL_COMMAND:-}"
  local smoke="${SMOKE_SEC:-30}" prev
  if [[ ! $sha =~ ^[0-9a-f]{40}$ ]]; then
    echo "нужен полный SHA коммита" >&2
    return 2
  fi
  exec 9>"${DEPLOY_LOCK:-/tmp/flight-bot-deploy.lock}"
  flock -w 300 9
  cd "$dir"

  git fetch -q origin main
  if ! git merge-base --is-ancestor "$sha" origin/main 2>/dev/null; then
    echo "коммит $sha не из main" >&2
    return 1
  fi
  if [[ -n $(git status --porcelain --untracked-files=no) ]]; then
    echo "на сервере правки в отслеживаемых файлах — выкатка остановлена" >&2
    return 1
  fi
  prev=$(git rev-parse HEAD)
  if git merge-base --is-ancestor "$sha" "$prev"; then
    echo "уже выкачено: $prev"
    return 0
  fi

  echo "выкатка $prev → $sha"
  git merge -q --ff-only "$sha"
  apply "$prev" "$sha"
  if smoke_ok "$smoke"; then
    echo "ok: $sha"
    return 0
  fi
  echo "сервис не держится — откат на $prev" >&2
  git reset -q --hard "$prev"
  apply "$sha" "$prev"
  smoke_ok "$smoke" || echo "после отката сервис тоже не держится — нужен человек" >&2
  return 1
}

# Рабочая копия уже на $2: зависимости, если requirements.txt менялся с $1, и рестарт.
apply() {
  if ! git diff --quiet "$1" "$2" -- requirements.txt; then
    .venv/bin/pip install -q -r requirements.txt
  fi
  sudo -n systemctl restart flight-bot
}

smoke_ok() {
  local pid i
  sleep 2
  pid=$(systemctl show -p MainPID --value flight-bot)
  [[ $pid != 0 ]] || return 1
  for ((i = 0; i < $1; i++)); do
    sleep 1
    systemctl is-active -q flight-bot || return 1
    [[ $(systemctl show -p MainPID --value flight-bot) == "$pid" ]] || return 1
  done
}

main "$@"; exit $?
