"""Memorial models - 奏折 (zouzhe) - memorial to the throne.

In Tang Dynasty, officials submitted memorials (奏折) to document decisions.
In DIKIWI, every stage decision is archived as a memorial.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any


class MemorialDecisionType(Enum):
    """Types of memorialized decisions."""

    PROMOTED = auto()       # Content advanced to next stage
    REJECTED = auto()       # Content rejected (封驳)
    MODIFIED = auto()       # Content approved with modifications
    AUTO_APPROVED = auto()  # TTL expired, auto-approved
    FAILED = auto()         # Processing failed


@dataclass(frozen=True)
class Memorial:
    """奏折 - Archive of a stage decision.

    Immutable record of every decision made in the DIKIWI pipeline.
    Links to full lineage via correlation_id.
    """

    # Identifiers
    memorial_id: str
    correlation_id: str
    pipeline_id: str

    # What happened
    stage: str
    decision: MemorialDecisionType

    # Content hashes (immutable verification)
    input_hash: str
    output_hash: str

    # Reasoning
    reasoning: str

    # Who made the decision
    agent_id: str
    gate_name: str = ""  # e.g., "menxia", "cvo"

    # Timestamps
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Additional metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        memorial_id: str,
        correlation_id: str,
        pipeline_id: str,
        stage: str,
        decision: MemorialDecisionType,
        input_content: str,
        output_content: str,
        reasoning: str,
        agent_id: str,
        gate_name: str = "",
        **metadata: Any,
    ) -> "Memorial":
        """Create a new memorial with content hashing."""
        input_hash = hashlib.sha256(input_content.encode()).hexdigest()[:16]
        output_hash = hashlib.sha256(output_content.encode()).hexdigest()[:16]

        return cls(
            memorial_id=memorial_id,
            correlation_id=correlation_id,
            pipeline_id=pipeline_id,
            stage=stage,
            decision=decision,
            input_hash=input_hash,
            output_hash=output_hash,
            reasoning=reasoning,
            agent_id=agent_id,
            gate_name=gate_name,
            metadata=metadata,
        )

    def to_markdown(self) -> str:
        """Convert to human-readable Obsidian format."""
        return f"""# Memorial: {self.memorial_id}

## Metadata
- **Pipeline**: `{self.pipeline_id}`
- **Correlation**: `{self.correlation_id}`
- **Stage**: {self.stage}
- **Decision**: {self.decision.name}
- **Gate**: {self.gate_name or "N/A"}
- **Timestamp**: {self.timestamp.isoformat()}
- **Agent**: `{self.agent_id}`

## Verification
- **Input Hash**: `{self.input_hash}`
- **Output Hash**: `{self.output_hash}`

## Reasoning

{self.reasoning}

## Additional Data

```json
{self.metadata}
```
"""

    def to_graph_node(self) -> dict[str, Any]:
        """Convert to GraphDB node properties."""
        return {
            "id": self.memorial_id,
            "correlation_id": self.correlation_id,
            "pipeline_id": self.pipeline_id,
            "stage": self.stage,
            "decision": self.decision.name,
            "gate_name": self.gate_name,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "reasoning": self.reasoning,
            "agent_id": self.agent_id,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }
