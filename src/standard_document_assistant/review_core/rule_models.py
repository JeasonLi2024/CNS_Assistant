from __future__ import annotations

from dataclasses import dataclass, field, asdict, fields
from typing import Dict, List, Optional


@dataclass
class RuleItem:
    chunk_id: str
    title: str
    scope: str
    content: str
    source_ref: str
    tags: List[str] = field(default_factory=list)
    analysis_mode: str = "local"
    target_scopes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "RuleItem":
        field_names = {f.name for f in fields(cls)}
        cleaned = {k: v for k, v in data.items() if k in field_names}
        if "target_scopes" not in cleaned:
            cleaned["target_scopes"] = []
        if "analysis_mode" not in cleaned:
            cleaned["analysis_mode"] = "local"
        return cls(**cleaned)

    def retrieval_text(self) -> str:
        joined_tags = " ".join(self.tags)
        joined_scopes = " ".join(self.target_scopes)
        return f"{self.title}\n{self.content}\n{self.scope}\n{self.analysis_mode}\n{joined_scopes}\n{joined_tags}"


@dataclass
class QueryContext:
    query: str
    scope: Optional[str] = None


@dataclass
class RetrievalHit:
    rule: RuleItem
    score: float
    source: str
    vector_score: float = 0.0


@dataclass
class AuditIssue:
    issue_id: str
    file_name: str
    rule_id: str
    rule_name: str
    scope: str
    severity: str
    status: str
    expected: str
    actual: str
    evidence_text: str
    source_ref: str
    suggestion: str
    confidence: float = 1.0
    # 来自 LLM JSON 的 reasoning：规则与证据的对照推理（简要说明）
    llm_reasoning: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class AuditResult:
    file_name: str
    pass_count: int = 0
    fail_count: int = 0
    warn_count: int = 0
    info_count: int = 0
    insufficient_context_count: int = 0
    llm_error_count: int = 0
    not_ready_count: int = 0
    issues: List[AuditIssue] = field(default_factory=list)

    def add_issue(self, issue: AuditIssue) -> None:
        self.issues.append(issue)
        if issue.status == "pass":
            self.pass_count += 1
        elif issue.status == "fail":
            self.fail_count += 1
            if issue.severity in {"轻度", "warn"}:
                self.warn_count += 1
        else:
            self.info_count += 1
            if issue.status == "insufficient_context":
                self.insufficient_context_count += 1
            elif issue.status == "llm_error":
                self.llm_error_count += 1
            elif issue.status == "not_ready":
                self.not_ready_count += 1

    def to_dict(self) -> Dict[str, object]:
        return {
            "file_name": self.file_name,
            "summary": {
                "pass_count": self.pass_count,
                "fail_count": self.fail_count,
                "warn_count": self.warn_count,
                "info_count": self.info_count,
                "insufficient_context_count": self.insufficient_context_count,
                "llm_error_count": self.llm_error_count,
                "not_ready_count": self.not_ready_count,
                "total_issues": len(self.issues),
            },
            "issues": [i.to_dict() for i in self.issues],
        }
