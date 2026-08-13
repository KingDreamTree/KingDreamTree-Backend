"""part_diagnosis / overall_diagnosis 쿼리 + 비교 대상 부위 산출 — 담당 B 소유.

app/services/db.py 는 공유 파일이라 B 전용 쿼리를 여기로 분리한다
(inbody_repo.py 와 같은 이유). 공용 클라이언트만 db.get_client() 로 빌려 쓴다.

⚠️ **A 의 segmenter.py 를 import 하지 않는다.** A 와의 계약은 DB 뿐이다
   (work-b.md §4). 여기서 읽는 것은 segmentation / body_part_segment / body_part
   세 테이블이고, 조인 키는 항상 `class_name` 이다 — `label_value` 는 모델 버전이
   바뀌면 같은 숫자가 다른 부위를 가리킨다.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.schemas.enums import DomainStatus, PhotoKind
from app.services.db import get_client, get_photo, get_segmentation, list_part_segments

#: 인물로 치는 부위 그룹. 정규화 분모(인물 전체 픽셀)를 낼 때 쓴다.
#: 'OTHER'(배경·의류·머리카락 등)를 분모에 넣으면 헐렁한 옷을 입었을 때
#: 분모가 부풀어 모든 부위가 작아 보인다.
_PERSON_GROUPS = ("UPPER", "CORE", "LOWER")


# --------------------------------------------------------------------------- #
# 비교 대상 부위 — VLM 호출 범위를 결정한다
# --------------------------------------------------------------------------- #


def build_comparison_context(session_id: UUID) -> dict[str, Any]:
    """진단에 필요한 재료를 한 번에 모은다.

    Returns:
        {
          "ready": bool,              # 세그멘테이션 두 장이 다 있는가
          "missing": [str],           # 없는 쪽 ("REFERENCE" | "USER")
          "photos":   {kind: row},
          "segmentations": {kind: row},
          "segments": {kind: {class_name: row}},   # is_valid 무관 전부
          "parts":    [body_part 행],  # 교집합 통과한 비교 대상만, display_order 순
          "excluded": [{class_name, name_ko, reason, side}],
          "person_classes": set[str],
        }

    ⚠️ 교집합 조건 = 레퍼런스·사용자 **양쪽 모두** is_valid 인 is_comparable 부위
       (work-b.md §4 의 SQL 과 같은 의미). 한쪽만 유효하면 비교 자체가 불가능하다.
    """
    photos: dict[str, Any] = {}
    segmentations: dict[str, Any] = {}
    segments: dict[str, dict[str, Any]] = {}
    missing: list[str] = []

    for kind in (PhotoKind.REFERENCE, PhotoKind.USER):
        photo = get_photo(session_id, kind)
        if photo is None:
            missing.append(str(kind))
            continue
        photos[str(kind)] = photo

        seg = get_segmentation(UUID(str(photo["photo_id"])))
        if seg is None:
            missing.append(str(kind))
            continue
        segmentations[str(kind)] = seg
        rows = list_part_segments(UUID(str(seg["segmentation_id"])))
        segments[str(kind)] = {r["class_name"]: r for r in rows}

    master = get_client().table("body_part").select("*").order("display_order").execute().data
    person_classes = {m["class_name"] for m in master if m["part_group"] in _PERSON_GROUPS}

    if missing:
        return {
            "ready": False,
            "missing": missing,
            "photos": photos,
            "segmentations": segmentations,
            "segments": segments,
            "parts": [],
            "excluded": [],
            "person_classes": person_classes,
        }

    ref_segments = segments[str(PhotoKind.REFERENCE)]
    user_segments = segments[str(PhotoKind.USER)]

    parts: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for bp in master:
        if not bp.get("is_comparable"):
            continue
        name = bp["class_name"]
        ref, user = ref_segments.get(name), user_segments.get(name)

        # 사용자 쪽 사유를 우선 노출한다 — 사용자가 취할 행동(재촬영)에 직결된다.
        if user is None or not user.get("is_valid"):
            excluded.append(
                {
                    "class_name": name,
                    "name_ko": bp.get("name_ko"),
                    "reason": (user or {}).get("invalid_reason") or "NOT_DETECTED",
                    "side": str(PhotoKind.USER),
                }
            )
            continue
        if ref is None or not ref.get("is_valid"):
            excluded.append(
                {
                    "class_name": name,
                    "name_ko": bp.get("name_ko"),
                    "reason": (ref or {}).get("invalid_reason") or "NOT_DETECTED",
                    "side": str(PhotoKind.REFERENCE),
                }
            )
            continue
        parts.append(bp)

    return {
        "ready": True,
        "missing": [],
        "photos": photos,
        "segmentations": segmentations,
        "segments": segments,
        "parts": parts,
        "excluded": excluded,
        "person_classes": person_classes,
    }


# --------------------------------------------------------------------------- #
# part_diagnosis
# --------------------------------------------------------------------------- #


def replace_part_diagnoses(session_id: UUID, rows: list[dict[str, Any]]) -> None:
    """세션의 부위 진단을 통째로 교체한다.

    ⚠️ UNIQUE (session_id, class_name) 이라 잡 재시도(attempts>1)에서 그냥 INSERT 하면
       두 번째 시도가 터진다. delete → insert 로 통일한다 (inbody_repo 와 같은 규약).
    """
    client = get_client()
    client.table("part_diagnosis").delete().eq("session_id", str(session_id)).execute()
    if rows:
        client.table("part_diagnosis").insert(
            [{**r, "session_id": str(session_id)} for r in rows]
        ).execute()


def list_part_diagnoses(session_id: UUID) -> list[dict[str, Any]]:
    return (
        get_client()
        .table("part_diagnosis")
        .select("*")
        .eq("session_id", str(session_id))
        .execute()
        .data
    )


def count_part_diagnoses(session_id: UUID) -> dict[str, int]:
    """진행률용 집계. 잡이 아니라 **실제 진단 행**을 센다.

    ⚠️ 잡 개수로 세면 안 된다. 부위 진단은 잡 1개가 전 부위를 처리하므로
       잡 기준으로는 항상 0/1 이 되어 화면의 "완료 3/9"를 만들 수 없다.
    """
    rows = (
        get_client()
        .table("part_diagnosis")
        .select("status")
        .eq("session_id", str(session_id))
        .execute()
        .data
    )
    return {
        "done": sum(1 for r in rows if r["status"] == DomainStatus.DONE),
        "failed": sum(1 for r in rows if r["status"] == DomainStatus.FAILED),
        "total": len(rows),
    }


# --------------------------------------------------------------------------- #
# overall_diagnosis
# --------------------------------------------------------------------------- #


def upsert_overall(session_id: UUID, row: dict[str, Any]) -> dict[str, Any]:
    """세션당 1건 (UNIQUE session_id). 재실행하면 덮어쓴다."""
    return (
        get_client()
        .table("overall_diagnosis")
        .upsert({**row, "session_id": str(session_id)}, on_conflict="session_id")
        .execute()
        .data[0]
    )


def get_overall(session_id: UUID) -> dict[str, Any] | None:
    rows = (
        get_client()
        .table("overall_diagnosis")
        .select("*")
        .eq("session_id", str(session_id))
        .execute()
        .data
    )
    return rows[0] if rows else None


def clear_diagnoses(session_id: UUID) -> None:
    """재분석 전 초기화. 부위·종합을 함께 지운다."""
    client = get_client()
    client.table("part_diagnosis").delete().eq("session_id", str(session_id)).execute()
    client.table("overall_diagnosis").delete().eq("session_id", str(session_id)).execute()
