"""Magnus Agent SDK — Interfaz programática principal.

Permite instanciar y controlar el motor Magnus, registrar agentes,
verificar permisos de egreso y consultar la wiki soberana en pocas líneas.
"""
from orchestration.engine import MagnusEngine
from orchestration.registry.agent_registry import AgentRegistry
from orchestration.capability_engine import CapabilityEngine
from orchestration.permissions import PermissionEngine
from orchestration.privacy import EgressPolicy

__all__ = [
    "MagnusEngine",
    "AgentRegistry",
    "CapabilityEngine",
    "PermissionEngine",
    "EgressPolicy",
]
