"""Higher-order synthesis (Insight/Wisdom/Impact) detection and candidates.

Detection is automatic and cheap and only RECOMMENDS ripe topics into the
candidate queue; generation stays explicit and approval-gated.
"""

from aily.synthesis.candidates import (
    SynthesisCandidate,
    SynthesisCandidateStore,
    candidate_id_for_nodes,
)
from aily.synthesis.detector import SynthesisDetector, subgraphs_to_candidates

__all__ = [
    "SynthesisCandidate",
    "SynthesisCandidateStore",
    "SynthesisDetector",
    "candidate_id_for_nodes",
    "subgraphs_to_candidates",
]
