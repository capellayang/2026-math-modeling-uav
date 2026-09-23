"""Q4 records. All intervals are half-open and measured in mission seconds."""

from dataclasses import dataclass, field

RESOURCE_KEYS = ("A_UAV", "B_UAV", "C_UAV", "A_battery", "B_battery",
                 "C_battery", "relay_UAV", "relay_component")
POLICIES = ("strict_no_duplication", "replicate_relay")


@dataclass(frozen=True)
class Q4AlgorithmConfig:
    relay_policy: str = "both"
    selection: str = "pareto_lexicographic"


@dataclass(frozen=True)
class AtomicTaskComponent:
    component_id: str
    service_areas: tuple[str, ...]
    transport_sorties: tuple[str, ...]
    relay_sorties: tuple[str, ...]
    box_count: int
    cargo_mass_kg: float


@dataclass(frozen=True)
class ResourceInterval:
    task_id: str
    source_task_id: str
    group_id: str
    resource_type: str
    start_s: float
    end_s: float
    source_q3_resource_id: str


@dataclass(frozen=True)
class ResourceAssignment:
    task_id: str
    source_task_id: str
    group_id: str
    resource_type: str
    internal_resource_id: str
    occupied_start_s: float
    occupied_end_s: float
    source_q3_resource_id: str


@dataclass(frozen=True)
class ResourcePeak:
    group_id: str
    resource_type: str
    start_s: float | None
    end_s: float | None
    required: int
    critical_task_ids: tuple[str, ...]


@dataclass(frozen=True)
class TaskGroup:
    group_id: str
    service_areas: tuple[str, ...]
    transport_sorties: tuple[str, ...]
    relay_tasks: tuple[dict, ...]


@dataclass(frozen=True)
class Q4GroupMetrics:
    group: TaskGroup
    service_area_count: int
    box_count: int
    cargo_mass_kg: float
    transport_sortie_count: int
    relay_task_count: int
    transport_workload_s: float
    relay_workload_s: float
    total_workload_s: float
    inherited_q3_id_count: dict[str, int]
    minimum_recolored_requirement: dict[str, int]
    resource_peaks: tuple[ResourcePeak, ...]
    assignments: tuple[ResourceAssignment, ...]


@dataclass(frozen=True)
class Q4Candidate:
    candidate_id: str
    k: int
    relay_policy: str
    canonical_group_signature: str
    groups: tuple[Q4GroupMetrics, ...]
    resource_vector: dict[str, int]
    resource_scale: int
    partition_redundancy: dict[str, int]
    partition_redundancy_total: int
    stock_surplus: dict[str, int]
    stock_gap: dict[str, int]
    stock_gap_total: int
    workload_cv: float
    workload_range: float
    original_relay_task_count: int
    effective_relay_task_count: int
    pareto: bool = False
    selected: bool = False


@dataclass(frozen=True)
class Q4PolicyResult:
    relay_policy: str
    k: int
    atomic_component_count: int
    feasible: bool
    infeasibility_reason: str | None
    candidates: tuple[Q4Candidate, ...] = ()
    selected_candidate_id: str | None = None
    pareto_candidate_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Q4Solution:
    source_q3_sha256: str
    source_q3_frozen: dict
    q3_validation: dict
    transport_components: tuple[AtomicTaskComponent, ...]
    strict_components: tuple[AtomicTaskComponent, ...]
    relay_bindings: tuple[dict, ...]
    stock: dict[str, int]
    global_q3_minimum: dict[str, int]
    policy_results: tuple[Q4PolicyResult, ...]
    config: Q4AlgorithmConfig = field(default_factory=Q4AlgorithmConfig)
