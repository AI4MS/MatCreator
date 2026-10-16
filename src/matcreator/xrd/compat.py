"""Small protocol boundary; MC does not import or run the OAW application."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
class ResourceValidationError(ValueError): pass
class AgentNotFoundError(KeyError): pass
class RuntimeProvider: pass
class NodeTemplateHandler: pass
class AgentNodeBehavior: pass
class NodeLifecycleTransaction: pass
class Definition:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.__dict__.update(kwargs)
NodeDocumentAction = NodeDocumentDefinition = NodeTypeDefinition = Definition
CapabilityDefinition = CapabilityGrantDefinition = RelationshipDefinition = Definition
PackDefinition = PluginDescriptor = NodeTemplateDependency = Definition
@dataclass
class NodeDocumentDownload:
    filename: str
    content: bytes
    media_type: str
@dataclass
class NodeResourceAction:
    handler: Any
    capability_kind: str = ''
class AgentStatus(str, Enum):
    IDLE = 'idle'
class AgentEventType(str, Enum):
    MESSAGE = 'message'
    COMPLETED = 'completed'
@dataclass
class AgentInfo:
    config: Any
    status: Any
    runtime_id: str
    details: dict = field(default_factory=dict)
@dataclass
class AgentEvent:
    agent_id: str
    run_id: str
    kind: Any
    payload: dict
    run_status: str | None = None
