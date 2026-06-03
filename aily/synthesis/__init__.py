"""Higher-order synthesis (Insight/Wisdom/Impact) detection and candidates.

Detection is automatic and cheap and only RECOMMENDS ripe topics into the
candidate queue; generation stays explicit and approval-gated.
"""

from aily.synthesis.candidates import (
    SynthesisCandidate,
    SynthesisCandidateStore,
    candidate_id_for_nodes,
)

__all__ = [
    "SynthesisCandidate",
    "SynthesisCandidateStore",
    "candidate_id_for_nodes",
]
