"""MinerU-backed processor for local document parsing."""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import logging
import os
import shutil
import socket
import tempfile
import zipfile
from pathlib import Path
from typing import ClassVar

import aiohttp

from aily.chaos.processors.base import ContentProcessor
from aily.chaos.processors.document import TextProcessor
from aily.chaos.types import ExtractedContentMultimodal

logger = logging.getLogger(__name__)


class _MinerULocalAPIService:
    """Shared local mineru-api lifecycle manager."""

    _instance: ClassVar["_MinerULocalAPIService | None"] = None

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._base_url: str | None = None
        self._command_signature: tuple[str, ...] | None = None
        self._atexit_registered = False

    @classmethod
    def shared(cls) -> "_MinerULocalAPIService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def ensure_started(
        self,
        *,
        command: str,
        startup_timeout_seconds: int,
        enable_vlm_preload: bool,
        model_source: str,
    ) -> str | None:
        async with self._lock:
            if await self._is_healthy():
                return self._base_url

            await self._stop_locked()

            port = self._find_free_port()
            self._base_url = f"http://127.0.0.1:{port}"
            self._command_signature = (
                command,
                str(startup_timeout_seconds),
                str(enable_vlm_preload),
                model_source,
            )

            env = os.environ.copy()
            if model_source:
                env.setdefault("MINERU_MODEL_SOURCE", model_source)

            cmd = [
                command,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ]
            if enable_vlm_preload:
                cmd.extend(["--enable-vlm-preload", "true"])

            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
            )

            if not self._atexit_registered:
                atexit.register(self.stop_sync)
                self._atexit_registered = True

            deadline = asyncio.get_running_loop().time() + startup_timeout_seconds
            while asyncio.get_running_loop().time() < deadline:
                if self._process.returncode is not None:
                    logger.warning("Local mineru-api exited early with code %s", self._process.returncode)
                    await self._stop_locked()
                    return None
                if await self._is_healthy():
                    return self._base_url
                await asyncio.sleep(1)

            logger.warning("Timed out waiting for local mineru-api to become healthy at %s", self._base_url)
            await self._stop_locked()
            return None

    async def _is_healthy(self) -> bool:
        if self._process is None or self._base_url is None:
            return False
        if self._process.returncode is not None:
            return False

        try:
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self._base_url}/health") as response:
                    return response.status == 200
        except Exception:
            return False

    def stop_sync(self) -> None:
        process = self._process
        self._process = None
        self._base_url = None
        if process is None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            return

    async def _stop_locked(self) -> None:
        process = self._process
        self._process = None
        self._base_url = None
        if process is None:
            return
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

    @staticmethod
    def _find_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            return int(sock.getsockname()[1])


