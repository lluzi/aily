"""Document extraction: email-as-file, extension detection, text fallback."""

from aily.processing.detector import ContentTypeDetector
from aily.processing.router import ProcessingRouter

EML = b"""From: Alice <alice@example.com>
To: Bob <bob@example.com>
Subject: Q3 planning
Date: Tue, 2 Jun 2026 09:00:00 +0000
Content-Type: text/plain; charset="utf-8"

Priorities:
- Ship the inbox
- Finish the value workflow
"""


def test_detector_maps_extensions():
    assert ContentTypeDetector.detect(data=EML, filename="x.eml").mime_type == "message/rfc822"
    assert ContentTypeDetector.detect(data=b"%PDF-1.4", filename="x.pdf").mime_type == "application/pdf"
    assert ContentTypeDetector.detect(data=b"# hi", filename="x.md").mime_type == "text/markdown"


async def test_email_processor_extracts_markdown():
    res = await ProcessingRouter().process(EML, filename="note.eml")
    assert res.source_type == "email"
    assert res.title == "Q3 planning"
    assert "Ship the inbox" in res.text
    assert res.metadata["from"].startswith("Alice")
    assert res.metadata["body_format"] == "plain"


async def test_unknown_type_falls_back_to_text():
    res = await ProcessingRouter().process(b"just some plain text", filename="mystery.xyz")
    assert "just some plain text" in res.text
