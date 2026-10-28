"""Explicit offline selection. Unimplemented live adapters never silently become stubs."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DependencyModes:
    readiness_source: str = "stub"
    data_agent_mode: str = "stub"
    event_sink: str = "stub"

    @classmethod
    def from_env(cls, values: Mapping[str, str]):
        choices = {
            "READINESS_SOURCE": ("stub", "fabric"),
            "DATA_AGENT_MODE": ("stub", "live"),
            "EVENT_SINK": ("stub", "eventstream"),
        }
        selected = {}
        for name, allowed in choices.items():
            value = values.get(name, "stub")
            if value not in allowed:
                raise ValueError(f"{name} must be one of {allowed}, not {value!r}")
            selected[name.lower()] = value
        return cls(**selected)

    def require_offline(self) -> None:
        if any(value != "stub" for value in asdict(self).values()):
            raise NotImplementedError("Live adapters are not implemented; no fallback to stubs")

    def labels(self) -> dict:
        return {**asdict(self), "dataverse": "not-connected"}
