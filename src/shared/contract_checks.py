"""Validate supporting cross-track review assets, not live integrations."""

from pathlib import Path

import yaml
from openapi_spec_validator import validate

from shared.contract_generation import bundled_openapi
from shared.contract_validation import load_schemas


def validate_supporting_files(root: Path) -> None:
    spec = bundled_openapi(root)
    validate(spec)
    if spec["info"]["version"] != (root / "VERSION").read_text(encoding="utf-8").strip():
        raise ValueError("OpenAPI info.version must match contracts/VERSION")
    schemas = load_schemas(root)
    objects = schemas["models.schema.json"]["$defs"]
    mapping = yaml.safe_load((root / "dataverse" / "mapping.yaml").read_text(encoding="utf-8"))
    for name in (
        "CloseOutCase",
        "Photo",
        "AcceptanceArtifact",
        "ValidationResult",
        "DecisionLogEntry",
    ):
        if set(mapping["tables"][name]["columns"]) != set(objects[name]["properties"]):
            raise ValueError(f"Dataverse mapping does not cover logical fields for {name}")
    questions = yaml.safe_load(
        (root / "mcp" / "data-agent-tests.yaml").read_text(encoding="utf-8")
    )["cases"]
    if not 10 <= len(questions) <= 15:
        raise ValueError("Expected 10-15 Data Agent contract questions")
    if len({question["id"] for question in questions}) != len(questions):
        raise ValueError("Duplicate Data Agent question ID")
    for question in questions:
        for required in ("scenario", "question", "expectedRefs", "expectedFacts"):
            if required not in question:
                raise ValueError(f"Missing Data Agent question field: {required}")
