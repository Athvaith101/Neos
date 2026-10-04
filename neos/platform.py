"""Final NEOS platform integration facade.

The OpenDSS-verified Stage-1 control engine remains the authoritative
research/evidence core. This module exposes the broader operating-system
capabilities without replacing that core or pretending that roadmap-only
features are field-ready.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Kit:
    id: str
    name: str
    focus: str
    status: str


KITS = (
    Kit("urban", "Urban / Suburban", "EV, PV, home batteries and flexible loads", "implemented_core"),
    Kit("low_income", "Low-income / Peri-urban", "Community energy hub and essential-service resilience", "simulation_modules"),
    Kit("rural", "Rural", "Shared solar, battery, pumps, water and thermal/cold storage", "simulation_modules"),
)

CAPABILITIES = {
    "open_dss_verified_core": "verified_research_core",
    "probabilistic_forecasting": "implemented",
    "mpc_optimisation": "implemented",
    "safety_projection": "implemented",
    "flexibility_envelope": "implemented_stage1",
    "decision_trace": "implemented_stage1",
    "community_energy_hub": "simulation_module",
    "rural_scheduler": "simulation_module",
    "phase_aware_gateway": "simulation_module",
    "verification_ledger": "simulation_module",
    "offline_continuity": "simulation_module",
    "discom_interface": "simulation_module",
    "islanding": "concept_simulation_only",
    "hardware_control": "not_implemented",
    "hardware_in_loop": "not_implemented",
}


def platform_manifest() -> dict:
    return {
        "name": "NEOS — Neighbourhood Energy Operating System",
        "version": "Final Candidate 1.0-research",
        "kits": [asdict(k) for k in KITS],
        "capabilities": dict(CAPABILITIES),
        "evidence_rule": "OpenDSS-generated results remain the authoritative quantitative evidence; broader modules do not replace them.",
        "deployment_boundary": "Simulation / research prototype. No physical actuator control.",
    }
