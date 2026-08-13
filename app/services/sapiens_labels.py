"""Sapiens2 라벨 인덱스 ↔ 클래스명 매핑.

⚠️ 이 파일이 이 프로젝트에서 가장 조용히 망가지기 쉬운 지점이다.

배경
    config.json 의 id2label 은 "LABEL_0" ... "LABEL_28" 플레이스홀더다.
    즉 **어느 픽셀 값이 어느 부위인지 모델 파일만으로는 알 수 없다.**

    Sapiens(1세대) goliath 28클래스의 순서는 공식 문서로 확인됐다:
      0 = Background, 1~22 = 알파벳순, 23~27 = 나중에 추가된 얼굴 세부 클래스
    Sapiens2는 여기에 Eyeglasses 하나가 늘어 29개다. 그런데 그게
      (A) 맨 뒤 28번에 붙었는지
      (B) 알파벳순으로 2번에 끼어들어 나머지가 한 칸씩 밀렸는지
    문서에 없다. 그리고 이걸 틀리면 **전 부위가 어긋난 채 에러 없이** 돌아간다.

그래서
    후보를 상수로 두고, 실측으로 하나를 고른 뒤 VERIFIED_ORDER 에 박는다.
    검증 전에는 워커가 실행을 거부한다 (settings.sapiens_require_verified_labels).
    검증 절차: scripts/verify_labels.py

    확정된 매핑은 추론할 때마다 segmentation.label_map 에 행 단위로 박제되므로,
    나중에 모델을 바꿔도 과거 데이터는 안전하다.
"""

from app.config import settings

#: Sapiens(1세대) goliath 28클래스 — 공식 문서로 확인된 순서.
#: https://github.com/facebookresearch/sapiens/blob/main/docs/SEG_README.md
SAPIENS1_GOLIATH_28: tuple[str, ...] = (
    "Background",  # 0
    "Apparel",  # 1
    "Face_Neck",  # 2
    "Hair",  # 3
    "Left_Foot",  # 4
    "Left_Hand",  # 5
    "Left_Lower_Arm",  # 6
    "Left_Lower_Leg",  # 7
    "Left_Shoe",  # 8
    "Left_Sock",  # 9
    "Left_Upper_Arm",  # 10
    "Left_Upper_Leg",  # 11
    "Lower_Clothing",  # 12
    "Right_Foot",  # 13
    "Right_Hand",  # 14
    "Right_Lower_Arm",  # 15
    "Right_Lower_Leg",  # 16
    "Right_Shoe",  # 17
    "Right_Sock",  # 18
    "Right_Upper_Arm",  # 19
    "Right_Upper_Leg",  # 20
    "Torso",  # 21
    "Upper_Clothing",  # 22
    "Lower_Lip",  # 23
    "Upper_Lip",  # 24
    "Lower_Teeth",  # 25
    "Upper_Teeth",  # 26
    "Tongue",  # 27
)

#: 후보 A — Eyeglasses가 맨 뒤에 append 됨
_CANDIDATE_APPEND: tuple[str, ...] = SAPIENS1_GOLIATH_28 + ("Eyeglasses",)

#: 후보 B — Eyeglasses가 알파벳순으로 Apparel과 Face_Neck 사이에 삽입됨
_CANDIDATE_ALPHA: tuple[str, ...] = (
    SAPIENS1_GOLIATH_28[:2] + ("Eyeglasses",) + SAPIENS1_GOLIATH_28[2:]
)

CANDIDATES: dict[str, tuple[str, ...]] = {
    "append": _CANDIDATE_APPEND,
    "alpha": _CANDIDATE_ALPHA,
}

#: ⚠️ 실측으로 확정한 뒤 여기에 후보 이름을 박는다. None이면 미검증.
#:    scripts/verify_labels.py 를 돌려 확인한 값을 넣을 것.
#:
#:    2026-08-13 확정 — 정면 전신 사진(팔다리 노출)으로 실측.
#:      alpha  통과 26 / 실패 0   점수 72
#:      append 통과 20 / 실패 3   점수 46
#:    append로 읽으면 Left_Foot이 화면 맨 위(y=96, 머리카락 자리)에 온다.
#:    alpha는 좌우 배치까지 통과했다:
#:      Left_Upper_Arm x=520 vs Right_Upper_Arm x=245 (정면 기준 피사체의 왼쪽 = x가 큼)
#:    fp16 / bfloat16 결과는 픽셀 수까지 0.5% 이내로 동일해 정밀도 영향은 없었다.
VERIFIED_ORDER: str | None = "alpha"

#: 검증에 쓴 모델. 크기가 달라도 29클래스 어휘는 같지만, 다른 모델로 넘어가면 재확인한다.
VERIFIED_WITH: str | None = "sapiens2-seg-5b"


class LabelsNotVerifiedError(RuntimeError):
    """라벨 매핑이 아직 실측 검증되지 않았다."""


def label_names(order: str | None = None) -> tuple[str, ...]:
    """인덱스 순서대로 정렬된 클래스명."""
    key = order or VERIFIED_ORDER
    if key is None:
        raise LabelsNotVerifiedError(
            "Sapiens2 라벨 매핑이 아직 검증되지 않았습니다.\n"
            "  python scripts/verify_labels.py --image <사람 사진>\n"
            "을 돌려 후보를 확정하고 app/services/sapiens_labels.py 의 "
            "VERIFIED_ORDER 에 넣으세요."
        )
    if key not in CANDIDATES:
        raise ValueError(f"알 수 없는 후보: {key}. 가능한 값: {sorted(CANDIDATES)}")
    return CANDIDATES[key]


def build_label_map(num_classes: int, order: str | None = None) -> dict[str, str]:
    """segmentation.label_map 에 저장할 {인덱스(str): 클래스명} 을 만든다.

    ⚠️ 모델 출력 클래스 수와 매핑 길이가 다르면 즉시 실패시킨다.
       길이가 안 맞는데 진행하면 어긋난 라벨로 전부 저장된다.
    """
    names = label_names(order)
    if len(names) != num_classes:
        raise ValueError(
            f"모델 출력 클래스 수({num_classes})와 라벨 매핑 길이({len(names)})가 다릅니다. "
            "모델이 바뀌었을 수 있습니다 — 라벨 매핑을 재검증하세요."
        )
    return {str(i): name for i, name in enumerate(names)}


def check_against_master(label_map: dict[str, str], master_class_names: set[str]) -> list[str]:
    """label_map 의 클래스명 중 body_part 마스터에 없는 것들을 돌려준다.

    ⚠️ 워커 기동 시 반드시 호출할 것. 조용히 넘어가면 seed 불일치를 못 잡는다.
    """
    return sorted({name for name in label_map.values() if name not in master_class_names})


def ensure_verified() -> str:
    """검증 상태를 확인하고 확정된 후보 이름을 돌려준다."""
    if VERIFIED_ORDER is None and settings.sapiens_require_verified_labels:
        raise LabelsNotVerifiedError(
            "라벨 매핑 미검증 상태에서는 세그멘테이션을 실행하지 않습니다.\n"
            "검증을 건너뛰려면 SAPIENS_REQUIRE_VERIFIED_LABELS=false 로 두세요 "
            "(⚠️ 부위가 통째로 뒤바뀐 결과가 저장될 수 있습니다)."
        )
    return VERIFIED_ORDER or "append"
