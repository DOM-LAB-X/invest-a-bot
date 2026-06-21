from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql+psycopg://invest:invest@localhost:5433/invest_a_bot"
    redis_url: str = "redis://localhost:6379/0"

    house_clerk_base_url: str = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs"
    house_clerk_poll_interval_seconds: int = 90

    pdf_storage_dir: str = "./data/filings"

    notifications_enabled: bool = True
    resend_api_key: str | None = None
    notification_from_email: str | None = None
    notification_to_emails: list[str] = []
    slack_webhook_url: str | None = None

    daily_digest_enabled: bool = True
    digest_timezone: str = "America/New_York"
    digest_top_n: int = 10

    # Off by default: Senate eFD's access terms (5 U.S.C. app. SS 105(c)) restrict use
    # to non-commercial purposes (or news media). Personal use is fine, but this stays
    # an explicit opt-in rather than a silent default.
    senate_ingestion_enabled: bool = False


settings = Settings()
