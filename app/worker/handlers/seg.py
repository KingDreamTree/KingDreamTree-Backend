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
from app.services import db, segmenter, storage
from app.worker.run import register

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


register(JobKind.SEG_REFERENCE, _handle)
register(JobKind.SEG_USER, _handle)
