from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load once for both typed application settings and AWS/OpenTelemetry SDKs.
load_dotenv()


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(extra="ignore")

    neo4j_uri: str = ""
    neo4j_username: str = ""
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"

    aws_s3_bucket: str = ""
    aws_region: str = ""

    opensearch_endpoint: str = ""
    opensearch_index: str = "content-search"
    opensearch_catalog_index: str = "catalog-search"
    opensearch_auth_mode: str = "none"
    opensearch_username: str = ""
    opensearch_password: str = ""

    environment: str = ""
    otel_enabled: bool = False
    otel_service_name: str = "openpecha-api"
    otel_exporter_otlp_endpoint: str = ""


settings = Settings()
