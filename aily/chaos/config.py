"""Configuration for Aily Chaos."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class VideoConfig:
    """Video processing configuration."""

    extract_frames_every_n_seconds: int = 5
    whisper_model: str = "base"  # tiny, base, small, medium, large
    max_frames_per_video: int = 20
    enable_visual_analysis: bool = True
    resize_frames_to: int = 2000  # Max dimension for LLM
    supported_formats: list[str] = field(
        default_factory=lambda: ["mp4", "avi", "mkv", "mov", "webm"]
    )


@dataclass
class ImageConfig:
    """Image processing configuration."""

    ocr_enabled: bool = True
    ocr_languages: list[str] = field(default_factory=lambda: ["en", "ch_sim"])
    visual_analysis: bool = True
    max_image_size: int = 2000  # Max dimension for LLM
    supported_formats: list[str] = field(
        default_factory=lambda: ["png", "jpg", "jpeg", "gif", "webp", "bmp"]
    )


@dataclass
class PDFConfig:
    """PDF processing configuration."""

    extract_layout: bool = True
    extract_tables: bool = True
    ocr_enabled: bool = True
    visual_analysis: bool = True
    max_pages_for_visual_analysis: int = 10
    supported_formats: list[str] = field(default_factory=lambda: ["pdf"])


@dataclass
class PPTXConfig:
    """PowerPoint processing configuration."""

    extract_speaker_notes: bool = True
    extract_slide_images: bool = True
    visual_analysis: bool = True
    supported_formats: list[str] = field(default_factory=lambda: ["pptx"])


@dataclass
class MinerUConfig:
    """MinerU local parser configuration."""

    enabled: bool = True
    command: str = "mineru"
    api_command: str = "mineru-api"
    backend: str = "pipeline"
    method: str = "auto"
    language: str = "ch"
    model_source: str = "modelscope"
    api_url: str = ""
    api_startup_timeout_seconds: int = 180
    api_enable_vlm_preload: bool = False
    enable_formula: bool = True
    enable_table: bool = True
    persist_outputs: bool = True


@dataclass
class TaggingConfig:
    """Tagging engine configuration."""

    content_based: bool = True
    llm_based: bool = True
    knowledge_graph_based: bool = False  # Phase 3
    auto_domain_classification: bool = True
    max_tags: int = 20


@dataclass
class DikiwiConfig:
    """DIKIWI integration configuration."""

    zettelkasten_only: bool = True
    generate_visual_element_zettels: bool = True
    generate_transcript_zettels: bool = False  # Include transcript in main zettel
    min_content_length: int = 100  # Minimum characters to process


@dataclass
class ChaosConfig:
    """Main configuration for Aily Chaos."""

    # Folders
    watch_folder: Path = field(default_factory=lambda: Path.home() / "aily_chaos")
    processed_folder: Path | None = None
    failed_folder: Path | None = None

    # Processing
    debounce_seconds: float = 5.0
    max_file_size_mb: int = 500
    max_concurrent_jobs: int = 3

    # Feature modules
    video: VideoConfig = field(default_factory=VideoConfig)
    image: ImageConfig = field(default_factory=ImageConfig)
    pdf: PDFConfig = field(default_factory=PDFConfig)
    pptx: PPTXConfig = field(default_factory=PPTXConfig)
    mineru: MinerUConfig = field(default_factory=MinerUConfig)
    tagging: TaggingConfig = field(default_factory=TaggingConfig)
    dikiwi: DikiwiConfig = field(default_factory=DikiwiConfig)

    # LLM
    llm_model: str = "glm-5.1"  # For tagging and analysis
    llm_timeout: int = 120

    def __post_init__(self):
        """Set up derived paths."""
        if self.processed_folder is None:
            self.processed_folder = self.watch_folder / ".processed"
        if self.failed_folder is None:
            self.failed_folder = self.watch_folder / ".failed"

        # Ensure paths are Path objects
        self.watch_folder = Path(self.watch_folder)
        self.processed_folder = Path(self.processed_folder)
        self.failed_folder = Path(self.failed_folder)

    @classmethod
    def from_file(cls, path: Path) -> "ChaosConfig":
        """Load configuration from YAML file."""
        with open(path, "r") as f:
            data = yaml.safe_load(f)

        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChaosConfig":
        """Create from dictionary."""
        # Handle nested config objects
        video_config = VideoConfig(**data.get("video", {}))
        image_config = ImageConfig(**data.get("image", {}))
        pdf_config = PDFConfig(**data.get("pdf", {}))
        pptx_config = PPTXConfig(**data.get("pptx", {}))
        mineru_config = MinerUConfig(**data.get("mineru", {}))
        tagging_config = TaggingConfig(**data.get("tagging", {}))
        dikiwi_config = DikiwiConfig(**data.get("dikiwi", {}))

        return cls(
            watch_folder=Path(data.get("watch_folder", "~/aily_chaos")),
            processed_folder=Path(data.get("processed_folder", "~"))
            if "processed_folder" in data
            else None,
            failed_folder=Path(data.get("failed_folder", "~"))
            if "failed_folder" in data
            else None,
            debounce_seconds=data.get("debounce_seconds", 5.0),
            max_file_size_mb=data.get("max_file_size_mb", 500),
            max_concurrent_jobs=data.get("max_concurrent_jobs", 3),
            video=video_config,
            image=image_config,
            pdf=pdf_config,
            pptx=pptx_config,
            mineru=mineru_config,
            tagging=tagging_config,
            dikiwi=dikiwi_config,
            llm_model=data.get("llm_model", "glm-5.1"),
            llm_timeout=data.get("llm_timeout", 120),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "watch_folder": str(self.watch_folder),
            "processed_folder": str(self.processed_folder),
            "failed_folder": str(self.failed_folder),
            "debounce_seconds": self.debounce_seconds,
            "max_file_size_mb": self.max_file_size_mb,
            "max_concurrent_jobs": self.max_concurrent_jobs,
            "video": self.video.__dict__,
            "image": self.image.__dict__,
            "pdf": self.pdf.__dict__,
            "pptx": self.pptx.__dict__,
            "mineru": self.mineru.__dict__,
            "tagging": self.tagging.__dict__,
            "dikiwi": self.dikiwi.__dict__,
            "llm_model": self.llm_model,
            "llm_timeout": self.llm_timeout,
        }

    def save(self, path: Path) -> None:
        """Save configuration to YAML file."""
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False)
