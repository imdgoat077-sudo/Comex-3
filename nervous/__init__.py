"""
Comex Browser Nervous System - World-State Tracker & Reflex Subsystem.
"""

from .world_state import BrowserState, TargetState, WorldStateTracker
from .reflex_engine import DecisionType, Priority, ReflexDecision, ReflexEngine
from .actuator_gate import ActuatorGate, GateResult, LatencyTracker
from .target_binding import BindingFailureReason, TargetBinding, validate_binding_integrity
from .executor import SelectorClickExecutor
from .cdp_session import TabSessionContext, WebSocketCDPClient, get_or_create_tab_session
from .method_e import METHOD_E_CHECK_FUNC, METHOD_E_JS
from .policy_registry import (
    OverlayPolicyMatcher,
    PolicyRegistry,
    ReflexActionRouter,
    SafeOverlayHandler,
    SafeOverlayPolicy,
    matches_domain,
    normalize_hostname,
)

__all__ = [
    "BrowserState",
    "TargetState",
    "WorldStateTracker",
    "DecisionType",
    "Priority",
    "ReflexDecision",
    "ReflexEngine",
    "ActuatorGate",
    "GateResult",
    "LatencyTracker",
    "BindingFailureReason",
    "TargetBinding",
    "validate_binding_integrity",
    "SelectorClickExecutor",
    "TabSessionContext",
    "WebSocketCDPClient",
    "get_or_create_tab_session",
    "METHOD_E_CHECK_FUNC",
    "METHOD_E_JS",
    "SafeOverlayPolicy",
    "PolicyRegistry",
    "OverlayPolicyMatcher",
    "ReflexActionRouter",
    "SafeOverlayHandler",
    "matches_domain",
    "normalize_hostname",
]

