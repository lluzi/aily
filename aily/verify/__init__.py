"""Source verification for Aily - like a human clicking links to check claims."""
from __future__ import annotations

from aily.verify.verifier import ClaimVerifier, VerificationResult, verify_digest_claims

__all__ = ["ClaimVerifier", "VerificationResult", "verify_digest_claims"]
