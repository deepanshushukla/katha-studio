"""Database models (SQLite via SQLModel)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, Session, SQLModel, create_engine, select

from .config import DB_FILE

engine = create_engine(f"sqlite:///{DB_FILE}", connect_args={"check_same_thread": False})


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = "Untitled story"
    story: str = ""
    language: str = "hi"                 # hi | en
    style_key: str = "cinematic"
    style_custom: str = ""
    target_seconds: int = 60
    seed: int = 0
    characters_json: str = "[]"          # [{name, description}]
    # voice
    tts_provider: str = ""
    voice: str = ""
    rate: int = 0                        # percent, -50..50
    pitch: int = 0                       # Hz, -20..20
    # render options + results
    render_json: str = "{}"
    metadata_json: str = "{}"            # {title, description, hashtags}
    last_render: str = ""
    step: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def characters(self) -> list:
        return json.loads(self.characters_json or "[]")


class Scene(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(index=True)
    position: int = 0
    narration: str = ""
    image_prompt: str = ""
    caption: str = ""
    approved_image_id: Optional[int] = None
    audio_path: str = ""
    audio_original_path: str = ""        # pristine self-recorded take; change-voice always re-processes from this
    audio_duration: float = 0.0
    audio_key: str = ""                  # hash of text+voice settings used for the audio
    audio_self: bool = False             # True if audio_path is a user-recorded take, not TTS
    words_json: str = "[]"               # [{w, start, end}] seconds, relative to scene audio


class ImageVariant(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    scene_id: int = Field(index=True)
    path: str
    prompt: str = ""
    seed: int = 0
    provider: str = ""
    uploaded: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(scene)").fetchall()}
        if "audio_self" not in cols:
            conn.exec_driver_sql("ALTER TABLE scene ADD COLUMN audio_self BOOLEAN DEFAULT 0")
            conn.commit()
        if "audio_original_path" not in cols:
            conn.exec_driver_sql("ALTER TABLE scene ADD COLUMN audio_original_path TEXT DEFAULT ''")
            conn.commit()


def session() -> Session:
    return Session(engine)


def scenes_of(s: Session, project_id: int) -> list[Scene]:
    return list(s.exec(select(Scene).where(Scene.project_id == project_id).order_by(Scene.position)).all())


def variants_of(s: Session, scene_id: int) -> list[ImageVariant]:
    return list(s.exec(select(ImageVariant).where(ImageVariant.scene_id == scene_id).order_by(ImageVariant.id)).all())
