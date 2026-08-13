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
    map_max_side: int = 1024  # MAP_MAX_SIDE: 라벨 맵 긴 변 상한
    min_comparable_parts: int = 3  # 비교 가능 부위가 이보다 적으면 재촬영 안내

    # ------------------------------------------------------------------ #
    # 잡 큐 / 워커
    # ------------------------------------------------------------------ #
    job_max_attempts: int = 3
    job_claim_retries: int = 5  # CAS 선점 실패 시 재시도 횟수
    worker_poll_interval_sec: float = 1.0
    # ⚠️ t3.large는 GPU가 없고 메모리 8GB다. 세그 워커를 2개 이상 돌리면 OOM.
    seg_worker_concurrency: int = 1
    vlm_worker_concurrency: int = 3

    # ------------------------------------------------------------------ #
    # 모델 (세그멘테이션 워커 전용 — API 프로세스는 로드하지 않는다)
    # ------------------------------------------------------------------ #
    model_dir: str = "models"

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
