from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLIPPIE_", env_file=".env", extra="ignore")

    data_dir: Path = Path("./data")
    database_url: str | None = None  # default: sqlite file under data_dir
    storage_backend: str = "local"  # "local" now; "s3" planned (see storage.py)
    max_workers: int = Field(2, ge=1)
    candidate_padding_before: float = 8.0
    candidate_padding_after: float = 12.0
    max_upload_gb: float = 20.0
    max_duration_hours: float = 6.0
    use_nvenc: bool = False
    # Vision-language verification (existing OpenAI-compatible vLLM endpoint). Disabled when unset.
    vlm_base_url: str | None = None   # e.g. http://127.0.0.1:8000/v1
    vlm_model: str | None = None      # default: first model reported by GET /models
    vlm_api_key: str = "EMPTY"
    vlm_frames: int = 6               # frames sampled per candidate
    vlm_max_images: int = 4           # images per request; 1 => frames are tiled into one contact sheet
    vlm_timeout: float = 120.0
    auto_verify: bool = True          # chain verification after analysis when the VLM is configured
    # Subtitles (local faster-whisper). The model is downloaded on first use.
    whisper_model: str = "base"       # tiny | base | small | medium ...  (".en" variants are English-only)
    whisper_device: str = "auto"      # auto | cpu | cuda
    whisper_compute_type: str = "auto"
    whisper_language: str | None = None   # None = detect automatically
    auto_captions: bool = False       # transcribe every new video without being asked
    # Detection tuning
    min_candidate_score: float = 0.35
    max_candidates_per_hour: int = 40

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.data_dir / 'clippie.db').resolve().as_posix()}"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
