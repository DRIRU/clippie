import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MediaAsset(Base):
    __tablename__ = "media_assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(300))
    source_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|ready|failed
    game: Mapped[str] = mapped_column(String(50), default="generic")  # manual override later
    profile_config: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. {"player_name": "...", "auto_captions": true}
    caption_style: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # see captions.CaptionStyle
    layout: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # vertical layout, see layout.Layout
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # ffprobe summary
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    candidates = relationship("Candidate", back_populates="asset", cascade="all, delete-orphan")


class Job(Base):
    __tablename__ = "processing_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("media_assets.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(30))  # import|ingest|analyze|export
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|done|failed
    stage: Mapped[str] = mapped_column(String(50), default="")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class GameEvent(Base):
    __tablename__ = "game_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id"), index=True)
    game: Mapped[str] = mapped_column(String(50), default="generic")
    event_type: Mapped[str] = mapped_column(String(50))
    timestamp: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(50))
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class Candidate(Base):
    __tablename__ = "clip_candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id"), index=True)
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    peak: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    event_types: Mapped[list] = mapped_column(JSON, default=list)
    verification: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # VLM verdict, see vlm.py
    preview_key: Mapped[str | None] = mapped_column(String(500), nullable=True)  # rendered, trimmed clip
    captions: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {"words": [...], "language": ..., "model": ...}
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected
    manual: Mapped[bool] = mapped_column(Boolean, default=False)

    asset = relationship("MediaAsset", back_populates="candidates")


class Export(Base):
    __tablename__ = "exports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("clip_candidates.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # original|vertical
    status: Mapped[str] = mapped_column(String(20), default="queued")
    key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    subtitles: Mapped[bool] = mapped_column(Boolean, default=False)  # subtitles burned into this export
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PerfRun(Base):
    """One server start: the settings and hardware the timings were taken under (for comparing later)."""
    __tablename__ = "perf_runs"
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    host: Mapped[dict] = mapped_column(JSON, default=dict)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)   # your name for this run, e.g. "6 workers"
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class PerfEvent(Base):
    """One timed stage. Deliberately has no foreign keys, so deleting a video keeps its measurements."""
    __tablename__ = "perf_events"
    __table_args__ = (Index("ix_perf_events_stage_ts", "stage", "ts"),)   # trend queries: one stage over time
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    stage: Mapped[str] = mapped_column(String(60), index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    seconds: Mapped[float] = mapped_column(Float)
    media_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)  # video/clip length processed
    status: Mapped[str] = mapped_column(String(12), default="ok")              # ok | error | cancelled
    concurrent: Mapped[int] = mapped_column(Integer, default=0)                # jobs running at that moment
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
