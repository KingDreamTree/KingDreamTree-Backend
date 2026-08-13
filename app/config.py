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
    # 구 anon key. 프론트가 Supabase에 직접 붙지 않으므로 서버에서는 쓰지 않는다.
    supabase_anon_key: str = ""
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
    pose_threshold: float = 90.0  # THRESHOLD: 최종 유사도 하한 (%)
    framing_f_min: float = 0.80  # F_MIN: 프레이밍 Jaccard 하한
    pose_tol_deg: float = 40.0  # TOL: 관절 각도 허용 오차 (도)
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
    # ⚠️ t3.large는 GPU가 없고 메모리 8GB다. 세그 워커를 2개 이상 돌리면 OOM.
    seg_worker_concurrency: int = 1
    # vlm_worker_concurrency 는 제거했다 (2026-08-14).
    #   부위별 병렬 호출을 폐기하고 전 부위를 한 번에 진단하게 바뀌면서
    #   "동시에 몇 부위를 부를지"라는 값 자체가 의미를 잃었다. 잡 1개가 전 부위를
    #   처리하므로 조절할 동시성이 없다. 안 쓰는 값을 남겨두면 다음 사람이
    #   "3으로 올리면 빨라지나"를 시도하게 된다. (llm-strategy.md §F08)

    # ------------------------------------------------------------------ #
    # Sapiens2 (세그멘테이션 워커 전용 — API 프로세스는 로드하지 않는다)
    # ------------------------------------------------------------------ #
    model_dir: str = "models"

    #: 백본 크기. 0.4b | 0.8b | 1b | 5b
    #  ⚠️ 코드는 크기와 무관하다. 바꿔도 재추론만 하면 되고 스키마는 그대로다.
    #     5b는 fp16 가중치만 ~9.5GB라 VRAM 16GB 이상(24GB 권장)이 필요하다.
    #  ⚠️ 기본값을 5b로 둔 이유 — 라벨 매핑을 이 크기로만 실측 검증했다
    #     (sapiens_labels.VERIFIED_WITH). 기본값이 미검증 크기면 아무 설정 없이
    #     돌렸을 때 검증 안 된 조합으로 도는 셈이 된다.
    sapiens_size: str = "5b"

    #: auto | cuda | cpu.  auto면 CUDA가 있으면 CUDA, 없으면 CPU
    sapiens_device: str = "auto"

    #: auto | float16 | bfloat16 | float32
    #  auto면 CUDA에서 float16, CPU에서 float32.
    #  ⚠️ CPU float16은 대부분 더 느리다. CPU에서는 float32를 쓸 것.
    sapiens_dtype: str = "auto"

    #: VRAM이 부족할 때 CPU로 레이어를 흘려보낼지 (accelerate device_map).
    #  ⚠️ 켜면 VRAM보다 큰 모델도 돌아가지만 레이어가 CPU↔GPU를 오가 느려진다.
    #     8GB VRAM에서 5b(fp16 ~9.5GB)를 보려는 경우가 이에 해당한다.
    #     운영(RunPod 24GB)에서는 꺼둘 것 — 켜져 있어도 다 올라가면 성능 손해는 없다.
    sapiens_offload: bool = False

    #: 오프로딩 시 GPU에 최대 몇 GiB까지 올릴지. 0이면 자동(전체의 90%).
    #  ⚠️ 전부 다 쓰면 활성값 자리가 없어 OOM이 난다. 여유를 남겨야 한다.
    sapiens_gpu_max_gib: float = 0

    #: 라벨 맵 검증 — label_map의 클래스명이 body_part 마스터에 다 있어야 통과.
    #  ⚠️ false로 두면 인덱스 매핑이 틀려도 조용히 진행된다. 운영에서는 켜둘 것.
    sapiens_require_verified_labels: bool = True

    # ------------------------------------------------------------------ #
    # VLM provider (담당 B 영역)
    # ------------------------------------------------------------------ #
    vlm_provider: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # ------------------------------------------------------------------ #
    # 개발 모드
    # ------------------------------------------------------------------ #
    use_mock: bool = False


settings = Settings()
