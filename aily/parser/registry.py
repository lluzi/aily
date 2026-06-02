from __future__ import annotations

import re
from typing import Callable

from aily.parser.parsers import ParseResult, parse_generic

_REGISTRY: list[tuple[re.Pattern, Callable[[str, str], ParseResult]]] = []


def register(pattern: str, parser: Callable[[str, str], ParseResult]) -> None:
    _REGISTRY.append((re.compile(pattern), parser))


def detect_parser(url: str) -> Callable[[str, str], ParseResult]:
    for pat, fn in _REGISTRY:
        if pat.search(url):
            return fn
    return parse_generic


def parse(url: str, raw_text: str) -> ParseResult:
    parser = detect_parser(url)
    return parser(url, raw_text)
