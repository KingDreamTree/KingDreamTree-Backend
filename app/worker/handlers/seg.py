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

    # ⚠️ **새 맵을 먼저 올리고, 옛 파일 정리는 행 교체가 끝난 뒤에 한다.**
    #    옛 맵부터 지우면 그 직후 업로드·행 교체가 실패했을 때(워커 사망 포함)
    #    살아 있는 segmentation 행이 지워진 파일을 가리킨다 — 조회 API 는 200,
    #    signed URL 은 404 인 상태로 굳고 뷰어가 통째로 죽는다.
    #    맵 경로는 결정적이라 같은 자리면 업로드가 곧 덮어쓰기고, 실패 시 남는 건
    #    기껏해야 고아 파일이다(유저 삭제의 prefix 정리가 걷어간다).
    old = db.get_segmentation(UUID(str(photo_id)))

    map_path = storage.map_path(user_id, session_id, kind)
    storage.upload(settings.bucket_segmentations, map_path, result.map_png, "image/png")

    # ⚠️ 오버레이용 병합 맵을 **나란히** 올린다. palette 통계는 병합 후 기준인데
    #    원본 맵에는 옷이 옷으로 남아 있어서, 프론트가 원본을 라벨값으로 칠하면
    #    실측(2026-08-17) 허벅지의 19~21% 가 빠진 채 듬성듬성 칠해진다.
    #    VLM 오버레이(segmap.merge_map)는 이미 맞춰져 있었고 프론트만 어긋났다.
    #    ⚠️ 실패해도 세그 잡을 죽이지 않는다 — 원본 맵은 이미 올라갔고, 프론트는
    #       merged_map_url 이 없으면 map_url 로 폴백한다.
    if result.merged_map_png:
        try:
            storage.upload(
                settings.bucket_segmentations,
                storage.merged_map_path(user_id, session_id, kind),
                result.merged_map_png,
                "image/png",
            )
        except Exception:  # noqa: BLE001
            log.exception("병합 맵 업로드 실패 — 프론트는 원본 맵으로 폴백합니다")

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
                # ⚠️ 병합을 안 돌렸으면 0 이 아니라 NULL 이다.
                #    0 으로 쓰면 "옷을 안 입어서 흡수분이 없음"과
                #    "병합 자체를 안 함"이 구분되지 않는다. 나중에 이 데이터를
                #    다시 볼 때 두 경우를 가를 수 없게 된다.
                "clothing_pixel_count": (
                    p.clothing_pixel_count if settings.seg_merge_clothing else None
                ),
                "is_valid": p.is_valid,
                "invalid_reason": p.invalid_reason,
            }
            for p in result.parts
        ],
    )

    # 행 교체까지 끝난 뒤에야 옛 파생물 정리 — 이 시점엔 어느 단계가 실패해도
    # 상태가 이미 일관적이다. 정리 실패로 잡을 실패시키지 않는다(추론을 다시
    # 돌리게 되면 그게 더 비싸다).
    try:
        storage.delete_prefix(settings.bucket_body_parts, f"{user_id}/{session_id}/{kind.lower()}")
        if old and old["map_path"] != map_path:
            storage.remove(old["storage_bucket"], [old["map_path"]])
    except Exception:  # noqa: BLE001
        log.exception("옛 파생물 정리 실패 — 고아 파일이 남았을 수 있습니다")

    invalid_comparable = [
        {"class_name": p.class_name, "reason": p.invalid_reason}
        for p in result.parts
        if not p.is_valid and p.class_name in comparable
    ]

    valid_comparable = sum(1 for p in result.parts if p.is_valid and p.class_name in comparable)
    clothing = {
        p.class_name: p.clothing_pixel_count for p in result.parts if p.clothing_pixel_count
    }

    # 프론트가 세그 완료 즉시 "왼쪽 종아리는 노출이 부족합니다" 안내를 낼 수 있게 한다.
    #
    # ⚠️ 임계값 판단을 프론트에 흩뿌리지 않는다 — retake_recommended 를 서버가 내려준다.
    #    pose.py 의 "측정은 프론트, 정책은 서버" 규약과 같은 취지다.
    #
    # ⚠️ valid_comparable 은 **옷 병합 후** 기준이라, 옷을 입어도 채워진다.
    #    실제로 살이 얼마나 드러났는지는 valid_comparable_raw 로만 알 수 있다.
    #    둘의 차이가 곧 "옷이 얼마나 가렸는가"다.
    return {
        "segmentation_id": created["segmentation_id"],
        "photo_kind": kind,
        "detected": result.detected_class_count,
        "valid_comparable": valid_comparable,
        "valid_comparable_raw": result.valid_comparable_raw,
        "retake_recommended": valid_comparable < settings.min_comparable_parts,
        "min_comparable_parts": settings.min_comparable_parts,
        "clothing_absorbed": clothing,
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

    # ⚠️ body_part 마스터가 비어 있으면(= seed 를 안 돌렸으면) 모든 부위가
    #    NOT_COMPARABLE 로 찍혀 **비교 가능 부위가 늘 0** 이 된다. 잡은 성공으로
    #    끝나고 화면에는 "재촬영하세요"만 뜬다 — 사진을 아무리 다시 찍어도 그대로다.
    #    잡을 받기 전에 여기서 막는다.
    comparable = db.comparable_class_names()
    if not comparable:
        raise RuntimeError(
            "body_part 마스터가 비어 있습니다 (is_comparable=true 인 행이 없음).\n"
            "  python scripts/seed_body_parts.py 를 먼저 돌리세요."
        )
    log.info("비교 대상 부위 %d개: %s", len(comparable), ", ".join(comparable))


register_preflight(_preflight)
register(JobKind.SEG_REFERENCE, _handle)
register(JobKind.SEG_USER, _handle)
