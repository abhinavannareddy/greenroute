"""Prometheus metrics. prometheus_client appends _total to counters."""
from prometheus_client import Counter, Gauge, Histogram

from .accounting import Footprint

_SMALL = (0.0001, 0.0003, 0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1, 3, 10)

REQUESTS = Counter("greenroute_requests", "Routed requests",
                   ["workload", "final_model", "escalated"])
MODEL_CALLS = Counter("greenroute_model_calls", "Model invocations",
                      ["model", "role", "outcome"])
ESCALATIONS = Counter("greenroute_escalations", "Escalations between tiers",
                      ["from_model", "to_model"])

ENERGY = Counter("greenroute_energy_wh", "Estimated energy (Wh)", ["model", "workload"])
CO2 = Counter("greenroute_co2_grams", "Estimated CO2e (g)", ["model", "workload"])
COST = Counter("greenroute_cost_usd", "Cost (USD)", ["model", "workload"])

BASELINE_ENERGY = Counter("greenroute_baseline_energy_wh",
                          "Counterfactual energy if the largest model had answered", ["workload"])
BASELINE_CO2 = Counter("greenroute_baseline_co2_grams", "Counterfactual CO2e (g)", ["workload"])
BASELINE_COST = Counter("greenroute_baseline_cost_usd", "Counterfactual cost (USD)", ["workload"])

REQUEST_ENERGY = Histogram("greenroute_request_energy_wh", "Energy per request",
                           ["workload"], buckets=_SMALL)
REQUEST_CO2 = Histogram("greenroute_request_co2_grams", "CO2e per request",
                        ["workload"], buckets=tuple(b / 100 for b in _SMALL))
LATENCY = Histogram("greenroute_request_latency_seconds", "End-to-end latency", ["workload"],
                    buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 32, 64))

GRID_INTENSITY = Gauge("greenroute_grid_intensity_g_per_kwh",
                       "Live grid carbon intensity", ["zone"])
CARBON_FALLBACKS = Counter("greenroute_carbon_fallbacks", "Carbon data fallbacks", ["reason"])

BATCH_JOBS = Counter("greenroute_batch_jobs", "Batch job state transitions", ["state"])
BATCH_PENDING = Gauge("greenroute_batch_jobs_pending", "Batch jobs waiting for a green window")
BATCH_DEFERRAL = Histogram("greenroute_batch_deferral_hours", "Actual deferral per job",
                           buckets=(0, 0.5, 1, 2, 4, 8, 12, 24, 48))
BATCH_CO2_AVOIDED = Counter("greenroute_batch_co2_avoided_grams",
                            "CO2e avoided by running batch jobs in greener hours")


def record_footprint(model: str, workload: str, fp: Footprint) -> None:
    ENERGY.labels(model, workload).inc(fp.energy_wh)
    CO2.labels(model, workload).inc(fp.co2_g)
    COST.labels(model, workload).inc(fp.cost_usd)
