"""Shared defaults and contagion weighting for social relations.

The social-graph fallback is the existing ``stranger`` relation from
``social.relation_templates``.  The weighting policy mirrors the existing
``PanicConfig`` defaults and ``PanicModel._relation_weight`` calculation so
that callers can inspect a relation's weight without instantiating B.
"""

from __future__ import annotations

from typing import Mapping


# Existing C02 stranger defaults (social.relation_templates.RELATION_DEFAULTS).
DEFAULT_RELATION_STRENGTH = 0.0
DEFAULT_RELATION_TRUST = 0.1

# Existing B relation-contagion defaults (simulation.movement_config.PanicConfig).
_DEFAULT_TYPE_WEIGHTS: Mapping[str, float] = {
    "family": 1.00,
    "friend": 0.80,
    "classmate": 0.65,
    "colleague": 0.60,
    "doctor_patient": 0.50,
    "staff_to_customer": 0.35,
    "stranger": 0.20,
}
_RELATION_FLOOR = 0.30
_STRENGTH_WEIGHT = 0.50
_TRUST_WEIGHT = 0.50


def relation_contagion_weight(
    relation_type: str,
    strength: float = DEFAULT_RELATION_STRENGTH,
    trust: float = DEFAULT_RELATION_TRUST,
) -> float:
    """Return the established B relation-contagion weight for one relation."""

    type_name = str(relation_type).strip().lower()
    relation_strength = min(1.0, max(0.0, float(strength)))
    relation_trust = min(1.0, max(0.0, float(trust)))
    psi = _DEFAULT_TYPE_WEIGHTS.get(type_name, _DEFAULT_TYPE_WEIGHTS["stranger"])
    modulation = (
        _STRENGTH_WEIGHT * relation_strength + _TRUST_WEIGHT * relation_trust
    ) / (_STRENGTH_WEIGHT + _TRUST_WEIGHT)
    return max(0.0, psi * (_RELATION_FLOOR + (1.0 - _RELATION_FLOOR) * modulation))
