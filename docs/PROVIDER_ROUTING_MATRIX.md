# Provider Routing Matrix

Phase 7 makes provider selection explicit and benchmarkable.

## Default Workload Routes

| Workload | Provider | Reason |
|---|---|---|
| `dikiwi.DATA` | Kimi | Long-context extraction and lower-level normalization. |
| `dikiwi.INFORMATION` | Kimi | Classification and clustering over datapoints. |
| `chaos.vision` | Kimi | Current default multimodal route. |
| `dikiwi.KNOWLEDGE` | DeepSeek | Upper-layer reasoning over graph neighborhoods. |
| `dikiwi.INSIGHT` | DeepSeek | Path reasoning and opportunity detection. |
| `dikiwi.WISDOM` | DeepSeek | Long-path synthesis. |
| `dikiwi.IMPACT` | DeepSeek | Innovation nuclei and centrality interpretation. |
| `dikiwi.RESIDUAL` | DeepSeek | Proposal-quality synthesis from graph/vault context. |
| `reactor` | DeepSeek | Innovation proposal generation. |
| `gstack` | DeepSeek | Business critique. |
| `entrepreneur` | DeepSeek | Business evaluation. |
| `guru` | DeepSeek | Deep business/technical planning. |

## Provider Capabilities

- Kimi: best default for bulk extraction, long context, and vision-capable chaos processing.
- DeepSeek: default upper-cognition provider for graph reasoning, innovation proposals, entrepreneur review, and Guru planning.

## Quarantined Providers

- Zhipu is removed from active routing because the service is currently unreliable for Aily. Recent real smoke tests returned `429 Too Many Requests`, and previous entrepreneur outputs were visibly degraded by provider failures.

## Required Evaluation Discipline

- Use identical source manifests before comparing providers.
- Save run IDs and LLM traces before ranking output quality.
- Use the future V1 evidence harness for real API reachability checks against active providers only.
- Use benchmark reports for novelty, feasibility, evidence grounding, EDA relevance, and business depth.
- Do not rank a provider from unit tests or mocked responses.
