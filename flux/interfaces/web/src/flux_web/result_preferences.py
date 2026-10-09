"""Shared, durable graph and measurement preferences belonging to a loop."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints

Name = Annotated[str, StringConstraints(strict=True, max_length=512)]
Names = Annotated[list[Name], Field(max_length=1024)]


class GraphPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: Name = ""
    y: Name = ""
    paretoStage: Name = ""
    timeStage: Name = ""
    scope: Name = ""
    paretoFocus: StrictBool = False
    metrics: Names = Field(default_factory=list)


class DictionaryPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected: Name = ""
    expanded: StrictBool = False


class ResultPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hiddenMetrics: Names = Field(default_factory=list)
    showHiddenMetrics: StrictBool = False
    mainMetrics: Names = Field(default_factory=list)
    relativeMetrics: Annotated[dict[Name, StrictBool], Field(max_length=1024)] = Field(default_factory=dict)
    valuesMode: Literal["configured", "absolute", "relative"] = "absolute"
    dictionaryMetrics: Annotated[dict[Name, DictionaryPreference], Field(max_length=1024)] = Field(default_factory=dict)
    graphs: GraphPreferences = Field(default_factory=GraphPreferences)


def merge_preferences(saved: dict | None, patch: dict) -> dict:
    """Partial updates preserve unrelated settings, including nested graph/metric choices."""
    result = dict(saved or {})
    for key, value in patch.items():
        result[key] = merge_preferences(result.get(key), value) if isinstance(value, dict) else value
    return result


def preferences(store, owner: str, name: str) -> dict:
    return store.server_get(f"results:{owner}:{name}") or {}
