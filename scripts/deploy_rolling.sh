#!/usr/bin/env bash
# 무중단 배포 — 이미지 한 번 빌드 → api_a → api_b → worker 순서로 하나씩 교체.
#
#   ~/KingDreamTree-Backend$ scripts/deploy_rolling.sh
#
# 왜 하나씩인가
#   `docker compose up -d --build` 는 api 를 통째로 재시작해 몇 초 끊긴다.
#   Caddyfile 이 api_a·api_b 둘을 보고 능동 헬스체크로 살아 있는 쪽에만 보내므로,
#   한쪽이 healthy 로 돌아온 뒤에 다른 쪽을 갈아끼우면 요청이 끊기지 않는다.
#   (`--wait` 가 compose healthcheck(/health 200)를 기다린다)
#
# worker 는 하나뿐이라 교체 중 최대 stop_grace_period(120s) 동안 잡을 안 집는다 —
# 사용자는 로딩이 조금 길어질 뿐 실패하지 않는다 (잡은 큐에서 기다린다).
#
# ⚠️ deploy.yml(GitHub Actions) 이 이 스크립트를 부른다. 손으로 돌려도 같다.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== 코드: $(git log --oneline -1)"
echo "== 이미지 빌드"
docker compose build api_a

wait_healthy() {
  # --wait 는 compose 2.20+ 에 있다. 없으면 healthcheck 를 직접 기다린다.
  local svc="$1"
  for _ in $(seq 1 40); do
    status=$(docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q "$svc")" 2>/dev/null || echo "none")
    [ "$status" = "healthy" ] && return 0
    sleep 2
  done
  echo "!! $svc 가 80초 안에 healthy 가 되지 않았습니다"; docker compose logs --tail=30 "$svc"; return 1
}

for svc in api_a api_b; do
  echo "== $svc 교체"
  docker compose up -d --no-deps "$svc"
  wait_healthy "$svc"
  # Caddy 능동 헬스체크(3초) 가 이 컨테이너를 다시 목록에 넣을 시간
  sleep 4
done

# Caddyfile 이 바뀌었을 수 있다 (예: api → api_a·api_b 전환). 컨테이너는 기동 때 읽은 설정을
# 물고 있으므로 무중단으로 다시 읽힌다. 안 바뀌었으면 그냥 통과.
echo "== caddy 설정 재로드"
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile 2>&1 | tail -1 || echo "!! caddy reload 실패 — 옛 설정으로 계속 동작 중"

echo "== worker 교체"
docker compose up -d --no-deps worker

echo "== 고아 컨테이너·옛 이미지 정리"
docker compose up -d --remove-orphans >/dev/null
docker image prune -f >/dev/null

echo "== 결과"
docker compose ps --format "table {{.Name}}\t{{.Status}}"
