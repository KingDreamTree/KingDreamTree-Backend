"""DB CHECK 제약과 1:1로 대응하는 열거형.

⚠️ 값은 전부 대문자다. 소문자를 넣으면 INSERT가 CHECK 위반으로 터진다.
   문자열 리터럴을 코드에 흩뿌리지 말고 반드시 여기를 import해서 쓸 것.
   (db/schema.sql의 CHECK 목록이 유일한 진실이고, 이 파일은 그 미러다)
"""

from enum import StrEnum


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class ReferenceSource(StrEnum):
    USER_UPLOAD = "USER_UPLOAD"
    PRESET = "PRESET"


class PhotoKind(StrEnum):
    REFERENCE = "REFERENCE"
    USER = "USER"


class CaptureSource(StrEnum):
    CAPTURE = "CAPTURE"
    UPLOAD = "UPLOAD"


class PoseScaleBasis(StrEnum):
    """⚠️ 레퍼런스와 사용자가 반드시 같은 기준을 써야 한다.

    하체만 나온 프레임은 어깨가 안 보여 몸통 길이를 못 재므로 HIP_KNEE로 대체한다.
    """

    TORSO = "TORSO"
    HIP_KNEE = "HIP_KNEE"


class PartGroup(StrEnum):
    UPPER = "UPPER"
    CORE = "CORE"
    LOWER = "LOWER"
    OTHER = "OTHER"  # 옷·머리·배경 등 비교 대상 아님


class InbodySegment(StrEnum):
    LEFT_ARM = "LEFT_ARM"
    RIGHT_ARM = "RIGHT_ARM"
    TRUNK = "TRUNK"
    LEFT_LEG = "LEFT_LEG"
    RIGHT_LEG = "RIGHT_LEG"


class InvalidReason(StrEnum):
    TOO_SMALL = "TOO_SMALL"
    TOO_SMALL_RATIO = "TOO_SMALL_RATIO"
    TRUNCATED = "TRUNCATED"
    NOT_COMPARABLE = "NOT_COMPARABLE"


class DomainStatus(StrEnum):
    """도메인 테이블의 status — "이 데이터를 화면에 써도 되는가".

    ⚠️ JobStatus(실행 상태)와 다르다. 혼동 금지.
    """

    PENDING = "PENDING"
    DONE = "DONE"
    FAILED = "FAILED"


class VlmInputType(StrEnum):
    CROP = "CROP"
    HIGHLIGHT = "HIGHLIGHT"


class GapLevel(StrEnum):
    NONE = "NONE"
    SLIGHT = "SLIGHT"
    MODERATE = "MODERATE"
    SIGNIFICANT = "SIGNIFICANT"


class Confidence(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ScoreSource(StrEnum):
    VLM = "VLM"
    RULE = "RULE"


class GenerationType(StrEnum):
    INITIAL = "INITIAL"
    DAYS_CHANGED = "DAYS_CHANGED"
    FEEDBACK = "FEEDBACK"


class Gender(StrEnum):
    MALE = "MALE"
    FEMALE = "FEMALE"


class JobKind(StrEnum):
    SEG_REFERENCE = "SEG_REFERENCE"
    SEG_USER = "SEG_USER"
    OCR_INBODY = "OCR_INBODY"
    VLM_PART = "VLM_PART"
    VLM_OVERALL = "VLM_OVERALL"
    ROUTINE_GEN = "ROUTINE_GEN"
    ROUTINE_PATCH = "ROUTINE_PATCH"


class JobStatus(StrEnum):
    """잡의 실행 상태 — 재시도·에러·워커 소유권.

    ⚠️ DomainStatus(화면 노출 가능 여부)와 다르다.
    """

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"


#: 담당 A가 처리하는 잡
SEG_KINDS: tuple[JobKind, ...] = (JobKind.SEG_REFERENCE, JobKind.SEG_USER)

#: 담당 B가 처리하는 잡
LLM_KINDS: tuple[JobKind, ...] = (
    JobKind.OCR_INBODY,
    JobKind.VLM_PART,
    JobKind.VLM_OVERALL,
    JobKind.ROUTINE_GEN,
    JobKind.ROUTINE_PATCH,
)
