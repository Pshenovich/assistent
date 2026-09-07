"""Типы и константы Executive Board."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

AgentType = Literal["P", "A", "E", "I", "CHAIR", "USER"]
PAEIAgent = Literal["P", "A", "E", "I"]
MeetingStatus = Literal[
    "CREATED",
    "ANALYZING",
    "DISCUSSION",
    "CHALLENGE",
    "SYNTHESIS",
    "COMPLETED",
    "STOPPED",
    "ERROR",
]
Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
Reversibility = Literal["REVERSIBLE", "PARTIALLY_REVERSIBLE", "IRREVERSIBLE"]
DebateMode = Literal["DISCUSSION", "CHALLENGE", "SYNTHESIS"]
ClaimKind = Literal["FACT", "ASSUMPTION", "HYPOTHESIS", "OPINION"]

PAEI_AGENTS: tuple[PAEIAgent, ...] = ("P", "E", "A", "I")
ROUND1_ORDER: tuple[PAEIAgent, ...] = ("P", "E", "A", "I")

AGENT_META: dict[str, dict[str, str]] = {
    "P": {"emoji": "🔵", "title": "P — RESULTS", "focus": "KPI"},
    "A": {"emoji": "🟢", "title": "A — SYSTEM", "focus": "Риск"},
    "E": {"emoji": "🟣", "title": "E — STRATEGY", "focus": "Предлагаю"},
    "I": {"emoji": "🟠", "title": "I — PEOPLE", "focus": "Команда"},
    "CHAIR": {"emoji": "🧠", "title": "CHAIR", "focus": "Решение"},
    "USER": {"emoji": "👤", "title": "USER", "focus": ""},
}

ACTIVE_STATUSES: frozenset[str] = frozenset(
    {"CREATED", "ANALYZING", "DISCUSSION", "CHALLENGE", "SYNTHESIS"}
)

SEVERITY_ROUNDS: dict[str, int] = {
    "LOW": 2,
    "MEDIUM": 3,
    "HIGH": 4,
    "CRITICAL": 4,
}


@dataclass
class AgentResponse:
    agent: str
    position: str
    responding_to: list[str] = field(default_factory=list)
    agreement: list[str] = field(default_factory=list)
    disagreement: list[str] = field(default_factory=list)
    new_arguments: list[str] = field(default_factory=list)
    changed_position: bool = False
    proposal: str = ""
    confidence: float = 0.5
    needs_user_input: bool = False
    claims: list[dict[str, str]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def display_text(self) -> str:
        parts = [self.position.strip()]
        if self.disagreement:
            extra = "\n".join(f"• {x}" for x in self.disagreement if str(x).strip())
            if extra and extra not in parts[0]:
                parts.append(extra)
        if self.proposal.strip():
            parts.append(self.proposal.strip())
        return "\n\n".join(p for p in parts if p).strip()


@dataclass
class OrchestratorDecision:
    continue_debate: bool
    next_agent: PAEIAgent | None
    reason: str
    mode: DebateMode = "DISCUSSION"
    consensus_score: float = 0.5
    agreement_rate: float = 0.5
    unresolved: list[str] = field(default_factory=list)
    unique_arguments: int = 0
    position_changes: int = 0
    scores: dict[str, float] = field(default_factory=dict)


@dataclass
class ProblemAnalysis:
    problem: str
    decision_required: str
    known_facts: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    decision_type: str = "general"
    severity: Severity = "MEDIUM"
    reversibility: Reversibility = "PARTIALLY_REVERSIBLE"
    title: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChairDecision:
    problem: str
    decision: str
    why: list[str] = field(default_factory=list)
    actions: list[dict[str, str]] = field(default_factory=list)
    kpis: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    do_not_do: list[str] = field(default_factory=list)
    confidence: float = 0.7
    unavailable_agents: list[str] = field(default_factory=list)
    matrix: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
