"""The web API's request bodies (D683): what each route reads, checked by pydantic."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .runs import HOME_SEED


class Login(BaseModel):
    name: str
    password: str


class NewUser(BaseModel):
    name: str
    password: str | None = None              # D818: none -- an invitation link to set it
    role: str = "internal"


class UserChange(BaseModel):
    password: str | None = None
    disabled: bool | None = None
    role: str | None = None


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


class AgentRename(BaseModel):                  # D945
    name: str
    dry_run: bool = False
