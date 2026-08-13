"""OCR_INBODY 핸들러 — GPU 불필요, EC2 LLM 워커에서 돈다.

흐름
    job.payload.{inbody_id, bucket, paths}
      → inbody 행 확인 (삭제됐으면 중단)
      → 임시 이미지 다운로드 (여러 페이지 = GPT 한 요청)
      → 추출(VLM) → 검증(결정론적 규칙) → DB CHECK 가드
      → inbody 컬럼 + raw_ocr + validation 갱신, segments 교체 → status DONE
      → **DONE 직후** 임시 이미지 삭제. 실패하면 재처리 위해 남긴다.

⚠️ 검증 WARN 은 실패가 아니다. status 는 DONE 으로 두고 validation 에만 남긴다 —
   "OCR이 이상한 값을 뽑았다"는 사실 자체를 기록해야 정확도를 평가할 수 있다.
⚠️ SMI 는 여기서 계산하지도, 저장하지도 않는다. 컬럼이 없고(스키마 §7),
   조회 시점에 백엔드가 calc_smi()로 파생한다. VLM에게 계산을 시키지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from app.schemas.enums import DomainStatus, JobKind
from app.services import inbody_repo, ocr, storage
from app.worker.run import register

log = logging.getLogger("worker.ocr")


def _handle(job: dict[str, Any]) -> dict[str, Any]:
    payload = job.get("payload") or {}
    inbody_id_raw, bucket, paths = (
        payload.get("inbody_id"),
        payload.get("bucket"),
        payload.get("paths") or [],
    )
    if not inbody_id_raw or not bucket or not paths:
        raise ValueError("job.payload 에 inbody_id / bucket / paths 가 없습니다.")

    inbody_id = UUID(str(inbody_id_raw))
    row = inbody_repo.get_inbody(inbody_id)
    if row is None:
        # 사용자가 OCR 완료 전에 DELETE 한 경우 — 임시 파일만 마저 지우고 끝낸다.
        storage.remove(bucket, paths)
        return {"inbody_id": str(inbody_id), "skipped": "이미 삭제된 인바디"}

    log.info("결과지 다운로드: %s (%d페이지)", bucket, len(paths))
    pages = [storage.download(bucket, p) for p in paths]

    try:
        raw = asyncio.run(ocr.extract_inbody(pages))

        validation = ocr.validate_inbody(raw)
        columns, db_warns = ocr.sanitize_columns(ocr.to_columns(raw))
        validation["checks"].extend(db_warns)
        validation["ok"] = validation["ok"] and not db_warns

        inbody_repo.update_inbody(
            inbody_id,
            {
                **columns,
                "raw_ocr": raw,  # ⚠️ 여기가 raw_ocr 을 쓰는 유일한 곳
                "validation": validation,
                "status": DomainStatus.DONE,
                "validation_error": None,
            },
        )
        inbody_repo.replace_segments(inbody_id, ocr.to_segment_rows(raw))
    except Exception as e:  # noqa: BLE001
        # 도메인 상태도 FAILED 로 — job 만 FAILED 면 프론트 확인 화면이 PENDING 에 갇힌다.
        # 임시 이미지는 남긴다 (재처리·수동 입력 대비).
        inbody_repo.update_inbody(
            inbody_id,
            {"status": DomainStatus.FAILED, "validation_error": str(e)[:500]},
        )
        raise

    # DONE 직후 삭제 — 개인정보가 담긴 결과지를 필요 이상 보관하지 않는다.
    storage.remove(bucket, paths)
    log.info("임시 이미지 %d건 삭제", len(paths))

    warn_count = sum(1 for c in validation["checks"] if c["level"] == "WARN")
    return {
        "inbody_id": str(inbody_id),
        "validation_ok": validation["ok"],
        "warn_count": warn_count,
        "pages": len(paths),
    }


register(JobKind.OCR_INBODY, _handle)
