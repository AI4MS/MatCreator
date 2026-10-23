"""PyWPEM runs through the OAW runtime protocol, in its own Python environment."""
from matcreator.xrd.compat import PackDefinition
from typing import Literal
from pydantic import BaseModel, Field
from matcreator.xrd.compat import AgentNodeBehavior, NodeTypeDefinition, PluginDescriptor
from .templates import remap_config, XRDTemplateHandler
from .runtime import XRDRuntime
from .history import actions as history_actions
from .sql_history import HistoryAgentBehavior
from .multiphase_object import register_harness
from .documents import InputDocument, import_input, associate
from matcreator.xrd.compat import (NodeDocumentDefinition, NodeDocumentAction, NodeDocumentDownload,
    CapabilityDefinition, CapabilityGrantDefinition, RelationshipDefinition)
import base64
import sqlite3
from .library import inspect_library
from .structures import MatchDocument, prepare_cod_structure, apply_cod_structure, download_structure
from matcreator.xrd.compat import ResourceValidationError

class LibraryDocument(BaseModel):
    kind: Literal['library'] = 'library'
    path: str = ''
    filename: str = ''
    sha256: str = ''
    size_bytes: int = 0
    mtime_ns: int = 0
    count: int = 0
    metadata: dict = Field(default_factory=dict)
    points: list = Field(default_factory=list)
    peaks: list = Field(default_factory=list)
    slots: list[dict | None] = Field(default_factory=list)

def configure_library(value, args):
    try:
        slot = args.get('slot', 0)
        if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < 6:
            raise ValueError('槽位必须为 1–6')
        mounted = inspect_library(args.get('path', ''))
        slots = list(value.get('slots') or ([{k:v for k,v in value.items() if k != 'slots'}] if value.get('path') else []))
        slots += [None] * (6 - len(slots))
        if any(d and i != slot and d.get('path') == mounted['path'] for i,d in enumerate(slots)):
            raise ValueError('这个数据库已经挂载在其他槽位')
        slots[slot] = mounted
        return {**next(d for d in slots if d), 'slots': slots}
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise ResourceValidationError(str(exc)) from exc

class MatchWorkspaceDocument(LibraryDocument, MatchDocument):
    pass

class XRDConfig(BaseModel):
    status: Literal["idle", "running", "waiting", "error"] = "idle"
    mode: Literal["fit", "match"] = "fit"
    connected_inputs: bool = False
    cif_node_id: str = ""
    tolerance_deg: float = Field(default=.15, gt=0, le=2)
    prominence_fraction: float = Field(default=.03, gt=0, le=.5)
    min_peak_distance: float = Field(default=.1, gt=0, le=2)
    smoothing_deg: float = Field(default=.03, gt=0, le=.5)
    reference_min_intensity: float = Field(default=5, ge=0, le=100)
    library_top_n: int = Field(default=20, ge=1, le=100)
    library_elements: str = Field(default='', max_length=300)
    library_engine: Literal['qualx', 'native'] = 'qualx'
    workflow_stage: Literal['search', 'preopt', 'fit'] = 'search'
    workflow_started_at_ms: int = Field(default=0, ge=0)
    workflow_match_run_id: str = Field(default='', max_length=128)
    workflow_preopt_run_id: str = Field(default='', max_length=128)
    selected_candidate_ids: list[str] = Field(default_factory=list, max_length=10)
    preopt_max_nfev: int = Field(default=120, ge=1, le=2000)
    preopt_coordinate_window: float = Field(default=.01, gt=0, le=.1)
    preopt_cell_window: float = Field(default=.35, gt=0, le=.5)
    runtime_provider_id: Literal["research.xrd"] = "research.xrd"
    model: Literal["OAW_XRDfit", "PyWPEM", "Peak matching"] = "OAW_XRDfit"
    max_concurrent_runs: Literal[1] = 1
    inherit_legion_model: Literal[False] = False
    system_instruction: str = "Run the configured OAW_XRDfit fit; completion is not proof of convergence or phase identity."
    intensity_csv: str = Field(default="", max_length=8*1024*1024)
    cif: str = Field(default="", max_length=2*1024*1024)
    iterations: int = Field(default=5, ge=1, le=700)
    wavelength: float = Field(default=1.540593, gt=0.1, le=5)
    low_angle: float = Field(default=20, ge=0, le=170)
    high_angle: float = Field(default=70, gt=0, le=180)
    preoptimize: bool = False
    demo: bool = False

class MatchConfig(XRDConfig):
    model: Literal["Peak matching", "OAW_XRDfit", "PyWPEM"] = "Peak matching"
    system_instruction: str = "Screen connected experimental peaks against reference cards; matching does not establish phase identity or purity."
    mode: Literal["match"] = "match"
    connected_inputs: Literal[True] = True
    demo: Literal[False] = False

class InputConfig(BaseModel):
    pass

class CanvasConfig(BaseModel):
    source_node_id: str = ''
