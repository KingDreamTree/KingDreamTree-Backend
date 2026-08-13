from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Supabase ---
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    # --- VLM provider (미확정 — 확정 후 api key 추가) ---
    # 지원 값: "claude" | "openai" | ""(미설정, USE_MOCK=true일 때만 허용)
    vlm_provider: str = ""
    anthropic_api_key: str = ""  # vlm_provider=claude 일 때 사용
    openai_api_key: str = ""  # vlm_provider=openai 일 때 사용 (TODO)

    # --- Sapiens2 모델 경로 ---
    # docker-compose에서는 ./models:/app/models 볼륨으로 주입
    # 로컬에서는 models/ 디렉토리에 가중치를 직접 배치
    model_dir: str = "models"

    # --- 개발 모드 ---
    use_mock: bool = False


settings = Settings()
