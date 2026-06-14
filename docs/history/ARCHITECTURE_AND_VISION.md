# Aily: Architecture and Vision

Aily is a three-mind system for turning messy inputs into structured knowledge, then into proposals, then into business-grade review material.

## Vision

The system is built around three consecutive forms of work:

1. Knowledge formation
2. Proposal generation
3. Business and execution planning

The current implementation is not a generic note bot. It is a pipeline that moves from DIKIWI notes to proposal nodes to business review appendices.

## Current Architecture

```text
Input
  -> DIKIWI
  -> Reactor
  -> Residual
  -> Entrepreneur / GStack
  -> Guru appendix
  -> Obsidian + GraphDB
```

### Input Layer

The system currently accepts input through:

- Feishu WebSocket messages
- chaos-file ingestion
- queue-driven legacy jobs for URLs, files, voice, digest, and session capture

The active Feishu path routes directly into `DikiwiMind`.

## Three Minds

### 1. DIKIWI Mind

Files:

- `aily/sessions/dikiwi_mind.py`
- `aily/dikiwi/orchestrator.py`
- `aily/dikiwi/agents/`

Responsibilities:

- run the six-stage DIKIWI pipeline
- keep per-pipeline memory and LLM budget
- write structured vault outputs
- populate GraphDB with nodes and relationships
- support both single-drop pipelines and stage-latched batch pipelines

Stage flow:

`DATA -> INFORMATION -> KNOWLEDGE -> INSIGHT -> WISDOM -> IMPACT`

For chaos batches, the active behavior is:

1. fill `00-Chaos`
2. advance the whole batch to `01-Data`
3. advance the whole batch to `02-Information`
4. advance the whole batch to `03-Knowledge`
5. continue only affected contexts into higher-order stages when graph growth crosses threshold

### 2. Reactor

File:

- `aily/sessions/reactor_scheduler.py`

Responsibilities:

- run multiple innovation frameworks in parallel
- generate proposal candidates from recent context
- score residual proposals before business review

Reactor is the innovation nozzle. It is now the active innovation scheduler. The old `Innolaval` naming is no longer the right model for the runtime.

### 3. Entrepreneur

Files:

- `aily/sessions/entrepreneur_scheduler.py`
- `aily/sessions/gstack_agent.py`

Responsibilities:

- evaluate pending business proposals with GStack
- persist review outcomes to GraphDB
- write entrepreneurship review notes
- trigger Guru planning appendices

## Post-Pipeline Proposal Flow

After DIKIWI reaches IMPACT:

1. Reactor generates framework proposals
2. Residual synthesizes vault, graph, and reactor context
3. Residual persists `residual_proposal` nodes
4. Reactor innovation-screens those residual proposals
5. Entrepreneur reviews `pending_business` proposals with GStack
6. Guru writes a detailed appendix for each reviewed proposal

This is the active proposal stack:

`DIKIWI -> Reactor -> Residual -> Reactor screening -> Entrepreneur -> Guru`

## Guru Role

Guru is a post-GStack planning role, not a voting persona.

It produces:

- a hypothesis-driven, fact-based business plan
- a simulation-driven, constraint-based, feedback-evolving development plan
- a CEO/CTO appendix even for denied ideas

The purpose is to preserve deep reasoning for future human use, not only to decide pass/fail.

## Storage Model

### Obsidian

Current vault layout:

- `00-Chaos`
- `01-Data`
- `02-Information`
- `03-Knowledge`
- `04-Insight`
- `05-Wisdom`
- `06-Impact`
- `07-Proposal`
- `08-Entrepreneurship`

### GraphDB

GraphDB is used for:

- DIKIWI nodes and edges
- incremental information-graph growth measurement
- residual proposal persistence
- innovation and business scoring
- review outcomes and related metadata

## Experimental Or Quarantined Subsystems

These packages still exist in-tree, but they are not part of the active production path:

- `aily/dikiwi/skills/`
- `aily/dikiwi/memorials/`

They should be treated as experimental or archival implementation material until explicitly rewired into runtime.

## Design Direction

The current system is moving toward:

- stronger proposal schemas
- stricter prompt contracts
- stage-latched corpus growth rather than document-local higher-order synthesis
- better proposal scoring based on buyer, workflow, and proof artifacts
- business review that fits deep-tech and EDA rather than consumer-startup defaults
- durable review artifacts in `08-Entrepreneurship`
