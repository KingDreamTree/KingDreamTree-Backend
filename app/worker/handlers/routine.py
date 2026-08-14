"""ROUTINE_GEN / ROUTINE_PATCH 핸들러 — GPU 불필요, EC2 LLM 워커에서 돈다.

⚠️ 지금은 **기동 점검(preflight)만** 등록한다. 잡 핸들러 본체는 루틴 스키마
   마이그레이션(db/migrations/2026-08-14_routine_cycle_model.sql)이 콘솔에
   반영된 뒤에 붙인다 — 저장할 테이블이 아직 없다.

기동 점검을 먼저 넣는 이유 (담당 A 요청, body_part 에서 겪은 사고):
    마스터 테이블이 비었는데 워커가 그냥 뜨면, 잡은 "성공"으로 끝나면서
    결과만 비어 있다. body_part 가 비었을 때 비교 가능 부위가 늘 0 이 나왔고,
    화면엔 "재촬영하세요"만 떠서 사용자가 사진을 아무리 다시 찍어도 안 고쳐졌다.
    카탈로그가 비면 LLM 이 고를 후보가 없어 같은 식으로 조용히 이상한 루틴이 나온다.
    → 그런 상태면 **워커를 아예 띄우지 않는다.**
"""

from __future__ import annotations

import logging

from app.schemas.enums import JobKind
from app.services.db import get_client
from app.worker.registry import HANDLERS, register_preflight

log = logging.getLogger("worker.routine")

#: 이 점검이 적용되는 잡 종류. 이 중 하나라도 실제로 처리하는 워커일 때만 본다.
_ROUTINE_KINDS = (JobKind.ROUTINE_GEN, JobKind.ROUTINE_PATCH)

#: 이보다 적으면 슬롯별 후보가 모자라 루틴 품질을 보장할 수 없다.
#: 실측 200건 기준 슬롯당 12~42개가 나오므로, 절반 아래로 떨어지면 이상 상황이다.
MIN_CATALOG_ROWS = 100


def _check_exercise_catalog() -> None:
    """운동 카탈로그가 채워져 있는지. 비었으면 기동을 거부한다.

    ⚠️ **루틴 잡을 실제로 처리하는 워커에서만 본다.** run.py 의 `_load_handlers` 는
       LLM 계열 워커면 이 모듈을 무조건 import 하므로, 조건 없이 검사하면
       VLM 전용 워커(RunPod 분리 배치)까지 카탈로그 때문에 못 뜬다.
       핸들러가 등록되기 전에는 조용히 건너뛴다.
    """
    if not any(k in HANDLERS for k in _ROUTINE_KINDS):
        return

    try:
        res = (
            get_client()
            .table("exercise_catalog")
            .select("exercise_ref", count="exact")
            .eq("is_beginner_safe", True)
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001 — 테이블이 아직 없을 수도 있다
        raise RuntimeError(
            "exercise_catalog 를 읽을 수 없습니다. 마이그레이션이 반영됐는지 확인하세요 "
            "(db/migrations/2026-08-14_exercise_catalog.sql). 원인: " + type(e).__name__
        ) from e

    count = res.count or 0
    if count < MIN_CATALOG_ROWS:
        raise RuntimeError(
            f"운동 카탈로그가 비어 있거나 부족합니다 (is_beginner_safe=true {count}행, "
            f"최소 {MIN_CATALOG_ROWS}행 필요). "
            "python scripts/seed_exercise_catalog.py 를 먼저 실행하세요. "
            "⚠️ 이 상태로 두면 LLM 이 고를 후보가 없어 빈 루틴이 조용히 생성됩니다."
        )

    log.info("운동 카탈로그 확인 — 초보 가용 %d행", count)


register_preflight(_check_exercise_catalog)
