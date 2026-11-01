"""D-side contract and capability checks for B's panic-risk lambda experiment.

This module never applies the risk-feedback equation itself.  It only validates
the requested experiment settings and reports whether the installed B runtime
publishes the configuration fields required to own that equation.
"""

from __future__ import annotations

import math
from dataclasses import fields, is_dataclass
from typing import Any, Mapping


LAMBDA_VALUES = (0.0, 0.2, 0.4, 0.6, 0.8)
RISK_FEEDBACK_CAP = 3.0
LAMBDA_EXIT_PRESSURE_GAIN = 0.5
PHYSICAL_CELL_SIZE_M = 0.5


class LambdaExperimentError(ValueError):
    """Raised when a lambda experiment request violates the D contract."""


def lambda_capability() -> dict[str, Any]:
    """Inspect B's public config and report whether lambda can really be used."""

    import simulation.movement_config as movement_config
    from simulation.movement_config import ExitChoiceConfig, PanicConfig

    panic_fields = (
        {field.name for field in fields(PanicConfig)}
        if is_dataclass(PanicConfig)
        else set()
    )
    exit_fields = (
        {field.name for field in fields(ExitChoiceConfig)}
        if is_dataclass(ExitChoiceConfig)
        else set()
    )
    missing = [
        name
        for name in ("panic_to_risk_gain", "risk_feedback_cap")
        if name not in panic_fields
    ]
    if "exit_pressure_gain" not in exit_fields:
        missing.append("exit_pressure_gain")
    implementation_verified = bool(
        getattr(movement_config, "PANIC_RISK_FEEDBACK_IMPLEMENTED", False)
    )
    if not implementation_verified:
        missing.append("B implementation marker PANIC_RISK_FEEDBACK_IMPLEMENTED")
    supported = not missing
    return {
        "feature": "panic_to_risk_lambda",
        "supported": supported,
        "status": "supported" if supported else "unavailable",
        "reason": (
            "B publishes the required fields and explicitly verifies runtime use"
            if supported
            else "B movement configuration is missing: " + ", ".join(missing)
        ),
        "required_parameter_path": "ScenarioConfig.parameters.movement.panic.panic_to_risk_gain",
        "allowed_lambda_values": list(LAMBDA_VALUES),
        "risk_feedback_cap": RISK_FEEDBACK_CAP,
        "lambda_exit_pressure_gain": LAMBDA_EXIT_PRESSURE_GAIN,
        "physical_cell_size_m": PHYSICAL_CELL_SIZE_M,
        "missing_fields": missing,
        "implementation_verified": implementation_verified,
    }


def lambda_movement_settings(value: Any, *, require_supported: bool = True) -> dict[str, Any]:
    """Validate one lambda level and return B's documented movement mapping."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LambdaExperimentError("lambda must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or not any(
        math.isclose(normalized, allowed, rel_tol=0.0, abs_tol=1e-12)
        for allowed in LAMBDA_VALUES
    ):
        raise LambdaExperimentError(
            "lambda must be one of: " + ", ".join(str(item) for item in LAMBDA_VALUES)
        )
    capability = lambda_capability()
    if require_supported and not capability["supported"]:
        raise LambdaExperimentError(str(capability["reason"]))
    return {
        "panic": {
            "panic_to_risk_gain": normalized,
            "risk_feedback_cap": RISK_FEEDBACK_CAP,
        },
        "exit_choice": {"exit_pressure_gain": LAMBDA_EXIT_PRESSURE_GAIN},
    }


def lambda_request_config(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return movement settings only for an explicitly requested lambda run."""

    requested = payload.get("lambda_experiment", False)
    if requested is not True:
        return None
    return lambda_movement_settings(payload.get("panic_to_risk_gain", 0.0))
