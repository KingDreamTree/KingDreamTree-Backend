"""프롬프트 저장소 — 진단 시스템 프롬프트를 DB(prompt_version)의 **활성 버전**에서 읽는다 (#164).

━━ 왜 DB 인가 ━━

프롬프트는 이 서비스에서 가장 자주 바뀌는 것인데(예문 자석 사고만 세 번), 문구 한 줄을 고치려면
코드 변경 + 배포를 탔고, 어느 진단 결과가 어느 문구로 만들어졌는지는 커밋 시각으로 짐작했다. 이제:

    · 새 버전   = 마이그레이션 SQL 한 장 (scripts/prompt_version.py new). 앱 배포 없음
    · 되돌리기  = 이전 버전 활성화 한 장 (scripts/prompt_version.py activate)
    · 추적      = 결과 행에 어느 버전으로 만들었는지 남는다
                  (part_diagnosis · overall_diagnosis 의 prompt_version, compose() 의 태그)

━━ 못 읽으면 (2026-09-11 결정) ━━

    · 한 번 읽은 값은 캐시해 두고 계속 쓴다 — DB 가 잠깐 안 돼도 진단은 나간다
    · 처음부터 못 읽으면 PromptUnavailableError → 잡 실패 → 워커 재시도.
      VLM 을 부르기 전에 실패하므로 재시도에 비용이 없다
    ⚠️ 코드에 기본 문구를 두지 않는다. 두 벌이면 한쪽만 고쳐진다 — 규칙이 두 파일에 있어서
       3주 동안 한쪽만 옛 지시를 내보낸 사고가 있었다 (app/prompts/part_rules.py 모듈 주석).

⚠️ DB 는 지연 import 한다 — 이 모듈을 DB 설정 없이 시험할 수 있게 (scripts/verify_prompt_store.py).
"""

import logging
import time

log = logging.getLogger(__name__)

#: 활성 버전을 다시 확인하는 주기. 새 버전을 적용하면 워커마다 최대 이만큼 뒤에 따라온다.
#  ponytail: 프로세스 메모리 캐시라 워커마다 따로 센다. 즉시 반영이 필요해지면 활성화 시각을 보고
#  무효화하는 쪽으로 바꾼다.
_TTL_SEC = 60.0

#: name → (본문, 버전, 읽은 시각)
_cache: dict[str, tuple[str, int, float]] = {}


class PromptUnavailableError(RuntimeError):
    """활성 프롬프트를 읽지 못했고 캐시도 없다. 워커는 이 잡을 재시도한다 (retryable 기본값)."""


def _now() -> float:
    return time.monotonic()


def _fetch(name: str) -> tuple[str, int] | None:
    """활성 버전 하나. 없으면 None."""
    from app.services.db import get_client

    rows = (
        get_client()
        .table("prompt_version")
        .select("content,version")
        .eq("name", name)
        .eq("is_active", True)
        .limit(1)
        .execute()
        .data
    )
    return (rows[0]["content"], int(rows[0]["version"])) if rows else None


def get(name: str) -> tuple[str, int]:
    """(본문, 버전). 캐시가 신선하면 DB 를 보지 않는다."""
    hit = _cache.get(name)
    if hit and _now() - hit[2] < _TTL_SEC:
        return hit[0], hit[1]
    try:
        row = _fetch(name)
    except Exception as e:
        if hit:
            # ⚠️ 예외 원문은 남기지 않는다 — 접속 문자열·키가 섞여 나온 전례가 있다 (worker/run.py)
            log.warning(
                "프롬프트 %s 를 DB 에서 못 읽어 캐시(v%s)를 씁니다: %s",
                name,
                hit[1],
                type(e).__name__,
            )
            return hit[0], hit[1]
        raise PromptUnavailableError(f"프롬프트를 불러오지 못했습니다: {name}") from e
    if row is None:
        if hit:
            log.error("프롬프트 %s 의 활성 버전이 없어 캐시(v%s)를 씁니다", name, hit[1])
            return hit[0], hit[1]
        raise PromptUnavailableError(f"활성 프롬프트가 없습니다: {name}")
    _cache[name] = (row[0], row[1], _now())
    return row


def compose(*names: str) -> tuple[str, str]:
    """조각들을 빈 줄로 이어 붙인 시스템 프롬프트와 버전 태그 ("part.rules@2 part.output@1").

    태그는 결과 행의 prompt_version 에 저장한다 — 어느 결과가 어느 문구로 나왔는지의 유일한 기록이다.
    """
    got = [(n, *get(n)) for n in names]
    return "\n\n".join(c for _, c, _ in got), " ".join(f"{n}@{v}" for n, _, v in got)
