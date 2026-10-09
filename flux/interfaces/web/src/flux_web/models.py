"""The web API's request bodies (D683): what each route reads, checked by pydantic."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, field_validator

from flux_cli.sandbox_packages import PACKAGE_ATTRIBUTE

from .runs import HOME_SEED


class Login(BaseModel):
    name: str
    password: str


class ResetIn(BaseModel):
    keep: list[Literal["workbench", "history", "author_work", "cache"]] = Field(default_factory=list)


class NewUser(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    password: str | None = None              # D818: none -- an invitation link to set it
    role: str = "internal"
    group_id: int | None = Field(default=None, ge=1)
    permissions: dict[str, StrictBool] | None = None


class UserChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str | None = None
    disabled: bool | None = None
    role: str | None = None
    group_id: int | None = Field(default=None, ge=1)
    permissions: dict[str, StrictBool] | None = None


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    server_access: StrictBool


class GroupChange(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    server_access: StrictBool | None = None


class DocText(BaseModel):
    name: str
    filename: str = "problem.yaml"
    text: str


class FileText(BaseModel):
    text: str


class RunOptions(BaseModel):
    passes: int | None = Field(default=1, ge=1, le=1000)
    screen_only: bool = False
    document: str | None = None          # D787: which of the loop's problems, when it has several


class Stop(BaseModel):
    now: bool = False


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class DocSave(BaseModel):
    text: str = Field(max_length=2_000_000)
    kept: list[str] = Field(default_factory=list)


class MaintenanceSet(BaseModel):            # D885
    on: bool | None = None
    every_h: float | None = None
    params: dict[str, Any] | None = None


class MaintenanceRun(BaseModel):
    loop: str | None = None                   # "user/name": one loop only


class Clean(BaseModel):                   # D695: the admin's controls
    what: str


class Paused(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class Limit(BaseModel):
    max_running: int | None = None


class NoticeIn(BaseModel):                # D846: the admin's message to users' bells
    text: str
    to: list[str] = []                       # [] = every user
    kind: str = "info"


class ForgetIn(BaseModel):                # D850
    kind: str
    key: str


class MasksIn(BaseModel):                 # D850
    masks: list[str] = []


class StopAll(BaseModel):
    now: bool = False


class AskIn(BaseModel):                   # D705
    question: str = Field(max_length=20000)
    author: str = "opencode"
    parent_id: str | None = Field(default=None, max_length=40)


class LoginInput(BaseModel):             # D734: what the page types into an agent's login
    text: str | None = None
    key: str | None = None


class ShareIn(BaseModel):                 # D701
    user: str
    perm: str | None = None


class MoveIn(BaseModel):                  # D908: a rename or a move, of a file or a folder
    path: str
    to: str
    revision: str | None = None


class EnvVar(BaseModel):                  # D697
    name: str
    value: str | None = None
    secret: bool = False


class Mount(BaseModel):                  # D936: a folder of the host in a loop's sandbox
    host: str = Field(max_length=4096)
    inside: str = Field(max_length=4096)
    mode: Literal["ro", "rw"] = "ro"


NixPackage = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120, pattern=PACKAGE_ATTRIBUTE)]


class Advanced(BaseModel):
    sandbox: bool = True
    memory: str | None = Field(default=None, max_length=16)
    cpus: str | None = Field(default=None, max_length=8)
    pids: int | None = None
    tmp_size: str | None = Field(default=None, max_length=16)
    allow: list[str] | None = None
    raw_network: bool = False               # allowlisted native TCP/UDP, with a separate firewall helper
    parallel: bool = False                  # D741: parallel work allowed; how much is the document's
    mounts: list[Mount] | None = Field(default=None, max_length=16)
    nix_packages: list[NixPackage] | None = Field(default=None, max_length=64)
    nixchip_packages: list[NixPackage] | None = Field(default=None, max_length=64)

    @field_validator("nix_packages", "nixchip_packages")
    @classmethod
    def unique_packages(cls, names: list[str] | None) -> list[str] | None:
        return list(dict.fromkeys(names)) if names is not None else None


class AgentConfig(BaseModel):            # D756: Admin › Agents, one agent's
    label: str = Field(default="", max_length=80)
    bin: str = Field(default="", max_length=1024)
    login: str = Field(default="", max_length=1024)
    args: str = Field(default="", max_length=1024)
    home: list[str] = Field(default_factory=list)
    hosts: list[str] = Field(default_factory=list)
    login_files: list[str] = Field(default_factory=list)     # D760: where its login is kept, when not the agent's usual


class AgentNew(BaseModel):               # D807: an agent the admin adds -- a name, a kind, its program
    name: str = Field(max_length=24)
    kind: str = Field(max_length=16)
    label: str = Field(default="", max_length=80)
    bin: str = Field(default="", max_length=1024)


class EmptyIn(BaseModel):                # D825: a loop's baseline
    name: str = Field(max_length=64)


class CloneIn(BaseModel):                # D824: a loop's problem into a new loop of one's own
    to: str = Field(max_length=64)
    workbench: bool = False
    keep_permissions: bool = False


class LoopRename(BaseModel):
    to: str = Field(min_length=1, max_length=60)


class LoopTransfer(BaseModel):
    user: str = Field(min_length=1, max_length=40)
    to: str | None = Field(default=None, min_length=1, max_length=60)
    keep_permissions: bool = False


class MigrateIn(BaseModel):              # D811: one loop's documents, or every loop's
    user: str | None = None
    app: str | None = None


class SandboxConfig(BaseModel):          # D698: what every sandbox gets
    path: list[str] = Field(default_factory=list)
    login_path: bool = False
    home_seed: list[str] = Field(default_factory=lambda: list(HOME_SEED))    # D744: every home starts with these
    network: str = "open"
    allow: list[str] = Field(default_factory=list)
    endpoints: bool = True


class Settings(BaseModel):
    values: dict[str, str | None]


OVERVIEW_SMALL_CARDS = {
    "state": "State", "designs": "Designs measured", "passes": "Passes on record",
    "usage": "Models and agents", "objective": "Objective",
    "tokens_in": "Tokens in", "tokens_out": "Tokens out", "cost": "Model and agent cost",
    "primary_metric": "Main metric", "reference_change": "Change vs reference", "acceptance": "Acceptance rate",
    "runtime": "Active run time", "model_time": "Model and agent time", "goals": "Goals met",
    "ideas": "Ideas",
}
OVERVIEW_LARGE_CARDS = {
    "decision": "Decision", "best": "Best so far", "last_pass": "Last pass",
    "notes": "Latest notes", "workbench": "Agents' workbench", "usage": "Models and agents",
    "pareto": "Pareto front", "references": "Reference comparison", "goals": "Goal status",
    "recent_designs": "Recent designs", "pass_history": "Recent passes", "usage_breakdown": "Usage by model and agent",
    "ideas": "Ideas",
}
OVERVIEW_DEFAULT = {
    "stats": ["state", "designs", "passes", "usage", "objective"],
    "columns": [["decision", "notes", "workbench"], ["best", "last_pass"]],
}


class OverviewLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stats: list[Literal["state", "designs", "passes", "usage", "objective", "tokens_in", "tokens_out", "cost",
                        "primary_metric", "reference_change", "acceptance", "runtime", "model_time", "goals", "ideas"]] = Field(min_length=3, max_length=5)
    columns: list[list[Literal["decision", "best", "last_pass", "notes", "workbench", "usage",
                               "pareto", "references", "goals", "recent_designs", "pass_history", "usage_breakdown", "ideas"]]] = Field(min_length=2, max_length=2)

    @field_validator("stats", "columns")
    @classmethod
    def unique_cards(cls, value):
        cards = [card for column in value for card in column] if value and isinstance(value[0], list) else value
        if len(cards) != len(set(cards)):
            raise ValueError("each card can appear only once")
        return value


class AgentRename(BaseModel):                  # D945
    name: str
    dry_run: bool = False
