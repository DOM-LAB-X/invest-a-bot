from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql+psycopg://invest:invest@localhost:5433/invest_a_bot"
    redis_url: str = "redis://localhost:6379/0"

    house_clerk_base_url: str = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs"
    house_clerk_poll_interval_seconds: int = 90

    pdf_storage_dir: str = "./data/filings"


settings = Settings()
