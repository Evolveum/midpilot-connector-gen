# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

"""Per-generation artifacts and the latest actionable validation feedback."""

import json
from dataclasses import asdict, dataclass

from src.modules.codegen.prompts.validation_feedback_prompts import FINAL_VALIDATION_REPAIR_INSTRUCTION
from src.modules.codegen.utils.connector_code_validation import CodeValidationReport


@dataclass(frozen=True)
class GeneratedArtifact:
    code: str
    validation: CodeValidationReport


@dataclass(frozen=True)
class ValidationFeedback:
    artifact: GeneratedArtifact
    chunk: str
    index: int


@dataclass
class GenerationState:
    """Keep at most the accepted artifact and the latest draft with diagnostics."""

    accepted: GeneratedArtifact | None = None
    pending: ValidationFeedback | None = None

    @property
    def code(self) -> str:
        return self.accepted.code if self.accepted else ""

    def known_artifacts(self) -> tuple[GeneratedArtifact, ...]:
        return tuple(
            artifact for artifact in (self.accepted, self.pending.artifact if self.pending else None) if artifact
        )

    def record(self, artifact: GeneratedArtifact, *, chunk: str, index: int) -> None:
        if not artifact.validation.errors:
            self.accepted = artifact
        if not artifact.validation.has_feedback:
            self.pending = None
        elif self.pending is None or self.pending.artifact != artifact:
            # Repeating the same draft adds no evidence; retain its originating chunk.
            self.pending = ValidationFeedback(artifact, chunk, index)

    def prompt_feedback(self, *, final_repair: bool = False) -> str:
        if self.pending is None:
            return ""
        artifact = self.pending.artifact
        rejected = bool(artifact.validation.errors)
        payload = {
            "source_chunk": self.pending.index,
            "artifact": "rejected_candidate" if rejected else "result",
            "errors": [asdict(item) for item in artifact.validation.errors],
            "warnings": [asdict(item) for item in artifact.validation.warnings],
        }
        if rejected:
            payload["rejected_candidate"] = artifact.code
        instruction = FINAL_VALIDATION_REPAIR_INSTRUCTION if final_repair else ""
        return (
            instruction
            + "\n<validation_feedback>\n"
            + json.dumps(payload, ensure_ascii=False)
            + "\n</validation_feedback>"
        )
