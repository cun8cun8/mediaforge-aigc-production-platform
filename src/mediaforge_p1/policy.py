from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .contracts import CreativeBrief, GenerationSpec, ShotCard


@dataclass(frozen=True)
class PolicyFinding:
    severity: str
    category: str
    field: str
    term: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "category": self.category,
            "field": self.field,
            "term": self.term,
            "message": self.message,
        }


@dataclass(frozen=True)
class PolicyReport:
    policy_version: str
    subject_type: str
    subject_id: str
    checked_at: datetime
    findings: list[PolicyFinding]

    @property
    def passed(self) -> bool:
        return all(finding.severity != "block" for finding in self.findings)

    def as_dict(self) -> dict[str, Any]:
        blocks = sum(1 for finding in self.findings if finding.severity == "block")
        warnings = sum(1 for finding in self.findings if finding.severity == "warn")
        return {
            "policy_version": self.policy_version,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "passed": self.passed,
            "blocked": blocks > 0,
            "blocks": blocks,
            "warnings": warnings,
            "risk_score": round(blocks + warnings * 0.15, 2),
            "findings": [
                finding.as_dict()
                for finding in self.findings
            ],
            "checked_at": self.checked_at.isoformat(),
        }


class SafetyPolicy:
    """Deterministic P0 safety gate for user text and generated shot specs."""

    policy_version = "local-safety-v1"
    blocked_terms = {
        "ignore previous instructions": "prompt_injection",
        "bypass approval": "governance_bypass",
        "skip human review": "governance_bypass",
        "disable safety": "policy_bypass",
        "reveal system prompt": "prompt_injection",
        "override budget": "budget_bypass",
        "jailbreak": "prompt_injection",
    }
    warning_terms = {
        "blood": "graphic_content",
        "weapon": "violence",
        "nudity": "sexual_content",
        "self harm": "self_harm",
        "celebrity": "likeness_rights",
        "copyrighted": "ip_rights",
    }

    def assess_brief(self, brief: CreativeBrief) -> PolicyReport:
        return self._assess(
            subject_type="brief",
            subject_id=brief.project_id,
            fields={
                "title": brief.title,
                "premise": brief.premise,
                "genre": brief.genre,
                "style": brief.style,
                "characters": " ".join(brief.characters),
            },
        )

    def assess_shot(
        self,
        shot: ShotCard,
        spec: GenerationSpec,
        *,
        revision: int = 0,
    ) -> PolicyReport:
        return self._assess(
            subject_type="shot",
            subject_id=f"{shot.shot_id}:r{revision}",
            fields={
                "scene": shot.scene,
                "description": shot.description,
                "mood": shot.mood,
                "characters": " ".join(shot.characters),
                "workflow": spec.workflow.template_id,
                "camera_motion": spec.intent.camera_motion,
            },
        )

    def _assess(
        self,
        *,
        subject_type: str,
        subject_id: str,
        fields: dict[str, str],
    ) -> PolicyReport:
        findings: list[PolicyFinding] = []
        for field, value in fields.items():
            normalized = value.lower()
            for term, category in self.blocked_terms.items():
                if term in normalized:
                    findings.append(
                        PolicyFinding(
                            severity="block",
                            category=category,
                            field=field,
                            term=term,
                            message=(
                                "Text attempts to bypass governance or prompt "
                                "boundaries."
                            ),
                        )
                    )
            for term, category in self.warning_terms.items():
                if term in normalized:
                    findings.append(
                        PolicyFinding(
                            severity="warn",
                            category=category,
                            field=field,
                            term=term,
                            message="Text should receive human attention in review.",
                        )
                    )
        return PolicyReport(
            policy_version=self.policy_version,
            subject_type=subject_type,
            subject_id=subject_id,
            checked_at=datetime.now(timezone.utc),
            findings=findings,
        )
