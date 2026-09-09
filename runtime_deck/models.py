"""Typed models and schemas for RuntimeDeck."""

from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class GpuConfig(BaseModel):
    name: str
    uuid: str


class ManagedProcessSpec(BaseModel):
    executable: Optional[str] = None
    exe_names: List[str] = Field(default_factory=list)
    markers: List[str] = Field(default_factory=list)
    cmd_markers: List[str] = Field(default_factory=list)
    role: Optional[str] = None
    process_role: Optional[str] = None


class ProfileConfig(BaseModel):
    name: str
    default_gpu: Optional[str] = None
    cwd: str
    executable: str
    args: List[str] = Field(default_factory=list)
    managed_process: Optional[ManagedProcessSpec] = None


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
    is_alive: bool
    identity_status: str  # "match" | "mismatch" | "not_found" | "error"
    is_legacy: bool = False


class StatusResponse(BaseModel):
    gpus: List[GpuMetric]
    instances: Dict[str, InstanceView]
    errors: List[str]


class StartRequest(BaseModel):
    profile: str
    gpu: Optional[str] = None


class StartResponse(BaseModel):
    status: str = "success"
    instance_id: str
    pid: int


class StopResponse(BaseModel):
    status: str  # "success" | "stale_cleaned"
    instance_id: str
    exited: bool
    message: Optional[str] = None


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
