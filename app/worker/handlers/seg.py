"""SEG_REFERENCE / SEG_USER 핸들러.

⚠️ 이 핸들러만 torch/transformers를 끌어온다. GPU 있는 곳에서 돌린다 (RunPod).
   LLM 워커(EC2)는 이 모듈을 import 하지 않는다.

흐름
    job.payload.photo_id
      → photo / analysis_session 조회
      → Storage에서 원본 다운로드
      → Sapiens2 추론 → 라벨 맵 + 부위별 통계
      → 맵 PNG를 segmentations 버킷에 업로드
      → segmentation + body_part_segment 행 교체
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.config import settings
from app.schemas.enums import JobKind
from app.services import db, sapiens_labels, segmenter, storage
from app.worker.registry import register, register_preflight

log = logging.getLogger("worker.seg")


def _handle(job: dict[str, Any]) -> dict[str, Any]:
    payload = job.get("payload") or {}
    photo_id = payload.get("photo_id")
    if not photo_id:
        raise ValueError("job.payload.photo_id 가 없습니다.")

    photo = db.get_photo_by_id(UUID(str(photo_id)))
    if photo is None:
        raise ValueError("대상 사진을 찾을 수 없습니다. 이미 교체되었을 수 있습니다.")

    session = db.get_session(UUID(str(photo["session_id"])))
    if session is None:
        raise ValueError("세션을 찾을 수 없습니다.")

    user_id = UUID(str(session["user_id"]))
    session_id = UUID(str(session["session_id"]))
    kind = str(photo["kind"])

    # 마스터를 DB에서 읽는다 — 워커에 SKIN_CLASSES 상수를 두지 않는다.
    comparable = set(db.comparable_class_names())
    master = db.master_class_names()

    log.info("사진 다운로드: %s/%s", photo["storage_bucket"], photo["storage_path"])
    image_bytes = storage.download(photo["storage_bucket"], photo["storage_path"])

    log.info("추론 시작 (%s)", segmenter.describe_environment())
    result = segmenter.segment(image_bytes, comparable=comparable, master_class_names=master)
    log.info(
        "추론 완료 %dms — 맵 %dx%d, 검출 %d클래스, 인물 비율 %.1f%%",
        result.inference_ms,
        result.map_width,
        result.map_height,
        result.detected_class_count,
        result.person_area_ratio * 100,
    )

    # ⚠️ 기존 파일을 먼저 지운다. 행을 먼저 지우면 어느 파일을 지울지 알 수 없게 된다.
    old = db.get_segmentation(UUID(str(photo_id)))
    if old:
        storage.delete_prefix(settings.bucket_body_parts, f"{user_id}/{session_id}/{kind.lower()}")
        storage.remove(old["storage_bucket"], [old["map_path"]])

    map_path = storage.map_path(user_id, session_id, kind)
    storage.upload(settings.bucket_segmentations, map_path, result.map_png, "image/png")

    created = db.replace_segmentation(
        UUID(str(photo_id)),
        segmentation={
            "storage_bucket": settings.bucket_segmentations,
            "map_path": map_path,
            "map_width": result.map_width,
            "map_height": result.map_height,
            "label_map": result.label_map,
            "model_name": result.model_name,
            "model_version": result.model_version,
            "person_pixel_count": result.person_pixel_count,
            "person_area_ratio": result.person_area_ratio,
            "detected_class_count": result.detected_class_count,
            "inference_ms": result.inference_ms,
        },
        parts=[
            {
                "class_name": p.class_name,
                "label_value": p.label_value,
                "pixel_count": p.pixel_count,
                "area_ratio": p.area_ratio,
                "bbox_x": p.bbox_x,
                "bbox_y": p.bbox_y,
                "bbox_w": p.bbox_w,
                "bbox_h": p.bbox_h,
                "is_truncated": p.is_truncated,
                "is_valid": p.is_valid,
                "invalid_reason": p.invalid_reason,
            }
            for p in result.parts
        ],
    )

    valid = [p for p in result.parts if p.is_valid]
    invalid_comparable = [
        {"class_name": p.class_name, "reason": p.invalid_reason}
        for p in result.parts
        if not p.is_valid and p.class_name in comparable
    ]

    # 프론트가 세그 완료 즉시 "왼쪽 종아리는 노출이 부족합니다" 안내를 낼 수 있게 한다.
    return {
        "segmentation_id": created["segmentation_id"],
        "photo_kind": kind,
        "detected": result.detected_class_count,
        "valid_comparable": len(valid),
        "invalid": invalid_comparable,
        "inference_ms": result.inference_ms,
        "model_version": result.model_version,
    }


def _preflight() -> None:
    """기동 시 점검 — 가중치가 실제로 있는지, 라벨 목록이 이 모델에 맞는지.

    ⚠️ 둘 다 "없으면 어차피 모든 잡이 실패할" 조건이다. 잡을 재시도 한도까지
       말아먹은 뒤에 알게 되면 사용자는 그동안 로딩 화면을 본다.

    ⚠️ MODEL_DIR 을 셸 export 로만 두면 터미널을 새로 열 때마다 날아간다.
       .env 에 넣어두는 게 안전하다 — 여기 메시지에 그 경로를 찍어 확인할 수 있게 한다.
    """
    import os

    path = segmenter.model_path()
    log.info("환경: %s", segmenter.describe_environment())
    log.info("가중치 경로: %s", path)

    if not os.path.isdir(path):
        raise RuntimeError(
            f"가중치 폴더가 없습니다: {path}\n"
            f"  MODEL_DIR 이 맞는지 확인하세요 (지금 값: {settings.model_dir}).\n"
            "  셸 export 는 터미널을 새로 열면 날아갑니다. .env 에 넣어두세요:\n"
            "    echo 'MODEL_DIR=/workspace/models' >> .env"
        )

    # 이 라벨 목록이 적용되는 모델인지 — 아니면 부위가 통째로 어긋난 채 저장된다
    sapiens_labels.ensure_supported(segmenter.model_version())


register_preflight(_preflight)
register(JobKind.SEG_REFERENCE, _handle)
register(JobKind.SEG_USER, _handle)
