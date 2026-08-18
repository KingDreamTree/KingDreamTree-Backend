"""부위 짝짓기 — 사용자 부위와 레퍼런스 부위를 어느 쪽끼리 비교할지.

⚠️ **2026-08-18 개정 — 교차 짝짓기를 껐다. 지금은 항상 "같은 이름끼리"다.**
   실시간 촬영도 직접 업로드와 같은 기준으로 통일하기로 했다. 종전에는 촬영
   사진을 비반전 원본으로 저장했기 때문에 거울 매칭(아래 역사)을 교차로 맞춰야
   했지만, 이제 프론트가 셔터 순간에 사진·랜드마크를 거울 방향으로 세트 반전해
   저장하므로 저장본이 이미 레퍼런스와 같은 방향이다. is_cross_paired docstring 참고.

역사 — 교차가 왜 있었나 (되돌릴 때를 위해 남긴다)
    실시간 촬영(CAPTURE)은 거울 미리보기를 보며 레퍼런스를 따라 하고, 자세 채점도
    레퍼런스를 좌우 반전시켜 비교한다 (web/pose-score.js). 그래서 레퍼런스가
    왼팔을 든 사진이면 사용자는 **오른팔**을 들어야 셔터가 터진다. 원본을 저장하던
    시절에는 "같은 이름끼리" 비교하면 자세가 다른 팔끼리 붙어서, 사용자 부위 ↔
    레퍼런스의 좌우 반대 부위로 교차했다 (2026-08-17 프론트 합의, 하루 만에 개정).

⚠️ 짝짓기 규칙은 반드시 이 모듈만 거친다. 비교 지점(세션 비교 API, VLM 부위
   진단)이 각자 규칙을 하드코딩하면, 한 곳만 다르게 갔을 때 왼팔 진단문에
   오른팔 그림이 붙는데 **에러는 나지 않는다.** 그 사고를 막는 유일한 방법이
   단일 관문이다 — 켜고 끄는 것도 이 관문 한 곳에서만 한다.
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
    """이 세션의 비교가 좌우 교차 짝짓기인지 — **2026-08-18 부터 항상 아니다.**

    실시간 촬영(CAPTURE)을 직접 업로드와 같은 기준으로 통일했다. 프론트가
    셔터 순간에 **사진과 랜드마크를 거울 방향으로 세트 반전해 저장**하므로,
    저장본이 이미 레퍼런스와 같은 방향이다 — 교차로 맞춰줄 어긋남 자체가 없다.
    (종전에는 원본을 저장하고 이 관문이 True 를 돌려줘 진단·비교·표시가
    좌우를 교차했다. 그 세 갈래가 전부 이 함수 하나를 경유한다.)

    ⚠️ 함수와 mirror_class 를 지우지 않고 남긴 이유 — 호출부(진단 컨텍스트,
       세션 비교 API, VLM 오버레이)가 전부 이 관문을 경유하므로, 되돌릴 일이
       생기면 여기 한 곳만 살리면 된다.

    ⚠️ 이 결정 이전에 저장된 CAPTURE 세션은 원본 방향으로 저장돼 있어, 새
       기준으로 조회하면 좌우 포즈가 어긋나 보인다. 과거 세션은 재촬영 대상이다.

    ⚠️ 거울 방향 저장의 대가 — 저장본의 "왼팔"은 실제 몸의 오른팔이다.
       인바디 좌우 실측을 진단에 섞는 경로(B 파트)와 어긋나므로 B 와 논의 필요.
    """
    _ = user_photo  # 시그니처 유지 — 호출부 수정 없이 규칙만 껐다
    return False


def reference_class_for(user_class: str, cross_paired: bool) -> str:
    """사용자 부위 user_class 와 비교할 레퍼런스 부위명."""
    return mirror_class(user_class) if cross_paired else user_class
