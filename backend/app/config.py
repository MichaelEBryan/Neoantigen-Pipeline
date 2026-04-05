from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env")

    # Database and cache
    database_url: str = "postgresql+asyncpg://cvdash:devpassword@db:5432/cvdash"
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"

    # Security
    secret_key: str = "dev-secret-key-change-in-production"
    allowed_origins: str = "http://localhost:3000,http://localhost:8000"

    # File handling
    upload_dir: str = "/app/uploads"

    # Compute backend selection: "gcp-batch" or "isambard"
    compute_backend: str = "gcp-batch"

    # --- GCP Batch settings ---
    gcp_project_id: str = ""
    gcp_region: str = "europe-west2"        # London -- change if bucket is elsewhere
    gcp_pipeline_bucket: str = ""           # e.g. "neoantigen_interface"
    gcp_service_account: str = ""           # SA email for Batch jobs
    gcp_pipeline_image: str = ""            # e.g. "europe-west2-docker.pkg.dev/{PROJECT}/cvdash/pipeline:latest"
    gcp_machine_type: str = "n2-standard-16"
    gcp_boot_disk_gb: int = 200
    gcp_nextflow_profile: str = "docker"

    # --- Isambard HPC settings ---
    isambard_host: str = "login.isambard.ac.uk"
    isambard_user: str = ""
    isambard_key_path: str = "/app/ssh_keys/isambard"
    isambard_project_dir: str = "/scratch/projects/cvdash"
    isambard_container_dir: str = "/projects/cvdash/containers"
    isambard_nextflow_path: str = "/projects/cvdash/nextflow"
    isambard_partition: str = "cpu"
    isambard_account: str = ""

    # Rate limiting
    rate_limit_rpm: int = 60
    rate_limit_burst: int = 10
    login_rate_limit_rpm: int = 5

    # Logging
    log_level: str = "INFO"

    # Environment
    environment: str = "development"  # development | staging | production

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_allowed_origins(cls, v: str) -> str:
        """Parse comma-separated allowed origins (kept as string for later use)."""
        if isinstance(v, str):
            return v
        return str(v)

    def get_allowed_origins_list(self) -> list[str]:
        """Get parsed list of allowed origins."""
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def validate_config(self) -> "Settings":
        """Validate configuration values."""
        # Warn if secret_key is default in non-development
        if (
            self.environment != "development"
            and self.secret_key == "dev-secret-key-change-in-production"
        ):
            import warnings
            warnings.warn(
                f"secret_key is using default value in {self.environment} environment. "
                "This is a security risk. Set SECRET_KEY environment variable.",
                stacklevel=2,
            )

        # Validate database URL
        if not self.database_url.startswith("postgresql"):
            raise ValueError(
                f"Invalid database_url: must start with 'postgresql'. Got: {self.database_url}"
            )

        # Validate redis URL
        if not self.redis_url.startswith("redis://"):
            raise ValueError(
                f"Invalid redis_url: must start with 'redis://'. Got: {self.redis_url}"
            )

        return self


settings = Settings()