class MinerUProcessor(ContentProcessor):
    """Process supported documents with a local MinerU installation."""

    SUPPORTED_SUFFIXES = {
        ".pdf",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
        ".xls",
        ".xlsx",
    }

    async def process(self, file_path: Path) -> ExtractedContentMultimodal | None:
        """Parse a document with MinerU and normalize its markdown output."""
        if not self.can_process(file_path):
            return None

        output_dir = self._output_dir_for(file_path)
        output_dir.mkdir(parents=True, exist_ok=True)

        markdown_path = self._find_full_markdown(output_dir)
        if markdown_path is None:
            base_url = await self._ensure_api_base_url()
            if base_url:
                success = await self._invoke_mineru_api(base_url, file_path, output_dir)
                if not success:
                    logger.warning("mineru-api parse failed for %s; trying CLI fallback", file_path.name)
            else:
                success = False

            if not success:
                command_path = self._resolve_command()
                if not command_path:
                    logger.info(
                        "MinerU command %r not found; falling back for %s",
                        self.config.mineru.command,
                        file_path.name,
                    )
                    return None
                success = await self._invoke_mineru_cli(command_path, file_path, output_dir)
                if not success:
                    return None
            markdown_path = self._find_full_markdown(output_dir)

        if markdown_path is None:
            logger.warning("MinerU completed for %s but markdown was not found in %s", file_path.name, output_dir)
            return None

        text_processor = TextProcessor(self.config)
        result = await text_processor.process(markdown_path)
        if result is None:
            return None

        result.source_path = file_path
        result.source_type = self._source_type_for_extension(file_path.suffix.lower())
        result.processing_method = f"mineru:{self.config.mineru.backend}"
        result.metadata = {
            **result.metadata,
            "source_paths": [str(file_path)],
            "mineru_backend": self.config.mineru.backend,
            "mineru_method": self.config.mineru.method,
            "mineru_model_source": self.config.mineru.model_source,
            "mineru_markdown_path": str(markdown_path),
            "mineru_output_dir": str(markdown_path.parent),
            "mineru_cache_dir": str(output_dir),
        }
        result.tags = list(dict.fromkeys([*result.tags, "mineru"]))
        return result

    def can_process(self, file_path: Path) -> bool:
        """Return True when MinerU should handle this file."""
        if not getattr(self.config, "mineru", None) or not self.config.mineru.enabled:
            return False
        return file_path.suffix.lower() in self.SUPPORTED_SUFFIXES

    async def _ensure_api_base_url(self) -> str | None:
        """Return an existing or reusable local mineru-api base URL."""
        if self.config.mineru.api_url:
            return self.config.mineru.api_url.rstrip("/")

        api_command = self._resolve_api_command()
        if not api_command:
            return None

        service = _MinerULocalAPIService.shared()
        return await service.ensure_started(
            command=api_command,
            startup_timeout_seconds=self.config.mineru.api_startup_timeout_seconds,
            enable_vlm_preload=self.config.mineru.api_enable_vlm_preload,
            model_source=self.config.mineru.model_source,
        )

    def _resolve_command(self) -> str | None:
        """Resolve MinerU CLI from PATH or an explicit config path."""
        configured = self.config.mineru.command
        if Path(configured).expanduser().exists():
            return str(Path(configured).expanduser())
        return shutil.which(configured)

    def _resolve_api_command(self) -> str | None:
        """Resolve MinerU API CLI from PATH or an explicit config path."""
        configured = self.config.mineru.api_command
        if Path(configured).expanduser().exists():
            return str(Path(configured).expanduser())
        return shutil.which(configured)

    def _output_dir_for(self, file_path: Path) -> Path:
        """Create a stable cache directory keyed by file path and mtime."""
        stat = file_path.stat()
        cache_key = hashlib.sha1(
            f"{file_path.resolve()}::{stat.st_size}::{stat.st_mtime_ns}".encode("utf-8")
        ).hexdigest()[:12]
        return self.config.processed_folder / ".mineru_cache" / f"{file_path.stem}_{cache_key}"

    def _build_command(self, command_path: str, file_path: Path, output_dir: Path) -> list[str]:
        """Build the MinerU CLI invocation."""
        args = [
            command_path,
            "-p",
            str(file_path),
            "-o",
            str(output_dir),
            "-b",
            self.config.mineru.backend,
            "-m",
            self.config.mineru.method,
            "-l",
            self.config.mineru.language,
            "-f",
            str(self.config.mineru.enable_formula).lower(),
            "-t",
            str(self.config.mineru.enable_table).lower(),
        ]
        if self.config.mineru.api_url:
            args.extend(["--api-url", self.config.mineru.api_url])
        if self.config.mineru.model_source:
            args.extend(["--source", self.config.mineru.model_source])
        return args

    async def _invoke_mineru_cli(self, command_path: str, file_path: Path, output_dir: Path) -> bool:
        """Run MinerU CLI directly and return whether parsing succeeded."""
        cmd = self._build_command(command_path, file_path, output_dir)
        logger.info("Processing %s with MinerU CLI backend=%s", file_path.name, self.config.mineru.backend)

        env = os.environ.copy()
        if self.config.mineru.model_source:
            env.setdefault("MINERU_MODEL_SOURCE", self.config.mineru.model_source)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                "MinerU failed for %s: exit=%s stdout=%s stderr=%s",
                file_path.name,
                proc.returncode,
                stdout.decode("utf-8", errors="ignore"),
                stderr.decode("utf-8", errors="ignore"),
            )
            return False
        return True

    async def _invoke_mineru_api(self, base_url: str, file_path: Path, output_dir: Path) -> bool:
        """Call mineru-api synchronously and extract its ZIP payload into output_dir."""
        logger.info("Processing %s with mineru-api backend=%s via %s", file_path.name, self.config.mineru.backend, base_url)

        form = aiohttp.FormData()
        form.add_field("files", file_path.read_bytes(), filename=file_path.name, content_type="application/octet-stream")
        form.add_field("lang_list", self.config.mineru.language)
        form.add_field("backend", self.config.mineru.backend)
        form.add_field("parse_method", self.config.mineru.method)
        form.add_field("formula_enable", str(self.config.mineru.enable_formula).lower())
        form.add_field("table_enable", str(self.config.mineru.enable_table).lower())
        form.add_field("return_md", "true")
        form.add_field("return_middle_json", "true")
        form.add_field("return_model_output", "true")
        form.add_field("return_content_list", "true")
        form.add_field("return_images", "true")
        form.add_field("response_format_zip", "true")
        form.add_field("return_original_file", "false")
        form.add_field("start_page_id", "0")
        form.add_field("end_page_id", "99999")

        timeout = aiohttp.ClientTimeout(total=max(300, self.config.llm_timeout))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(f"{base_url}/file_parse", data=form) as response:
                    if response.status != 200:
                        detail = await response.text()
                        logger.warning("mineru-api failed for %s: %s %s", file_path.name, response.status, detail[:800])
                        return False
                    zip_bytes = await response.read()
        except Exception as exc:
            logger.warning("mineru-api request failed for %s: %s", file_path.name, exc)
            return False

        self._extract_result_zip(zip_bytes, output_dir)
        return True

    def _extract_result_zip(self, zip_bytes: bytes, output_dir: Path) -> None:
        """Safely unpack mineru-api ZIP results."""
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp_file:
            temp_file.write(zip_bytes)
            temp_zip = Path(temp_file.name)

        try:
            with zipfile.ZipFile(temp_zip, "r") as zip_file:
                for member in zip_file.infolist():
                    member_path = Path(*Path(member.filename).parts)
                    if member.is_dir():
                        (output_dir / member_path).mkdir(parents=True, exist_ok=True)
                        continue
                    target_path = (output_dir / member_path).resolve()
                    if output_dir.resolve() not in target_path.parents and target_path != output_dir.resolve():
                        raise ValueError(f"Unsafe ZIP entry from MinerU: {member.filename}")
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with zip_file.open(member, "r") as source, open(target_path, "wb") as handle:
                        handle.write(source.read())
        finally:
            temp_zip.unlink(missing_ok=True)

    def _find_full_markdown(self, output_dir: Path) -> Path | None:
        """Locate the primary MinerU markdown output."""
        candidates = [
            path
            for path in output_dir.rglob("*.md")
            if path.name.lower() == "full.md" or not path.name.startswith(".")
        ]
        candidates = [
            path
            for path in candidates
            if path.parent.name in {"auto", "ocr", "txt"} or path.name.lower() == "full.md"
        ]
        candidates.sort(
            key=lambda path: (
                0 if path.name.lower() == "full.md" else 1,
                len(path.parts),
                str(path),
            )
        )
        return candidates[0] if candidates else None

    def _source_type_for_extension(self, ext: str) -> str:
        """Map original extension to the logical Chaos source type."""
        mapping = {
            ".pdf": "pdf",
            ".doc": "document",
            ".docx": "document",
            ".ppt": "presentation",
            ".pptx": "presentation",
            ".xls": "spreadsheet",
            ".xlsx": "spreadsheet",
        }
        return mapping.get(ext, "document")
