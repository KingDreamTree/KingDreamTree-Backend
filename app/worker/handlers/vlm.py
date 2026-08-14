"""VLM_PART / VLM_OVERALL 핸들러 — GPU 불필요, EC2 LLM 워커에서 돈다.

━━ VLM_PART 는 잡 1개가 전 부위를 처리한다 ━━

    job(VLM_PART) 1개
      → 원본·맵 다운로드 → 오버레이 2장 생성 → 수치 계산
      → Vision 1회 호출 → 부위 수만큼 part_diagnosis 행
      → VLM_OVERALL 잡 등록

부위마다 잡을 만들지 않는 이유는 app/prompts/part_diagnosis.py 모듈 주석 참고.
"부분 실패 허용"은 그대로 지켜진다 — 호출이 아니라 **응답 파싱** 단위로
부위별 채택/실패가 갈린다 (services/vlm.py 모듈 주석).

━━ VLM_OVERALL 은 VLM_PART 가 끝난 뒤에 등록된다 ━━

종합 진단의 입력은 부위별 진단 결과다. 그래서 POST 시점이 아니라 **VLM_PART
핸들러가 성공한 직후** 등록한다. 잡 큐에 의존성 개념이 없어도 순서가 인과적으로
보장되고, "선행 잡이 끝났나" 를 폴링하며 재시도 횟수를 태우는 대기 로직이
아예 필요 없어진다.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any
from uuid import UUID

from PIL import Image

from app.config import settings
from app.schemas.enums import DomainStatus, JobKind, PhotoKind, ScoreSource, VlmInputType
from app.services import db, diagnosis_repo, inbody_repo, scoring, segmap, storage, vlm
from app.worker import queue
from app.worker.registry import register

log = logging.getLogger("worker.vlm")


class InputNotUsableError(RuntimeError):
    """입력 자체가 진단에 못 쓸 상태 — 다시 시도해도 같다.

    비교 대상 부위가 모자란 경우가 이에 해당한다. 사진이 바뀌지 않는 한
    재시도가 결과를 바꾸지 못하므로 즉시 종결시켜 사용자가 재촬영으로
    넘어가게 한다 (worker/run.py `_is_retryable`).
    """

    retryable = False


# --------------------------------------------------------------------------- #
# 입력 준비
# --------------------------------------------------------------------------- #


def _load_side(context: dict[str, Any], kind: PhotoKind) -> tuple[bytes, bytes, list[str]]:
    """한쪽(레퍼런스 또는 사용자)의 원본 JPEG·오버레이 JPEG·칠해진 부위를 만든다."""
    photo = context["photos"][str(kind)]
    seg = context["segmentations"][str(kind)]
    parts = context["parts"]

    photo_bytes = storage.download(photo["storage_bucket"], photo["storage_path"])
    map_bytes = storage.download(seg["storage_bucket"], seg["map_path"])

    image = segmap.fit_for_vlm(Image.open(io.BytesIO(photo_bytes)).convert("RGB"))

    # ⚠️ 오버레이는 **병합된 맵**으로 칠한다 (segmap.merge_map docstring 참고).
    #    A 가 저장하는 통계·is_valid 가 병합 후 픽셀 기준이므로, 그림도 같은
    #    병합을 태워야 VLM 이 받는 그림과 수치가 같은 몸을 말한다.
    seg_map = segmap.merge_map(
        segmap.load_map(map_bytes),
        seg["label_map"],
        {p["class_name"] for p in parts},
    )

    overlay, painted = segmap.build_overlay(
        photo=image,
        seg_map=seg_map,
        parts=[(p["class_name"], p["color_hex"] or "#888888") for p in parts],
        label_map=seg["label_map"],
    )
    return segmap.encode_jpeg(image), segmap.encode_jpeg(overlay), painted


def _inbody_for(session_id: UUID) -> tuple[dict[str, Any] | None, str]:
    """진단에 쓸 인바디 1건과 그 사유.

    ⚠️ 인바디는 선행 조건이 아니다. OCR 이 아직 안 끝났어도 **기다리지 않는다** —
       사용자를 로딩 화면에 무한정 세우지 않기 위해서다. 대신 왜 없이 진행했는지를
       job.result 에 남겨 나중에 "인바디를 올렸는데 왜 반영이 안 됐지"에 답한다.
    """
    row = inbody_repo.latest_done(session_id)
    if row is not None:
        return inbody_repo.to_prompt_payload(row), "USED"

    # ⚠️ queue.all_settled() 를 쓰다가 그 함수가 제거되면서 **인바디 없는 사용자의
    #    진단이 통째로 죽었다** (AttributeError). 인바디가 있으면 위에서 반환해
    #    이 줄을 안 타므로, 조각 테스트로는 안 잡히고 전 구간 스모크에서 드러났다.
    #    find_open 은 좀비 잡을 걸러주므로 오히려 이쪽이 정확하다.
    if queue.find_open(session_id, JobKind.OCR_INBODY) is not None:
        return None, "SKIPPED_OCR_IN_PROGRESS"
    return None, "NONE"


# --------------------------------------------------------------------------- #
# VLM_PART
# --------------------------------------------------------------------------- #


def _diagnose_parts(job: dict[str, Any]) -> dict[str, Any]:
    session_id = UUID(str(job["session_id"]))
    context = diagnosis_repo.build_comparison_context(session_id)

    if not context["ready"]:
        raise ValueError("세그멘테이션이 아직 완료되지 않았습니다.")

    parts = context["parts"]
    if len(parts) < settings.min_comparable_parts:
        raise InputNotUsableError(
            f"비교 가능한 부위가 {len(parts)}개뿐입니다 "
            f"(최소 {settings.min_comparable_parts}개). 다시 촬영해주세요."
        )

    class_names = [p["class_name"] for p in parts]
    log.info("비교 대상 %d부위: %s", len(class_names), ", ".join(class_names))

    ref_photo, ref_overlay, ref_painted = _load_side(context, PhotoKind.REFERENCE)
    user_photo, user_overlay, user_painted = _load_side(context, PhotoKind.USER)

    # 오버레이에 실제로 칠해진 부위만 진단 대상으로 삼는다. 범례와 그림이
    # 어긋나면 VLM 이 없는 색을 찾다가 엉뚱한 부위를 지목한다.
    painted = [n for n in class_names if n in set(ref_painted) & set(user_painted)]
    if len(painted) < len(class_names):
        log.warning("맵에 없어 제외된 부위: %s", set(class_names) - set(painted))
    parts = [p for p in parts if p["class_name"] in painted]
    if len(parts) < settings.min_comparable_parts:
        raise InputNotUsableError("맵에서 비교 대상 부위를 충분히 찾지 못했습니다.")

    ref_segments = context["segments"][str(PhotoKind.REFERENCE)]
    user_segments = context["segments"][str(PhotoKind.USER)]
    person_classes = context["person_classes"]
    names = [p["class_name"] for p in parts]

    metrics = segmap.compare_parts(ref_segments, user_segments, names, person_classes)
    inbody, inbody_source = _inbody_for(session_id)

    result = asyncio.run(
        vlm.diagnose_parts(
            reference_photo=ref_photo,
            reference_overlay=ref_overlay,
            user_photo=user_photo,
            user_overlay=user_overlay,
            parts=parts,
            metrics=metrics,
            reference_symmetry=segmap.symmetry(ref_segments, names, person_classes),
            user_symmetry=segmap.symmetry(user_segments, names, person_classes),
            inbody=inbody,
        )
    )

    rows = _to_diagnosis_rows(result, parts, ref_segments, user_segments)
    diagnosis_repo.replace_part_diagnoses(session_id, rows)

    # 부위 진단이 저장된 뒤에만 종합을 등록한다 (모듈 주석 참고).
    overall_job, created = queue.enqueue_once(session_id, JobKind.VLM_OVERALL)
    log.info("종합 진단 잡 %s (신규=%s)", overall_job["job_id"], created)

    return {
        "part_count": len(rows),
        "done": sum(1 for r in rows if r["status"] == DomainStatus.DONE),
        "failed": result["missing"],
        "inbody": inbody_source,
        "overall_job_id": str(overall_job["job_id"]),
    }


def _call_meta(raw: dict[str, Any]) -> dict[str, Any]:
    """호출 식별 정보만 뽑는다. ⚠️ 프롬프트 전문·API 키는 절대 넣지 않는다."""
    return {
        "id": raw.get("id"),
        "model": raw.get("model"),
        "usage": raw.get("usage"),
        "created": raw.get("created"),
    }


def _to_diagnosis_rows(
    result: dict[str, Any],
    parts: list[dict[str, Any]],
    ref_segments: dict[str, Any],
    user_segments: dict[str, Any],
) -> list[dict[str, Any]]:
    """검증 결과 → part_diagnosis 행. 못 쓴 부위는 FAILED 행으로 남긴다.

    ⚠️ raw_response 에는 **그 부위의 응답 항목 + 호출 메타데이터**를 넣는다.
       한 번의 호출이 전 부위를 처리하므로 전체 응답을 부위마다 복사하면 같은
       blob 이 부위 수만큼 중복된다. 항목 원문이 남아 있으면 프롬프트 튜닝의
       재현 목적(work-b.md §6)은 그대로 달성된다.
    """
    meta = _call_meta(result.get("raw_response") or {})
    by_name = {p["class_name"]: p for p in parts}
    rows: list[dict[str, Any]] = []

    def ids(name: str) -> dict[str, Any]:
        return {
            "reference_segment_id": (ref_segments.get(name) or {}).get("segment_id"),
            "user_segment_id": (user_segments.get(name) or {}).get("segment_id"),
        }

    for item in result["results"]:
        name = item["class_name"]
        blocked = item["blocked_reason"]
        rows.append(
            {
                "class_name": name,
                **ids(name),
                "vlm_input_type": str(VlmInputType.HIGHLIGHT),
                "differences": item["differences"],
                # 판단 불가인데 설명이 비어 있으면 화면에 빈칸이 뜬다. 사용자는
                # "왜 이 부위만 결과가 없지"를 알 수 있어야 한다.
                "assessment": item["assessment"]
                or (f"이 부위는 판단하지 못했습니다: {blocked}" if blocked else None),
                "gap_level": item["gap_level"],
                "priority": item["priority"],
                "confidence": item["confidence"],
                # ⚠️ blocked_reason 은 part_diagnosis 에 컬럼이 없다(스키마 §9).
                #    스키마를 늘리는 대신 item 원문에 남겨 F09 가 여기서 되읽는다.
                "raw_response": {"item": item, "call": meta},
                "status": str(DomainStatus.DONE),
            }
        )

    for name in result["missing"]:
        if name not in by_name:
            continue
        rows.append(
            {
                "class_name": name,
                **ids(name),
                "vlm_input_type": str(VlmInputType.HIGHLIGHT),
                "assessment": "이 부위는 진단 결과를 받지 못했습니다.",
                "raw_response": {"item": None, "call": meta},
                "status": str(DomainStatus.FAILED),
            }
        )

    return rows


# --------------------------------------------------------------------------- #
# VLM_OVERALL
# --------------------------------------------------------------------------- #


def _excluded_parts(session_id: UUID, diagnosed: set[str]) -> list[str]:
    """비교 대상 마스터에는 있는데 **진단 행조차 없는** 부위.

    미검출(사진에 안 잡힘)이나 교집합 탈락(한쪽만 유효)으로 F08 에 들어가지도
    못한 부위들이다. F09 에 알려주지 않으면 LLM 이 본 적 없는 부위를 근거로
    쓴다 — 실연동에서 "하체 균형이 좋습니다"가 나왔는데 하체는 검출조차
    안 된 상태였다 (A 발견).
    """
    try:
        master = set(db.comparable_class_names())
    except Exception:  # noqa: BLE001 — 부가 정보다. 실패해도 진단은 진행한다.
        log.warning("비교 대상 마스터를 읽지 못해 excluded 를 생략합니다.")
        return []
    return sorted(master - diagnosed)


def _for_overall(row: dict[str, Any]) -> dict[str, Any]:
    """part_diagnosis 행 → F09 프롬프트 입력.

    ⚠️ blocked_reason 은 컬럼이 아니라 raw_response.item 에 있다(_to_diagnosis_rows).
       되읽지 않으면 판단 불가 부위가 "격차 미상"으로 섞여 들어가 점수를 왜곡한다.
    """
    item = (row.get("raw_response") or {}).get("item") or {}
    return {
        "class_name": row["class_name"],
        "gap_level": row.get("gap_level"),
        "priority": row.get("priority"),
        "confidence": row.get("confidence"),
        "assessment": row.get("assessment"),
        "differences": row.get("differences") or [],
        "blocked_reason": item.get("blocked_reason"),
    }


def _diagnose_overall(job: dict[str, Any]) -> dict[str, Any]:
    session_id = UUID(str(job["session_id"]))
    rows = diagnosis_repo.list_part_diagnoses(session_id)
    if not rows:
        raise ValueError("부위별 진단이 없습니다. 분석을 다시 시작해주세요.")

    done = [r for r in rows if r["status"] == DomainStatus.DONE]
    failed = [r["class_name"] for r in rows if r["status"] == DomainStatus.FAILED]

    if not done:
        # 전 부위가 실패했다. 종합할 재료가 없으므로 FAILED 를 남기고 끝낸다 —
        # 여기서 억지로 요약을 만들면 근거 없는 문장이 화면에 뜬다.
        diagnosis_repo.upsert_overall(
            session_id,
            {"status": str(DomainStatus.FAILED), "score_source": str(ScoreSource.VLM)},
        )
        raise ValueError("모든 부위의 진단이 실패해 종합할 수 없습니다.")

    inbody, inbody_source = _inbody_for(session_id)
    overall_input = [_for_overall(r) for r in done]

    # 점수는 코드가 계산한다 (score_source=RULE). VLM 은 격차 "판정"까지만 하고
    # 판정→점수 합산은 규칙이다 — "68점이 왜 68점인가"에 공식으로 답하기 위해서.
    # 근거·공식은 app/services/scoring.py 모듈 docstring.
    scored = scoring.compute_similarity(overall_input)
    score = scored["score"] if scored else None

    result = asyncio.run(
        vlm.diagnose_overall(
            results=overall_input,
            failed=failed,
            inbody=inbody,
            score=score,
            # 검출조차 안 된 비교 대상 — 없으면 LLM 이 본 적 없는 부위를 칭찬한다.
            excluded=_excluded_parts(session_id, {r["class_name"] for r in rows}),
        )
    )

    diagnosis_repo.upsert_overall(
        session_id,
        {
            "similarity_score": score,
            "score_source": str(ScoreSource.RULE),
            "score_rationale": scored["rationale"] if scored else None,
            "summary": result["summary"],
            "priority_parts": result["priority_parts"],
            "strengths": result["strengths"],
            "cautions": result["cautions"],
            # breakdown 을 함께 박제 — 심사·디버깅 때 "이 점수가 어떻게 나왔나"를
            # 부위 단위까지 재구성할 수 있다.
            "raw_response": {
                "llm": result.get("raw_response"),
                "score_breakdown": scored["breakdown"] if scored else None,
            },
            "status": str(DomainStatus.DONE),
        },
    )

    return {
        "similarity_score": score,
        "judged": len(done),
        "failed": len(failed),
        "inbody": inbody_source,
    }


register(JobKind.VLM_PART, _diagnose_parts)
register(JobKind.VLM_OVERALL, _diagnose_overall)
