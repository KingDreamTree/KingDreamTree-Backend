"""애플리케이션 설정.

⚠️ 튜닝 대상 값은 절대 코드에 박지 말고 여기(=.env)로 뺀다.
   임계값이 바뀔 때마다 코드를 고치면 나중에 "어느 값으로 뽑은 결과인지" 추적이 안 된다.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # model_dir 처럼 model_ 로 시작하는 필드에 대한 pydantic 경고 억제
        protected_namespaces=(),
    )

    # ------------------------------------------------------------------ #
    # Supabase
    # ------------------------------------------------------------------ #
    supabase_url: str = ""
    # ⚠️ SUPABASE_ANON_KEY 는 2026-08-14 에 뺐다. 프론트가 Supabase 에 직접
    #    붙지 않아 서버에서 쓸 일이 없다. .env 에 남겨두면 "채워야 하나?" 싶어진다.
    # 구 service_role key. ⚠️ RLS를 전부 우회한다. 서버 사이드 전용.
    supabase_service_role_key: str = ""

    # ------------------------------------------------------------------ #
    # Storage 버킷 (전부 private)
    # ------------------------------------------------------------------ #
    bucket_photos: str = "photos"
    bucket_segmentations: str = "segmentations"
    bucket_body_parts: str = "body-parts"
    bucket_inbody_temp: str = "inbody-temp"

    signed_url_expires_sec: int = 3600
    signed_url_max_batch: int = 30

    # ------------------------------------------------------------------ #
    # 업로드 제한
    # ------------------------------------------------------------------ #
    max_upload_bytes: int = 10 * 1024 * 1024  # 10MB
    max_image_side: int = 4096  # 초과 시 서버가 리사이즈
    inbody_max_files: int = 5

    # ------------------------------------------------------------------ #
    # 포즈 판정 — ⚠️ 전부 튜닝 대상 잠정값
    # ------------------------------------------------------------------ #
    # ⚠️ 산식과 각 값의 근거는 docs/pose-scoring.md 에 있다. 숫자만 보고 바꾸지 말 것.
    #    프론트가 GET /pose-criteria 로 이 값들을 받아 쓰므로, 여기만 고치면 양쪽이 같이 움직인다.
    pose_threshold: float = 70.0  # THRESHOLD: 자세 점수 하한. TOL=45 기준 평균 오차 13.5°
    framing_f_min: float = 0.65  # F_MIN: 인물 bbox IoU 하한
    pose_tol_deg: float = 45.0  # TOL: 관절 하나가 0점이 되는 각도 차
    pose_hard_tol_deg: float = 60.0  # HARD: 하나라도 넘으면 즉시 탈락 (그 부위는 못 씀)
    pose_facing_max_delta: float = 0.25  # R_MAX: 어깨폭/몸통길이 비율 차 상한 (몸 돌아감)
    pose_min_visible_angles: int = 4  # 유효 각도가 이보다 적으면 판정 불가
    pose_min_visibility: float = 0.5  # 이 미만인 관절은 각도 계산에서 뺀다
    pose_n_hold: int = 15  # N_HOLD: 자동 촬영 유지 프레임 수 (프론트 참고용)

    # ------------------------------------------------------------------ #
    # 세그멘테이션 — ⚠️ 전부 튜닝 대상 잠정값
    # ------------------------------------------------------------------ #
    seg_min_pixels: int = 1500  # MIN_PIXELS: 유효 부위 최소 픽셀
    seg_min_ratio: float = 0.005  # MIN_RATIO: 인물 면적 대비 최소 비율 (0.5%)
    #: MAP_MAX_SIDE: 라벨 맵 긴 변 상한. 넘으면 NEAREST로 줄인 뒤 통계를 낸다.
    #  ⚠️ 지금 모델 출력이 768x1024라 실질 무동작이다. 모델을 바꿔 출력이 커질 때
    #     전송량과 저장량을 묶어두는 안전장치다.
    map_max_side: int = 1024
    min_comparable_parts: int = 3  # 비교 가능 부위가 이보다 적으면 재촬영 안내

    #: 업로드 시 VLM 으로 "이 사진으로 실루엣 비교가 되는가"를 판정할지.
    #  ⚠️ 동기 호출이라 업로드 응답이 2~5초 느려진다. 그래도 동기인 이유는
    #     목적이 재촬영 유도이기 때문 — 비동기면 사용자가 다음 화면으로 넘어간
    #     뒤에 알림이 뜬다.
    photo_screening_enabled: bool = True
    #: ⚠️ 넘기면 통과시킨다(fail-open). 업로드가 통째로 막히는 것보다 낫다는 판단.
    photo_screening_timeout_sec: float = 10.0
    #: 판정에 보낼 이미지의 긴 변 상한. 저장용 원본을 그대로 보내면 토큰이 낭비된다 —
    #  옷 밀착도·촬영 거리·잘림은 작은 이미지로도 판별되고, 두 장을 보내므로 두 배다.
    photo_screening_max_side: int = 768

    #: 옷 픽셀을 인접한 부위로 흡수해 통계를 낼지 (app/services/part_merge.py).
    #  ⚠️ **is_valid 판정보다 먼저** 적용된다. 옷에 가려 TOO_SMALL 로 죽던 부위가
    #     살아난다. 대신 헐렁한 옷 실루엣도 유효 부위가 되므로,
    #     body_part_segment 의 clothing 기여도를 함께 보고 판단할 것.
    #  ⚠️ 저장되는 맵 파일은 병합하지 않는다 — 맵은 추론 결과의 원본이다.
    seg_merge_clothing: bool = True

    # ------------------------------------------------------------------ #
    # 잡 큐 / 워커
    # ------------------------------------------------------------------ #
    job_max_attempts: int = 3
    job_claim_retries: int = 5  # CAS 선점 실패 시 재시도 횟수
    worker_poll_interval_sec: float = 1.0

    #: PROCESSING 인 채로 이 시간이 지나면 워커가 죽은 것으로 보고 회수한다.
    #  ⚠️ **가장 오래 걸리는 잡보다 넉넉히 길어야 한다.** 짧게 잡으면 멀쩡히
    #     돌고 있는 잡을 회수해 같은 일을 두 번 하게 된다(= VLM이면 요금 두 배).
    #     세그는 GPU에서 1초 미만, CPU에서 수십 초다. 루틴 생성이 제일 길다.
    job_stale_after_sec: int = 900  # 15분
    #: 회수 검사 주기. 매 폴링마다 돌리면 쓸데없는 쿼리가 쌓인다.
    job_reclaim_interval_sec: int = 60
    # ⚠️ SEG_WORKER_CONCURRENCY / VLM_WORKER_CONCURRENCY 는 2026-08-14 에 지웠다.
    #    각각 이유가 다르다.
    #    - VLM: 부위별 병렬 호출을 폐기하고 전 부위를 한 번에 진단하게 바뀌면서
    #      "동시에 몇 부위를 부를지"라는 값 자체가 의미를 잃었다. 잡 1개가 전 부위를
    #      처리하므로 조절할 동시성이 없다. (llm-strategy.md §F08)
    #    - SEG: **애초에 배선돼 있지 않았다.** run.py 는 단일 루프라 몇을 넣든
    #      워커는 하나다. 값이 있으면 "3개가 돈다"고 믿게 되므로 미사용보다 나쁘다.
    #    ⚠️ 동시 실행을 실제로 구현한 뒤에 되살릴 것. 그때 참고할 제약:
    #       t3.large 는 GPU 가 없고 메모리 8GB 라 세그 워커 2개면 OOM.

    # ------------------------------------------------------------------ #
    # Sapiens2 (세그멘테이션 워커 전용 — API 프로세스는 로드하지 않는다)
    # ------------------------------------------------------------------ #
    model_dir: str = "models"

    #: 백본 크기. 0.4b | 0.8b | 1b | 5b
    #  ⚠️ 코드는 크기와 무관하다. 바꿔도 재추론만 하면 되고 스키마는 그대로다.
    #     5b는 fp16 가중치만 ~9.5GB라 VRAM 16GB 이상(24GB 권장)이 필요하다.
    #  ⚠️ 기본값은 운영에서 쓰는 5b다. 아무 설정 없이 돌렸을 때 실제 배포와
    #     같은 조합이 되도록.
    sapiens_size: str = "5b"

    #: auto | cuda | cpu.  auto면 CUDA가 있으면 CUDA, 없으면 CPU
    sapiens_device: str = "auto"

    #: auto | float16 | bfloat16 | float32
    #  auto면 CUDA에서 float16, CPU에서 float32.
    #  ⚠️ CPU float16은 대부분 더 느리다. CPU에서는 float32를 쓸 것.
    sapiens_dtype: str = "auto"

    # ⚠️ SAPIENS_OFFLOAD / SAPIENS_GPU_MAX_GIB 는 2026-08-14 에 뺐다.
    #    VRAM 보다 큰 모델을 CPU 로 흘려보내는 설정이었는데, 우리 두 환경 어디서도
    #    타지 않는다 — RunPod 는 24GB 라 다 올라가고, 로컬은 CPU 라 조건(cuda)에
    #    걸리지 않는다. 한 번도 실행되지 않는 분기를 모델 로딩 경로에 두고 있었다.
    #    docs/removed-code.md 참고.

    #: 라벨 목록이 확인되지 않은 모델이면 워커 기동을 거부할지.
    #  ⚠️ false로 두면 클래스 순서가 다른 모델도 그냥 돈다. 부위가 통째로
    #     어긋난 결과가 저장되고 에러는 안 난다. 운영에서는 켜둘 것.
    sapiens_strict_labels: bool = True

    # ------------------------------------------------------------------ #
    # VLM provider (담당 B 영역)
    # ------------------------------------------------------------------ #
    vlm_provider: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # ------------------------------------------------------------------ #
    # ExerciseDB (운동 카탈로그, 담당 B 영역)
    # ------------------------------------------------------------------ #
    #: RapidAPI 키. 운동 풀 배치 수집(scripts/fetch_exercisedb.py)에만 쓴다.
    #  ⚠️ 런타임에는 필요 없다 — 수집한 카탈로그를 로컬 캐시에서 읽으므로
    #     API 프로세스와 워커는 이 키 없이 돈다. 외부 API 장애가 서비스 장애가
    #     되지 않게 하려는 설계다 (docs/routine-logic-decision.md D6).
    rapidapi_key: str = ""
    rapidapi_exercisedb_host: str = "edb-with-videos-and-images-by-ascendapi.p.rapidapi.com"

    # ------------------------------------------------------------------ #
    # 개발 모드
    # ------------------------------------------------------------------ #
    use_mock: bool = False


settings = Settings()


def _warn_unknown_env_keys() -> None:
    """.env 에 있지만 Settings 가 모르는 키를 경고한다.

    ⚠️ extra="ignore" 라서 오타나 옛 이름은 **아무 말 없이 무시된다.**
       실제로 SAPIENS_REQUIRE_VERIFIED_LABELS(→ SAPIENS_STRICT_LABELS 로 개명)가
       .env 에 남은 채 한동안 아무 일도 안 하고 있었다. 설정한 사람은 켜둔 줄 안다.
       막지는 않는다(다른 도구가 같은 .env 를 쓸 수 있다) — 보이게만 한다.
    """
    import logging
    import pathlib
    import re

    path = pathlib.Path(Settings.model_config["env_file"])
    if not path.is_file():
        return

    known = set(Settings.model_fields)
    unknown = [
        m.group(1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if (m := re.match(r"^([A-Z_][A-Z0-9_]*)=", line.strip()))
        and m.group(1).lower() not in known
    ]
    if unknown:
        logging.getLogger("config").warning(
            ".env 에 모르는 키가 있습니다 (무시됨): %s", ", ".join(unknown)
        )


_warn_unknown_env_keys()
