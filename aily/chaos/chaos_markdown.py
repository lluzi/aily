"""Render source-equivalent chaos Markdown for Chaos intake notes."""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from aily.chaos.types import ExtractedContentMultimodal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SlideAsset:
    page_number: int
    vault_relative_path: str
    wikilink: str
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class ChaosMarkdownResult:
    markdown: str
    page_count: int
    screenshot_count: int
    screenshot_renderer: str
    screenshot_error: str = ""


async def render_chaos_markdown(
    *,
    extracted: ExtractedContentMultimodal,
    source_path: Path,
    base_name: str,
    vault_path: Path,
    source_display_name: str = "",
    processed_at: datetime | None = None,
    extra_origin_fields: dict[str, Any] | None = None,
) -> ChaosMarkdownResult:
    """Build the human-facing 00-Chaos note for a source document.

    PDFs are treated as visual source documents. The resulting Markdown keeps a
    one-to-one page/slide structure and embeds a rendered image for every page
    when a local renderer is available.
    """
    source_path = source_path.expanduser().resolve()
    vault_path = vault_path.expanduser().resolve()
    source_display_name = source_display_name or source_path.name
    processed_at = processed_at or datetime.now()

    if source_path.suffix.lower() == ".pdf" or str(extracted.source_type).lower() == "pdf":
        return await _render_pdf_chaos_markdown(
            extracted=extracted,
            source_path=source_path,
            source_display_name=source_display_name,
            base_name=base_name,
            vault_path=vault_path,
            processed_at=processed_at,
            extra_origin_fields=extra_origin_fields or {},
        )

    markdown = _render_generic_chaos_markdown(
        extracted=extracted,
        source_path=source_path,
        source_display_name=source_display_name,
        base_name=base_name,
        processed_at=processed_at,
        extra_origin_fields=extra_origin_fields or {},
    )
    return ChaosMarkdownResult(markdown=markdown, page_count=0, screenshot_count=0, screenshot_renderer="not_applicable")


async def _render_pdf_chaos_markdown(
    *,
    extracted: ExtractedContentMultimodal,
    source_path: Path,
    source_display_name: str,
    base_name: str,
    vault_path: Path,
    processed_at: datetime,
    extra_origin_fields: dict[str, Any],
) -> ChaosMarkdownResult:
    page_texts, page_count = await asyncio.to_thread(_extract_pdf_page_texts, source_path)
    assets, renderer, screenshot_error = await asyncio.to_thread(
        _render_pdf_page_images,
        source_path,
        vault_path,
        base_name,
    )
    assets_by_page = {asset.page_number: asset for asset in assets}
    page_total = max(page_count, len(assets))
    display_title = _display_title(extracted, source_path, page_texts, source_display_name=source_display_name)

    lines = _frontmatter(
        extracted=extracted,
        source_path=source_path,
        source_display_name=source_display_name,
        base_name=base_name,
        processed_at=processed_at,
        extra={
            "source_equivalent": True,
            "page_count": page_total,
            "screenshot_count": len(assets),
            "screenshot_renderer": renderer,
            "screenshot_error": screenshot_error,
            **extra_origin_fields,
        },
    )
    lines.extend(
        [
            f"# {display_title}",
            "",
            "## Source Equivalent",
            "",
            f"- Original File: `{source_display_name}`",
            f"- Stored Object: `{source_path.name}`",
            f"- Source Type: `{extracted.source_type}`",
            f"- Page/Slide Count: `{page_total}`",
            f"- Slide Screenshots: `{len(assets)}`",
            f"- Screenshot Renderer: `{renderer}`",
        ]
    )
    if screenshot_error:
        lines.append(f"- Screenshot Error: `{screenshot_error}`")

    lines.extend(["", "## Slide-by-Slide Content", ""])
    for page_number in range(1, page_total + 1):
        asset = assets_by_page.get(page_number)
        page_text = (page_texts[page_number - 1] if page_number <= len(page_texts) else "").strip()
        lines.extend([f"### Slide {page_number:03d}", ""])
        if asset is not None:
            lines.extend([asset.wikilink, ""])
        else:
            lines.extend(["> Missing rendered slide screenshot. This source note is not source-equivalent until repaired.", ""])
        lines.extend(["#### Slide Text", ""])
        if page_text:
            lines.extend(_markdown_code_block(page_text))
        else:
            lines.append("> No extractable text was found on this slide. Use the screenshot as the visual source.")
        lines.append("")

    parser_markdown = (extracted.get_full_text() or "").strip()
    if parser_markdown:
        lines.extend(["## Parser Markdown", "", parser_markdown, ""])

    return ChaosMarkdownResult(
        markdown="\n".join(lines).rstrip() + "\n",
        page_count=page_total,
        screenshot_count=len(assets),
        screenshot_renderer=renderer,
        screenshot_error=screenshot_error,
    )


