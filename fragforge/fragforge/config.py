from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FRAGFORGE_", env_file=".env", extra="ignore")

    data_dir: Path = Path("./data")
    database_url: str | None = None  # default: sqlite file under data_dir
    storage_backend: str = "local"  # "local" now; "s3" planned (see storage.py)
    max_workers: int = Field(2, ge=1)
    candidate_padding_before: float = 8.0
    candidate_padding_after: float = 12.0
    max_upload_gb: float = 20.0
    max_duration_hours: float = 6.0
    use_nvenc: bool = False
    # Detection tuning
    min_candidate_score: float = 0.35
    max_candidates_per_hour: int = 40

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.data_dir / 'fragforge.db').resolve().as_posix()}"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
