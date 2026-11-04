from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    enable_demo_auth: bool = False
    action_planner_enabled: bool = True
    database_url: str = "sqlite:///data/dealguard.db"
    frontend_origin: str = "http://localhost:3001"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    local_embedding_model_path: str = ""
    auth_login_max_attempts: int = 5
    auth_login_window_seconds: int = 300
    auth_register_max_attempts: int = 5

    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")


settings = Settings()
