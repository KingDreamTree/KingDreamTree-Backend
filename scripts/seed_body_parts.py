"""body_part 마스터 seed — 멱등(idempotent).

Sapiens2 body-part segmentation 29개 클래스.

사용법:
    python scripts/seed_body_parts.py            # 적용
    python scripts/seed_body_parts.py --check    # 현재 DB와 대조만

✅ 클래스 이름과 인덱스 모두 공식 문서로 확인됐다.
   https://github.com/facebookresearch/sapiens2/blob/main/docs/SEG.md
   인덱스는 저장하지 않는다 — 추론 시점의 매핑을 segmentation.label_map 에
   행마다 박제하는 설계라 문제가 되지 않는다.

출처
  Sapiens(1세대) goliath 28클래스:
    https://github.com/facebookresearch/sapiens/blob/main/docs/SEG_README.md
  Sapiens2 = 위 28 + Eyeglass(2번에 삽입) = 29:
    https://github.com/facebookresearch/sapiens2/blob/main/docs/SEG.md
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_client():
    """.env를 직접 읽어 Supabase 클라이언트를 만든다.

    app 패키지에 의존하지 않는다 — 브랜치 머지 순서와 무관하게 돌아야 하고,
    seed는 앱 부팅 전에도 실행할 수 있어야 하기 때문이다.
    """
    from supabase import create_client

    env: dict[str, str] = {}
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()

    url = os.environ.get("SUPABASE_URL") or env.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or env.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        print("[X] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 가 없습니다. .env를 확인하세요.")
        sys.exit(1)
    return create_client(url, key)


# (class_name, name_ko, part_group, inbody_segment, is_comparable, color_hex, display_order)
BODY_PARTS: list[tuple[str, str, str, str | None, bool, str | None, int]] = [
    # ── 비교 대상 9개 — 맨살이 드러나는 부위 ────────────────────────────────
    # ⚠️ 좌/우를 비슷한 색 계열로 잡은 이유: 좌우 반전 사고를 눈으로 잡기 위해서.
    ("Torso", "몸통", "CORE", "TRUNK", True, "#4C6EF5", 1),
    ("Left_Upper_Arm", "왼쪽 위팔", "UPPER", "LEFT_ARM", True, "#F76707", 2),
    ("Left_Lower_Arm", "왼쪽 팔뚝", "UPPER", "LEFT_ARM", True, "#FFA94D", 3),
    ("Right_Upper_Arm", "오른쪽 위팔", "UPPER", "RIGHT_ARM", True, "#2F9E44", 4),
    ("Right_Lower_Arm", "오른쪽 팔뚝", "UPPER", "RIGHT_ARM", True, "#69DB7C", 5),
    ("Left_Upper_Leg", "왼쪽 허벅지", "LOWER", "LEFT_LEG", True, "#AE3EC9", 6),
    ("Left_Lower_Leg", "왼쪽 종아리", "LOWER", "LEFT_LEG", True, "#DA77F2", 7),
    ("Right_Upper_Leg", "오른쪽 허벅지", "LOWER", "RIGHT_LEG", True, "#E03131", 8),
    ("Right_Lower_Leg", "오른쪽 종아리", "LOWER", "RIGHT_LEG", True, "#FF8787", 9),
    # ── 비교 대상 아님 20개 — 맵에는 있지만 색칠하지 않는다 ─────────────────
    # ⚠️ 손·발·신발·양말은 좌우가 별도 클래스. 옷도 상/하의가 나뉜다.
    ("Background", "배경", "OTHER", None, False, None, 90),
    ("Apparel", "착용물", "OTHER", None, False, None, 91),
    ("Upper_Clothing", "상의", "OTHER", None, False, None, 92),
    ("Lower_Clothing", "하의", "OTHER", None, False, None, 93),
    ("Left_Shoe", "왼쪽 신발", "OTHER", None, False, None, 94),
    ("Right_Shoe", "오른쪽 신발", "OTHER", None, False, None, 95),
    ("Left_Sock", "왼쪽 양말", "OTHER", None, False, None, 96),
    ("Right_Sock", "오른쪽 양말", "OTHER", None, False, None, 97),
    ("Left_Hand", "왼손", "OTHER", None, False, None, 98),
    ("Right_Hand", "오른손", "OTHER", None, False, None, 99),
    ("Left_Foot", "왼발", "OTHER", None, False, None, 100),
    ("Right_Foot", "오른발", "OTHER", None, False, None, 101),
    ("Hair", "머리카락", "OTHER", None, False, None, 102),
    ("Face_Neck", "얼굴·목", "OTHER", None, False, None, 103),
    # ⚠️ 단수형이다 (Eyeglasses 아님). 공식 문서 기준.
    ("Eyeglass", "안경", "OTHER", None, False, None, 104),
    ("Upper_Lip", "윗입술", "OTHER", None, False, None, 105),
    ("Lower_Lip", "아랫입술", "OTHER", None, False, None, 106),
    ("Upper_Teeth", "윗니", "OTHER", None, False, None, 107),
    ("Lower_Teeth", "아랫니", "OTHER", None, False, None, 108),
    ("Tongue", "혀", "OTHER", None, False, None, 109),
]

COLUMNS = (
    "class_name",
    "name_ko",
    "part_group",
    "inbody_segment",
    "is_comparable",
    "color_hex",
    "display_order",
)


def as_rows() -> list[dict]:
    return [dict(zip(COLUMNS, row)) for row in BODY_PARTS]


def check() -> int:
    """DB 현재 상태를 seed와 대조한다. 다르면 1을 반환."""
    client = get_client()
    current = {r["class_name"]: r for r in client.table("body_part").select("*").execute().data}
    expected = {r["class_name"]: r for r in as_rows()}

    missing = sorted(set(expected) - set(current))
    extra = sorted(set(current) - set(expected))
    differing = [
        name
        for name in sorted(set(expected) & set(current))
        if any(current[name].get(c) != expected[name][c] for c in COLUMNS)
    ]

    print(f"DB {len(current)}개 / seed {len(expected)}개")
    if missing:
        print(f"  [X] DB에 없음  ({len(missing)}): {', '.join(missing)}")
    if extra:
        print(f"  [X] seed에 없음 ({len(extra)}): {', '.join(extra)}")
    if differing:
        print(f"  [X] 값 다름     ({len(differing)}): {', '.join(differing)}")
    if not (missing or extra or differing):
        comparable = sum(1 for r in current.values() if r["is_comparable"])
        print(f"  [O] 일치 — 비교 대상 {comparable}개")
        return 0
    return 1


def apply() -> int:
    """⚠️ body_part_segment가 참조 중이면 삭제가 막힌다. 그때는 재추론이 먼저다."""
    client = get_client()

    referenced = client.table("body_part_segment").select("segment_id").limit(1).execute().data
    if referenced:
        print("[X] body_part_segment에 참조 중인 행이 있어 seed를 갈아엎을 수 없습니다.")
        print("    클래스명이 바뀌면 기존 세그멘테이션 결과도 무효입니다.")
        print("    해당 데이터를 먼저 지우고(또는 재추론하고) 다시 실행하세요.")
        return 1

    client.table("body_part").delete().neq("class_name", "").execute()
    client.table("body_part").insert(as_rows()).execute()
    print(f"[O] body_part {len(BODY_PARTS)}개 적용 완료")
    return check()


if __name__ == "__main__":
    sys.exit(check() if "--check" in sys.argv else apply())
