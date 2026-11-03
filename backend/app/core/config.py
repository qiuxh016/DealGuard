from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "sqlite:///data/dealguard.db"
    frontend_origin: str = "http://localhost:3000"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    auth_login_max_attempts: int = 5
    auth_login_window_seconds: int = 300

    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")


settings = Settings()
