"""부위 짝짓기 — 사용자 부위와 레퍼런스 부위를 어느 쪽끼리 비교할지.

왜 이 모듈이 있나
    실시간 촬영(CAPTURE)은 거울 미리보기를 보며 레퍼런스를 따라 하고, 자세 채점도
    레퍼런스를 좌우 반전시켜 비교한다 (web/pose-score.js). 그래서 레퍼런스가
    왼팔을 든 사진이면 사용자는 **오른팔**을 들어야 셔터가 터진다 — 촬영 사진은
    구조적으로 항상 거울 매칭이다.

    저장된 사진 자체는 양쪽 다 비반전 원본이므로 픽셀은 건드릴 게 없다. 대신
    "같은 이름끼리" 비교하면 자세가 다른 팔·다리끼리 비교하게 된다
    (사용자 오른팔(든 팔) vs 레퍼런스 오른팔(내린 팔)). 공정한 비교는
    **사용자 부위 ↔ 레퍼런스의 좌우 반대 부위**다.

⚠️ 짝짓기 규칙은 반드시 이 모듈만 거친다. 비교 지점(세션 비교 API, VLM 부위
   진단)이 각자 "같은 이름끼리"를 하드코딩하면, 한 곳만 교차를 반영했을 때
   왼팔 진단문에 오른팔 그림이 붙는데 **에러는 나지 않는다.** 그 사고를 막는
   유일한 방법이 단일 관문이다.

⚠️ 교차 여부는 저장된 `photo.capture_source` 에서 도출한다 — 새 필드가 아니다.
   CAPTURE 는 위 이유로 항상 교차, UPLOAD(갤러리)는 "남이 찍어준 사진" 가정으로
   비교차 (2026-08-17 프론트 합의). 과거 세션에도 그대로 소급 적용된다.
"""

from __future__ import annotations

from typing import Any

#: 좌우 대칭 부위의 미러 매핑. 여기 없는 부위(Torso 등)는 자기 자신과 짝.
_MIRROR: dict[str, str] = {
    "Left_Upper_Arm": "Right_Upper_Arm",
    "Right_Upper_Arm": "Left_Upper_Arm",
    "Left_Lower_Arm": "Right_Lower_Arm",
    "Right_Lower_Arm": "Left_Lower_Arm",
    "Left_Upper_Leg": "Right_Upper_Leg",
    "Right_Upper_Leg": "Left_Upper_Leg",
    "Left_Lower_Leg": "Right_Lower_Leg",
    "Right_Lower_Leg": "Left_Lower_Leg",
}


def mirror_class(class_name: str) -> str:
    """좌우 반대 부위명. 비대칭 개념이 없는 부위는 그대로."""
    return _MIRROR.get(class_name, class_name)


def is_cross_paired(user_photo: dict[str, Any] | None) -> bool:
    """이 세션의 비교가 좌우 교차 짝짓기인지 — **사용자 사진** 행으로 판단한다.

    레퍼런스 사진의 capture_source 는 보지 않는다. 거울 매칭은 "사용자가
    레퍼런스를 따라 찍는" 과정에서 생기므로 사용자 쪽 촬영 방식만이 근거다.
    """
    if not user_photo:
        return False
    return str(user_photo.get("capture_source") or "") == "CAPTURE"


def reference_class_for(user_class: str, cross_paired: bool) -> str:
    """사용자 부위 user_class 와 비교할 레퍼런스 부위명."""
    return mirror_class(user_class) if cross_paired else user_class
