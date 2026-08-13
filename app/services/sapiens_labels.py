"""Sapiens2 라벨 인덱스 ↔ 클래스명 매핑.

출처 — 공식 문서
    https://github.com/facebookresearch/sapiens2/blob/main/docs/SEG.md
    (모델 카드 https://huggingface.co/facebook/sapiens2-seg-5b 가 이 문서로 안내한다)

⚠️ **모델 파일에서는 읽을 수 없다.** config.json 의 id2label 이
   "LABEL_0" ... "LABEL_28" 플레이스홀더라, 어느 픽셀 값이 어느 부위인지
   가중치만 받아서는 알 수 없다. 위 문서가 유일한 출처다.

⚠️ **이 목록이 틀리면 전 부위가 어긋난 채 에러 없이 돌아간다.**
   왼팔을 오른다리로 읽으면서 프로그램은 멀쩡히 끝난다. 그래서
     * 모델이 뱉은 클래스 수와 이 목록의 길이가 다르면 즉시 실패시키고
     * 이 목록에 없는 모델로 돌리려 하면 워커가 기동을 거부하고
     * 추론할 때마다 그 시점의 매핑을 segmentation.label_map 에 박제한다
   마지막 것 덕분에 나중에 모델을 바꿔도 과거 데이터는 안전하다.
"""

import logging

from app.config import settings

log = logging.getLogger("services.sapiens_labels")

#: 이 목록이 적용되는 모델. 다른 체크포인트를 쓰려면 그 모델 문서에서
#: 클래스 목록을 확인하고 여기에 추가할 것.
SUPPORTED_MODELS: tuple[str, ...] = (
    "sapiens2-seg-0.4b",
    "sapiens2-seg-0.8b",
    "sapiens2-seg-1b",
    "sapiens2-seg-5b",
)

#: Sapiens2 body-part segmentation 29클래스. 인덱스 = 라벨 맵의 픽셀 값.
#: ⚠️ `Eyeglass` 는 단수형이다. 복수형으로 적으면 body_part 마스터 대조에서 걸린다.
LABEL_NAMES: tuple[str, ...] = (
    "Background",  # 0
    "Apparel",  # 1
    "Eyeglass",  # 2
    "Face_Neck",  # 3
    "Hair",  # 4
    "Left_Foot",  # 5
    "Left_Hand",  # 6
    "Left_Lower_Arm",  # 7
    "Left_Lower_Leg",  # 8
    "Left_Shoe",  # 9
    "Left_Sock",  # 10
    "Left_Upper_Arm",  # 11
    "Left_Upper_Leg",  # 12
    "Lower_Clothing",  # 13
    "Right_Foot",  # 14
    "Right_Hand",  # 15
    "Right_Lower_Arm",  # 16
    "Right_Lower_Leg",  # 17
    "Right_Shoe",  # 18
    "Right_Sock",  # 19
    "Right_Upper_Arm",  # 20
    "Right_Upper_Leg",  # 21
    "Torso",  # 22
    "Upper_Clothing",  # 23
    "Lower_Lip",  # 24
    "Upper_Lip",  # 25
    "Lower_Teeth",  # 26
    "Upper_Teeth",  # 27
    "Tongue",  # 28
)

#: ⚠️ Left / Right 는 **피사체 기준**이다. 정면 사진에서 피사체의 왼팔은
#:    이미지의 오른쪽(x가 큰 쪽)에 나온다. 화면 기준으로 착각하면 진단이
#:    통째로 좌우 반대가 되고, 그래도 에러는 안 난다.
#:    확인 절차: scripts/verify_labels.py


class LabelsNotVerifiedError(RuntimeError):
    """이 모델의 클래스 목록을 확인하지 않았다."""


def build_label_map(num_classes: int) -> dict[str, str]:
    """segmentation.label_map 에 저장할 {인덱스(str): 클래스명} 을 만든다.

    ⚠️ 모델 출력 클래스 수와 목록 길이가 다르면 즉시 실패시킨다.
       길이가 안 맞는데 진행하면 어긋난 라벨로 전부 저장된다.
    """
    if len(LABEL_NAMES) != num_classes:
        raise ValueError(
            f"모델 출력 클래스 수({num_classes})와 라벨 목록 길이({len(LABEL_NAMES)})가 "
            "다릅니다. 다른 모델일 수 있습니다 — 그 모델의 문서에서 클래스 목록을 "
            "확인하세요."
        )
    return {str(i): name for i, name in enumerate(LABEL_NAMES)}


def check_against_master(label_map: dict[str, str], master_class_names: set[str]) -> list[str]:
    """label_map 의 클래스명 중 body_part 마스터에 없는 것들을 돌려준다.

    ⚠️ 워커 기동 시 반드시 호출할 것. 조용히 넘어가면 seed 불일치를 못 잡는다.
    """
    return sorted({name for name in label_map.values() if name not in master_class_names})


def ensure_supported(model_version: str) -> None:
    """이 라벨 목록이 적용되는 모델인지 확인한다.

    ⚠️ 다른 체크포인트는 클래스 순서가 다를 수 있다. 확인 없이 돌리면
       부위가 통째로 어긋난 결과가 저장되고, 에러는 나지 않는다.
    """
    if model_version in SUPPORTED_MODELS:
        return

    message = (
        f"{model_version} 는 라벨 목록이 확인된 모델이 아닙니다 "
        f"(확인된 모델: {', '.join(SUPPORTED_MODELS)}).\n"
        "그 모델의 문서에서 클래스 목록을 확인하고 "
        "app/services/sapiens_labels.py 의 SUPPORTED_MODELS 에 추가하세요."
    )
    if settings.sapiens_strict_labels:
        raise LabelsNotVerifiedError(message)
    log.warning("%s", message)
