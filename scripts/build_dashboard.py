"""Generate the Grafana dashboard JSON (dashboard-as-code).

    python scripts/build_dashboard.py  ->  deploy/grafana/dashboards/greenroute.json
"""
import json
from pathlib import Path

DS = {"type": "prometheus", "uid": "prometheus"}
W = 'workload=~"interactive|batch"'
R = "[$__rate_interval]"
_id = iter(range(1, 100))


def target(expr, legend="", instant=False):
    return {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": chr(64 + next(_ref)),
            **({"instant": True} if instant else {})}


def panel(kind, title, targets, x, y, w, h, unit="short", decimals=None, extra=None):
    p = {"id": next(_id), "type": kind, "title": title, "datasource": DS,
         "gridPos": {"x": x, "y": y, "w": w, "h": h},
         "fieldConfig": {"defaults": {"unit": unit}, "overrides": []}, "targets": targets,
         "options": {}}
    if decimals is not None:
        p["fieldConfig"]["defaults"]["decimals"] = decimals
    if kind == "timeseries":
        p["fieldConfig"]["defaults"]["custom"] = {"lineWidth": 2, "fillOpacity": 10}
    p.update(extra or {})
    return p


def per_request(metric):
    return f"sum(increase({metric}{{{W}}}[$__range])) / sum(increase(greenroute_requests_total{{{W}}}[$__range]))"


def savings(metric):
    return (f"100 * (1 - sum(increase(greenroute_{metric}_total{{{W}}}[$__range])) / "
            f"sum(increase(greenroute_baseline_{metric}_total{{{W}}}[$__range])))")


def rate_per_req(metric):
    return f"sum(rate({metric}{{{W}}}{R})) / sum(rate(greenroute_requests_total{{{W}}}{R}))"


panels = []
_ref = iter(range(1, 30))
stat = {"options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value",
                    "graphMode": "none"}}
green = {"options": {**stat["options"], "colorMode": "background"},
         "fieldConfig": {"defaults": {"unit": "percent", "decimals": 1, "thresholds": {
             "mode": "absolute", "steps": [{"color": "red", "value": None},
                                           {"color": "orange", "value": 20},
                                           {"color": "green", "value": 50}]}}, "overrides": []}}

# Row 1: headline stats
row = [
    ("CO2e per request", per_request("greenroute_co2_grams_total"), "massg", 5),
    ("Energy per request", per_request("greenroute_energy_wh_total"), "watth", 4),
    ("Cost per request", per_request("greenroute_cost_usd_total"), "currencyUSD", 6),
]
for i, (t, e, u, d) in enumerate(row):
    _ref = iter(range(1, 30))
    panels.append(panel("stat", t, [target(e, instant=True)], i * 4, 0, 4, 5, u, d, stat))
for i, (t, m) in enumerate([("CO2e saved vs always-largest", "co2_grams"),
                            ("Energy saved vs always-largest", "energy_wh"),
                            ("Cost saved vs always-largest", "cost_usd")]):
    _ref = iter(range(1, 30))
    p = panel("stat", t, [target(savings(m), instant=True)], 12 + i * 4, 0, 4, 5)
    p.update(green)
    p["fieldConfig"] = json.loads(json.dumps(green["fieldConfig"]))
    panels.append(p)

# Row 2: grid + routing mix
_ref = iter(range(1, 30))
panels.append(panel("timeseries", "Grid carbon intensity (live)",
                    [target("greenroute_grid_intensity_g_per_kwh", "{{zone}}")], 0, 5, 12, 8,
                    "none", 1, {"description": "gCO2e/kWh from Electricity Maps"}))
_ref = iter(range(1, 30))
panels.append(panel("piechart", "Final model mix",
                    [target(f"sum by (final_model) (increase(greenroute_requests_total{{{W}}}[$__range]))",
                            "{{final_model}}", instant=True)], 12, 5, 6, 8,
                    extra={"options": {"reduceOptions": {"calcs": ["lastNotNull"]},
                                       "pieType": "donut", "legend": {"displayMode": "table",
                                                                      "placement": "right",
                                                                      "values": ["percent"]}}}))
