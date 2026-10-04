"""Typed models and schemas for RuntimeDeck."""

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator


def _coerce_meta_str(v: Any) -> Optional[str]:
    """Coerce arbitrary metadata values (int, float, str) to clean strings."""
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _coerce_port(v: Any) -> Optional[int]:
    """Coerce arbitrary port representations to an integer within legal range."""
    if v is None:
        return None
    try:
        p = int(v)
        return p if 1 <= p <= 65535 else None
    except (ValueError, TypeError, OverflowError):
        return None


class GpuConfig(BaseModel):
    name: str
    uuid: str


class ManagedProcessSpec(BaseModel):
    executable: Optional[str] = None
    port: Optional[int] = None
    exe_names: List[str] = Field(default_factory=list)
    markers: List[str] = Field(default_factory=list)
    cmd_markers: List[str] = Field(default_factory=list)
    role: Optional[str] = None
    process_role: Optional[str] = None


class ProfileConfig(BaseModel):
    name: str
    port: Optional[int] = None
    default_gpu: Optional[str] = None
    cwd: str
    executable: str
    args: List[str] = Field(default_factory=list)
    managed_process: Optional[ManagedProcessSpec] = None
    model_id: Optional[str] = None
    context_size: Optional[str] = None
    kv_quant: Optional[str] = None

    @field_validator("port", mode="before")
    @classmethod
    def normalize_port(cls, v: Any) -> Optional[int]:
        return _coerce_port(v)

    @field_validator("model_id", "context_size", "kv_quant", mode="before")
    @classmethod
    def normalize_metadata(cls, v: Any) -> Optional[str]:
        return _coerce_meta_str(v)


class AppConfig(BaseModel):
    gpus: Dict[str, GpuConfig] = Field(default_factory=dict)
    profiles: Dict[str, ProfileConfig] = Field(default_factory=dict)


class GpuMetric(BaseModel):
    index: int
    name: str
    uuid: str
    used_mb: int
    total_mb: int
    util_percent: int


class GpuProcess(BaseModel):
    gpu_index: int
    gpu_uuid: str
    pid: int
    name: str
    executable: str
    used_mb: Optional[int] = None
    create_time: Optional[float] = None
    can_kill: bool = True
    kill_reason: Optional[str] = None


class GpuProcessesResponse(BaseModel):
    gpu_index: int
    gpu_uuid: str
    processes: List[GpuProcess]
    errors: List[str]


class KillGpuProcessRequest(BaseModel):
    create_time: Optional[float] = None


class KillGpuProcessResponse(BaseModel):
    status: str
    gpu_index: int
    pid: int
    exited: bool
    message: Optional[str] = None


# Legacy / Internal Instance Record
class InstanceRecord(BaseModel):
    profile: str
    name: str
    pid: int
    create_time: float
    executable: str
    gpu: str
    gpu_name: str
    started_at: str
    process_role: Optional[str] = None
    command_line_redacted: Optional[str] = None
    launcher_pid: Optional[int] = None
    launcher_create_time: Optional[float] = None
    launcher_executable: Optional[str] = None
    model_id: Optional[str] = None
    context_size: Optional[str] = None
    kv_quant: Optional[str] = None
    port: Optional[int] = None

    @field_validator("port", mode="before")
    @classmethod
    def normalize_port(cls, v: Any) -> Optional[int]:
        return _coerce_port(v)

    @field_validator("model_id", "context_size", "kv_quant", mode="before")
    @classmethod
    def normalize_metadata(cls, v: Any) -> Optional[str]:
        return _coerce_meta_str(v)


class InstanceView(BaseModel):
    profile: str
    name: str
    pid: int
    create_time: float
    executable: str
    gpu: str
    gpu_name: str
    started_at: str
    process_role: Optional[str] = None
    launcher_pid: Optional[int] = None
    launcher_create_time: Optional[float] = None
    launcher_executable: Optional[str] = None
    model_id: Optional[str] = None
    context_size: Optional[str] = None
    kv_quant: Optional[str] = None
    port: Optional[int] = None
    is_alive: bool
    identity_status: str  # "match" | "mismatch" | "not_found" | "error"
    is_legacy: bool = False

    @field_validator("port", mode="before")
    @classmethod
    def normalize_port(cls, v: Any) -> Optional[int]:
        return _coerce_port(v)

    @field_validator("model_id", "context_size", "kv_quant", mode="before")
    @classmethod
    def normalize_metadata(cls, v: Any) -> Optional[str]:
        return _coerce_meta_str(v)


class StatusResponse(BaseModel):
    gpus: List[GpuMetric]
    instances: Dict[str, InstanceView]
    errors: List[str]


class PortConflictDetail(BaseModel):
    conflict: Literal["port_in_use"] = "port_in_use"
    message: str
    port: int
    pid: Optional[int] = None
    name: Optional[str] = None
    create_time: Optional[float] = None
    can_force_clean: bool = True


class StartRequest(BaseModel):
    profile: str
    gpu: Optional[str] = None
    force_clean_port: bool = False
    expected_port_pid: Optional[int] = None


class StartResponse(BaseModel):
    status: str = "success"
    instance_id: str
    pid: int


class StopResponse(BaseModel):
    status: str  # "success" | "stale_cleaned"
    instance_id: str
    exited: bool
    message: Optional[str] = None


class DeleteProfileResponse(BaseModel):
    status: str = "success"
    message: str
    profile_id: str


# ---- Stable v1 API Models ----

RuntimeKind = Literal["llama_cpp", "comfyui", "unknown"]
ProfileState = Literal["running", "stopped", "stale", "error"]
ProfileHealth = Literal["ready", "unknown", "error"]


class V1ProfileSummary(BaseModel):
    profile_id: str
    name: str
    runtime_kind: RuntimeKind
    default_gpu: Optional[str] = None


class V1ProfilesResponse(BaseModel):
    profiles: List[V1ProfileSummary]


class V1ProfileStatusResponse(BaseModel):
    profile_id: str
    runtime_kind: RuntimeKind
    state: ProfileState
    endpoint: Optional[str] = None
    health: ProfileHealth
    gpu: Optional[str] = None


class V1StartRequest(BaseModel):
    gpu: Optional[str] = None
    force_clean_port: bool = False
    expected_port_pid: Optional[int] = None


# ---- Controlled Profile Script API Models ----

class ScriptResponse(BaseModel):
    """Safe script payload; no server-side path is exposed."""

    profile_id: str
    filename: Optional[str] = None
    language: Optional[str] = None
    content: str = ""
    revision: Optional[str] = None
    editable: bool = False


class ScriptSaveRequest(BaseModel):
    """Content and the revision observed by the editing client."""

    content: str
    expected_revision: Optional[str] = None
    revision: Optional[str] = None

    def get_expected_revision(self) -> Optional[str]:
        """Accept both explicit and concise revision spellings."""
        return self.expected_revision if self.expected_revision is not None else self.revision


class ScriptSaveResponse(ScriptResponse):
    """Response returned after a successful atomic script save."""


# Friendly aliases retained for integrations using the shorter names.
ScriptRequest = ScriptSaveRequest
ScriptReadResponse = ScriptResponse
ScriptUpdateRequest = ScriptSaveRequest
ScriptUpdateResponse = ScriptSaveResponse
