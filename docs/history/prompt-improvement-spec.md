# Prompt Improvement Spec

This document records the prompt changes needed to improve DIKIWI note quality, proposal quality, and GStack fit for EDA and semiconductor workflows.

## Scope

The spec covers:

- `aily/llm/prompt_registry.py`
- `aily/thinking/frameworks/gstack.py`
- `aily/sessions/gstack_agent.py`
- Reactor proposal-generation prompts in `aily/thinking/frameworks/`

The spec does not attempt a full architecture rewrite. It focuses on prompt and prompt-contract quality first.

## Goals

- Reduce tautological and weak `KNOWLEDGE` links.
- Make `INSIGHT` outputs describe non-obvious claims instead of stitched labels.
- Make `WISDOM` notes more atomic and source-grounded.
- Turn `IMPACT` and `RESIDUAL` outputs into better proposal seeds and venture hypotheses.
- Make GStack evaluate deep-tech EDA opportunities with the right priors instead of default consumer-startup heuristics.

## Main Findings

### DIKIWI knowledge graph quality drops after INFORMATION

`DATA` and `INFORMATION` are mostly usable. The main prompt failures start in `KNOWLEDGE`, where broad relation types and weak anti-duplication guidance allow trivial edges. Those weak edges then degrade `INSIGHT` and `WISDOM`.

Implementation note: `KNOWLEDGE` is now graph-triggered. It should receive selected subgraph context from `aily/dikiwi/network_synthesis.py`, not only the current file's information nodes. If no meaningful changed subgraph is found, the pipeline should stop at `KNOWLEDGE`.

### Proposal generation is too theme-shaped

`IMPACT` and `RESIDUAL` often produce strong strategic directions, but not strong venture hypotheses. Common missing fields are:

- target user
- economic buyer
- workflow trigger
- current workaround
- integration surface
- proof artifact
- validation next step

### GStack uses the wrong default frame for EDA

The current GStack prompts over-index on generic YC-style PMF and growth-loop logic. For EDA and semiconductor tooling, the right questions are different:

- where does this insert into the flow?
- who owns the pain?
- who signs the budget?
- what proof artifact would convince a design team?
- how much signoff trust and workflow disruption is required?

## Prompt Design Principles

All prompt updates should follow these rules:

1. Prefer `specific evidence` over generic abstractions.
2. Prefer `none` over weak structure.
3. Reject duplicate or restated ideas instead of rephrasing them.
4. Distinguish `source-grounded fact`, `inference`, and `proposal`.
5. Require durable human titles for notes and proposal-like outputs.
6. Prefer narrow, testable deep-tech wedges over broad platform language.

## DIKIWI Prompt Changes

### DATA

Add stronger requirements for:

- reusable concept naming
- `canonical_title`
- concrete evidence anchors
- rejection of slide-fragment / table-of-contents content

### INFORMATION

Tighten classification toward:

- human-usable titles
- domain-native tags
- normalized enum values
- source evidence anchors

### KNOWLEDGE

Tighten relation creation:

- operate on selected graph subgraphs, not single-file local lists
- prefer links that explain why a changed node is attaching to an existing neighborhood
- forbid topic-only links
- forbid duplicate/restatement links
- prefer specific causal or dependency relations over vague ones
- require a concrete bridge explanation in the model reasoning

### INSIGHT

Tighten insight definition:

- an insight must combine linked facts into a new claim, decision rule, or design implication
- a renamed path is not an insight
- the model must explain why the conclusion is non-obvious

### WISDOM

Shift from broad synthesis to atomic permanent notes:

- one note per durable mechanism, tradeoff, workflow, or decision rule
- preserve `source_evidence`
- reject executive-summary style notes

### IMPACT

Treat `IMPACT` as proposal-seed generation:

- require target user and buyer framing
- require workflow insertion point
- require current workaround and proof-of-value
- avoid hype words unless source evidence supports them

### RESIDUAL

Residual proposals should be structured venture hypotheses, not only thematic recommendations. Proposal drafts should include:

- target user
- economic buyer
- current workaround
- why existing tools fail
- integration boundary
- adoption wedge
- proof artifact
- success metric
- next validation step

Residual should also consume prior rejection feedback before drafting the next round of proposals.

## Reactor Prompt Changes

The Reactor loop should move from `parallel generation + confidence sort` toward:

1. generate
2. critique
3. revise
4. rank

Prompt updates alone can improve generation quality, but true iteration quality still depends on better structured state between rounds.

## GStack Prompt Changes

### Framework-level GStack

Keep startup discipline, but reinterpret it for deep-tech contexts:

- PMF becomes workflow pain plus adoption friction.
- shipping discipline includes proof-of-value and benchmarkability.
- growth loops should be optional in enterprise and EDA cases.

### Agent-level GStack personas

Persona prompts should become more domain-aware and less rhetorically harsh. For EDA and semiconductor domains, prompts should prioritize:

- signoff trust
- insertion cost
- benchmark delta
- runtime or QoR delta
- pilotability with a design team
- buyer and champion clarity

Tone should remain direct, but prompts should avoid low-signal consumer-product language like `AI slop` in deep-tech evaluation mode.

## Implementation Order

1. Update the DIKIWI prompt registry contracts and guidelines.
2. Update Reactor proposal-generation prompts to emit tighter deep-tech hypotheses.
3. Update GStack framework and agent prompts for EDA-aware evaluation.
4. Add or update tests that assert the new prompt guidance is present.

## Known Limits

Prompt improvements alone will not fix:

- missing business fields not passed through runtime context
- GraphDB proposal persistence losing structured metadata
- ID-only path rendering in the insight stage

Those remain follow-up engineering tasks after the prompt pass.
