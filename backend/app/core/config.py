from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "sqlite:///data/dealguard.db"
    frontend_origin: str = "http://localhost:3000"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    embedding_model: str = ""

    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")


settings = Settings()
