# Aily Chaos Architecture

This document describes the current chaos ingestion path rather than older planned variants.

## Purpose

Chaos is the file-ingestion side of Aily. It turns files dropped into the chaos area into DIKIWI inputs.

The current active pieces are:

- `scripts/run_chaos_daemon.py`
- `aily/chaos/queue_processor.py`
- `aily/chaos/dikiwi_bridge.py`
- `aily/chaos/processors/`
- `aily/chaos/tagger/`

## Current Flow

```text
file arrives
  -> chaos queue processor
  -> content extraction
  -> write 00-Chaos / .processed batch artifacts
  -> optional tagging
  -> RainDrop conversion
  -> DikiwiMind.process_inputs_batched()
  -> numbered Obsidian vault + GraphDB
```

## Main Runtime Components

### Queue Processor

- file: `aily/chaos/queue_processor.py`
- role: track file-processing jobs and dispatch them through the processor layer

### DIKIWI Bridge

- file: `aily/chaos/dikiwi_bridge.py`
- role: convert extracted chaos content into `RainDrop` objects and hand them to `DikiwiMind`

The bridge now has both:

- single-item `process_extracted_content()`
- batch `process_extracted_content_batch()`

The batch path is the active semantics for folder ingestion.

### Processors

Current processor files include:

- `aily/chaos/processors/document.py`
- `aily/chaos/processors/pdf.py`
- `aily/chaos/processors/docling_processor.py`
- `aily/chaos/processors/pptx.py`
- `aily/chaos/processors/image.py`
- `aily/chaos/processors/video.py`

### Tagger

Current tagger files include:

- `aily/chaos/tagger/engine.py`
- `aily/chaos/tagger/content_based.py`
- `aily/chaos/tagger/llm_based.py`

The tagger is structured as a package, not as a single `tagger.py` module.

## Relationship To DIKIWI

Chaos is not a separate knowledge system. It is an ingestion layer for DIKIWI.

The bridge hands extracted content into:

- `aily/sessions/dikiwi_mind.py`

Once that handoff happens, the same DIKIWI, Reactor, Residual, Entrepreneur, and Guru path applies.

For batch folder ingestion the runtime is explicitly two-phase:

1. Extract files in parallel and persist semantic `00-Chaos` notes.
2. Run DIKIWI as a stage-latched batch over the new drops.

That means later DIKIWI stages wait for the earlier stage to finish across the batch.

## Output Layout

The current vault layout is the numbered DIKIWI structure:

- `00-Chaos`
- `01-Data`
- `02-Information`
- `03-Knowledge`
- `04-Insight`
- `05-Wisdom`
- `06-Impact`
- `07-Proposal`
- `08-Entrepreneurship`

Chaos-derived inputs enter at `00-Chaos` and then promote through the later directories.

Higher-order promotion is incremental. The information graph grows with new nodes, and batch DIKIWI only continues past `KNOWLEDGE` when information-node growth crosses the configured incremental threshold and the changed graph region forms meaningful neighborhoods.

## What This Document Does Not Claim

This document intentionally does not describe older planned modules that are not present in the repo, such as:

- `aily/chaos/detector.py`
- `aily/chaos/tagger.py`
- `pdf_enhanced.py`
- `image_enhanced.py`

Those names appeared in older planning docs, but they are not the current implementation surface.