def _render_generic_chaos_markdown(
    *,
    extracted: ExtractedContentMultimodal,
    source_path: Path,
    source_display_name: str,
    base_name: str,
    processed_at: datetime,
    extra_origin_fields: dict[str, Any],
) -> str:
    display_title = _display_title(
        extracted,
        source_path,
        [(extracted.get_full_text() or "")],
        source_display_name=source_display_name,
    )
    lines = _frontmatter(
        extracted=extracted,
        source_path=source_path,
        source_display_name=source_display_name,
        base_name=base_name,
        processed_at=processed_at,
        extra={
            "source_equivalent": source_path.suffix.lower() not in {".pdf", ".ppt", ".pptx"},
            **extra_origin_fields,
        },
    )
    lines.extend(
        [
            f"# {display_title}",
            "",
            f"**Original File:** {source_display_name}",
            "",
            f"**Stored Object:** {source_path.name}",
            "",
            f"**Type:** {extracted.source_type}",
            "",
            extracted.get_full_text(),
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _display_title(
    extracted: ExtractedContentMultimodal,
    source_path: Path,
    page_texts: list[str],
    *,
    source_display_name: str = "",
) -> str:
    candidate = str(extracted.title or "").strip()
    display_path = Path(source_display_name) if source_display_name else source_path
    if candidate and not _looks_like_filename_title(candidate, source_path) and not _looks_like_filename_title(candidate, display_path):
        return candidate
    for text in page_texts[:5]:
        title = _first_meaningful_title_line(text, display_path)
        if title:
            return title
    return _humanize_filename(display_path.stem)


def _looks_like_filename_title(candidate: str, source_path: Path) -> bool:
    normalized_candidate = _normalize_title(candidate)
    normalized_stem = _normalize_title(source_path.stem)
    if normalized_candidate == normalized_stem:
        return True
    return bool(re.search(r"\b(pres|paper|user|publish|only|snps)\b", normalized_candidate)) and "-" in candidate


def _first_meaningful_title_line(text: str, source_path: Path | None = None) -> str:
    skip = {"abstract", "agenda", "contents", "introduction", "overview", "synopsys"}
    source_stem = _normalize_title(source_path.stem) if source_path is not None else ""
    best: tuple[int, str] | None = None
    for raw_line in text.splitlines():
        line = " ".join(raw_line.strip().split())
        if not line:
            continue
        lowered = line.lower().strip(":")
        normalized = _normalize_title(line)
        if source_stem and normalized == source_stem:
            continue
        if _looks_like_filename_title(line, source_path or Path(line)):
            continue
        if re.match(r"^-+\s*page\s+\d+\s*-+$", lowered):
            continue
        if lowered in skip:
            continue
        if len(line) < 8 or len(line) > 120:
            continue
        if re.search(r"\b(www\.|https?://|\d{1,2}/\d{1,2}/\d{2,4})\b", line, flags=re.I):
            continue
        words = re.findall(r"[A-Za-z][A-Za-z0-9+-]*", line)
        if len(words) < 3:
            continue
        score = len(words)
        if re.search(r"\b(power|verification|design|synthesis|simulation|optimization|architecture|rtl|timing|model|methodology|analysis|framework|flow)\b", line, flags=re.I):
            score += 8
        if re.search(r"\b(author|email|copyright|synopsys|abstract)\b", line, flags=re.I):
            score -= 8
        if best is None or score > best[0]:
            best = (score, line)
    return best[1] if best else ""


def _humanize_filename(stem: str) -> str:
    words = re.sub(r"[_-]+", " ", stem).strip()
    words = re.sub(r"\b(pres|user|paper|publish|only|snps)\b", "", words, flags=re.I)
    words = " ".join(words.split())
    return words.title() if words else stem


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _frontmatter(
    *,
    extracted: ExtractedContentMultimodal,
    source_path: Path,
    source_display_name: str,
    base_name: str,
    processed_at: datetime,
    extra: dict[str, Any],
) -> list[str]:
    source_hash = _file_sha256(source_path)
    lines = [
        "---",
        "origin:",
        "  creator: aily.chaos.chaos_markdown",
        f"  generated_at: {processed_at.isoformat(timespec='seconds')}",
        "  generation_method: source_equivalent_chaos_markdown",
        f"  source_file: {source_display_name}",
        f"  source_sha256: {source_hash}",
        f"  stored_object: {source_path.name}",
        f"  semantic_node: {base_name}",
        f"  processing_method: {extracted.processing_method}",
    ]
    for key, value in extra.items():
        lines.append(f"  {key}: {_yaml_scalar(value)}")
    lines.extend(["---", ""])
    return lines


def _extract_pdf_page_texts(source_path: Path) -> tuple[list[str], int]:
    try:
        import pdfplumber
    except ImportError:
        logger.warning("pdfplumber is unavailable; PDF page text cannot be extracted for %s", source_path.name)
        return [], 0

    texts: list[str] = []
    with pdfplumber.open(source_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            tables = page.extract_tables() or []
            table_markdown = [_table_to_markdown(table) for table in tables if table]
            if table_markdown:
                page_text = "\n\n".join([part for part in [page_text, *table_markdown] if part.strip()])
            texts.append(page_text.strip())
    return texts, len(texts)


def _render_pdf_page_images(source_path: Path, vault_path: Path, base_name: str) -> tuple[list[SlideAsset], str, str]:
    asset_dir = vault_path / "00-Chaos" / "_assets" / base_name / "slides"
    asset_dir.mkdir(parents=True, exist_ok=True)

    try:
        return _render_with_pymupdf(source_path, vault_path, asset_dir)
    except Exception as exc:
        pymupdf_error = str(exc)
        logger.warning("PyMuPDF PDF rendering failed for %s: %s", source_path.name, exc)

    try:
        return _render_with_pdf2image(source_path, vault_path, asset_dir)
    except Exception as exc:
        pdf2image_error = str(exc)
        logger.warning("pdf2image PDF rendering failed for %s: %s", source_path.name, exc)

    if os.getenv("AILY_USE_PYPDFIUM_RENDERER", "").lower() in {"1", "true", "yes"}:
        try:
            return _render_with_pypdfium2(source_path, vault_path, asset_dir)
        except Exception as exc:
            pypdfium_error = str(exc)
            logger.warning("pypdfium2 PDF rendering failed for %s: %s", source_path.name, exc)
    else:
        pypdfium_error = "disabled by default; set AILY_USE_PYPDFIUM_RENDERER=1 to enable"

    error = f"pymupdf={pymupdf_error}; pdf2image={pdf2image_error}; pypdfium2={pypdfium_error}"
    logger.warning("PDF page rendering failed for %s: %s", source_path.name, error)
    return [], "unavailable", error[:500]


def _render_with_pypdfium2(source_path: Path, vault_path: Path, asset_dir: Path) -> tuple[list[SlideAsset], str, str]:
    import pypdfium2 as pdfium  # type: ignore[import-not-found]

    assets: list[SlideAsset] = []
    pdf = pdfium.PdfDocument(source_path)
    try:
        for page_index in range(len(pdf)):
            page = pdf[page_index]
            target = asset_dir / f"slide-{page_index + 1:03d}.png"
            if not target.exists():
                bitmap = page.render(scale=2.0)
                image = bitmap.to_pil()
                image.save(target, format="PNG")
                width, height = image.width, image.height
            else:
                width, height = None, None
            assets.append(_slide_asset(page_index + 1, vault_path, target, width=width, height=height))
    finally:
        pdf.close()
    return assets, "pypdfium2", ""


def _render_with_pymupdf(source_path: Path, vault_path: Path, asset_dir: Path) -> tuple[list[SlideAsset], str, str]:
    import fitz  # type: ignore[import-not-found]

    assets: list[SlideAsset] = []
    with fitz.open(source_path) as doc:
        matrix = fitz.Matrix(2.0, 2.0)
        for page_index, page in enumerate(doc, start=1):
            target = asset_dir / f"slide-{page_index:03d}.png"
            if not target.exists():
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                pix.save(str(target))
                width, height = pix.width, pix.height
            else:
                width, height = None, None
            assets.append(_slide_asset(page_index, vault_path, target, width=width, height=height))
    return assets, "pymupdf", ""


def _render_with_pdf2image(source_path: Path, vault_path: Path, asset_dir: Path) -> tuple[list[SlideAsset], str, str]:
    from pdf2image import convert_from_path

    images = convert_from_path(str(source_path), dpi=144)
    assets: list[SlideAsset] = []
    for page_index, image in enumerate(images, start=1):
        target = asset_dir / f"slide-{page_index:03d}.png"
        if not target.exists():
            image.save(target, format="PNG")
        assets.append(_slide_asset(page_index, vault_path, target, width=image.width, height=image.height))
    return assets, "pdf2image", ""


def _slide_asset(page_number: int, vault_path: Path, target: Path, *, width: int | None, height: int | None) -> SlideAsset:
    relative = target.relative_to(vault_path).as_posix()
    return SlideAsset(
        page_number=page_number,
        vault_relative_path=relative,
        wikilink=f"![[{relative}]]",
        width=width,
        height=height,
    )


def _table_to_markdown(table: list[list[Any]]) -> str:
    rows = [["" if cell is None else str(cell).replace("\n", " ").strip() for cell in row] for row in table]
    rows = [row for row in rows if any(cell for cell in row)]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    header = padded[0]
    body = padded[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _markdown_code_block(text: str) -> list[str]:
    fence = "```"
    while fence in text:
        fence += "`"
    return [fence, text.strip(), fence]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'
