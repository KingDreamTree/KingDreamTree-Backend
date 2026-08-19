"""아주 단순한 IP 기준 레이트리밋 (#115).

⚠️ 프로세스 메모리에 든다 — 워커 프로세스와 무관한 별개 상태고, API 프로세스가
   여러 대로 늘어나면 인스턴스마다 따로 센다. 지금은 API가 단일 프로세스라
   문제없다. 여러 대로 늘어나면 Redis 등 공유 저장소로 옮길 것.

⚠️ 새 의존성(slowapi 등)을 안 쓴다. 막을 대상이 "인증 없는 POST /users
   뒤에 붙은 동기 Vision 호출을 스크립트로 반복 호출"이라는 단일 엔드포인트
   시나리오라, dict 하나로 충분하다.
"""

from __future__ import annotations

import time
from collections import defaultdict

from app.errors import too_many_requests

#: {key: [호출 시각, ...]} — 윈도우 밖 기록은 조회 때마다 정리한다.
_hits: dict[str, list[float]] = defaultdict(list)


def check(key: str, limit: int, window_sec: int) -> None:
    """key가 window_sec 동안 limit번을 넘겨 불렀으면 429를 던진다.

    ⚠️ 이벤트 루프 단일 스레드 안에서 await 없이 끝나는 함수라 원자적이다 —
       별도 락이 필요 없다.
    """
    now = time.monotonic()
    hits = [t for t in _hits[key] if now - t < window_sec]
    if len(hits) >= limit:
        retry_after = window_sec - (now - hits[0])
        raise too_many_requests(max(1, round(retry_after)))
    hits.append(now)
    _hits[key] = hits