_ref = iter(range(1, 30))
panels.append(panel("timeseries", "Escalation rate",
                    [target(f'sum(rate(greenroute_requests_total{{{W},escalated="true"}}{R})) / '
                            f"sum(rate(greenroute_requests_total{{{W}}}{R}))", "escalated")],
                    18, 5, 6, 8, "percentunit", 1))

# Row 3: per-request trends vs baseline
for i, (t, m, b, u) in enumerate([
    ("Energy per request vs baseline", "greenroute_energy_wh_total", "greenroute_baseline_energy_wh_total", "watth"),
    ("CO2e per request vs baseline", "greenroute_co2_grams_total", "greenroute_baseline_co2_grams_total", "massg"),
    ("Cost per request vs baseline", "greenroute_cost_usd_total", "greenroute_baseline_cost_usd_total", "currencyUSD"),
]):
    _ref = iter(range(1, 30))
    panels.append(panel("timeseries", t, [target(rate_per_req(m), "GreenRoute"),
                                          target(rate_per_req(b), "Always-largest")],
                        i * 8, 13, 8, 8, u))

# Row 4: health + quality gate + batch
_ref = iter(range(1, 30))
panels.append(panel("timeseries", "Requests/s by final model",
                    [target(f"sum by (final_model) (rate(greenroute_requests_total{{{W}}}{R}))",
                            "{{final_model}}")], 0, 21, 8, 8, "reqps"))
_ref = iter(range(1, 30))
panels.append(panel("timeseries", "Quality-gate failure rate by model",
                    [target('sum by (model) (rate(greenroute_model_calls_total{role="answer",outcome="fail"}'
                            f'{R})) / sum by (model) (rate(greenroute_model_calls_total{{role="answer"}}{R}))',
                            "{{model}}")], 8, 21, 8, 8, "percentunit", 1))
_ref = iter(range(1, 30))
panels.append(panel("timeseries", "Latency p50 / p95",
                    [target(f"histogram_quantile(0.5, sum by (le) (rate(greenroute_request_latency_seconds_bucket{{{W}}}{R})))", "p50"),
                     target(f"histogram_quantile(0.95, sum by (le) (rate(greenroute_request_latency_seconds_bucket{{{W}}}{R})))", "p95")],
                    16, 21, 8, 8, "s"))
_ref = iter(range(1, 30))
panels.append(panel("stat", "Batch jobs waiting for green window",
                    [target("greenroute_batch_jobs_pending", instant=True)], 0, 29, 6, 5, extra=stat))
_ref = iter(range(1, 30))
panels.append(panel("stat", "CO2e avoided by deferral",
                    [target("sum(increase(greenroute_batch_co2_avoided_grams_total[$__range]))",
                            instant=True)], 6, 29, 6, 5, "massg", 4, stat))
_ref = iter(range(1, 30))
panels.append(panel("timeseries", "Batch deferral (hours, p50 / p95)",
                    [target("histogram_quantile(0.5, sum by (le) (rate(greenroute_batch_deferral_hours_bucket[1h])))", "p50"),
                     target("histogram_quantile(0.95, sum by (le) (rate(greenroute_batch_deferral_hours_bucket[1h])))", "p95")],
                    12, 29, 12, 5, "h"))

dashboard = {
    "uid": "greenroute", "title": "GreenRoute: carbon-aware LLM routing",
    "tags": ["greenroute", "llm", "sustainability"], "timezone": "browser",
    "schemaVersion": 39, "version": 1, "refresh": "30s",
    "time": {"from": "now-6h", "to": "now"}, "panels": panels,
}
out = Path(__file__).resolve().parents[1] / "deploy/grafana/dashboards/greenroute.json"
out.write_text(json.dumps(dashboard, indent=2))
print(f"wrote {out} ({len(panels)} panels)")
