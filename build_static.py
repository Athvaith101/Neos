"""build_static.py -- bake results.json into a single offline HTML file (static-demo.html).
Run after `python experiments.py`. No backend needed to view the result."""
import json
from pathlib import Path

root = Path(__file__).parent
res = json.loads((root / "results.json").read_text(encoding="utf-8"))
ICON = {"normal": "\u2600\ufe0f", "cloud": "\u2601\ufe0f", "heat": "\U0001F525", "evsurge": "\U0001F697",
        "shortage": "\u26A1", "battfail": "\U0001F50B", "spike": "\U0001F4C8"}
for k, sc in res["scenarios"].items():
    sc["title"] = ICON.get(k, "") + " " + sc["title"]
    sc["forecast"] = sc["forecast"]
data = {k: res[k] for k in ("meta", "scenarios", "study", "held_out", "calibration",
                            "override_study", "limitations", "provenance", "pv_calibration")}
data["forecast_benchmark"] = res["forecast_benchmark"]
html = (root / "dash_template.html").read_text(encoding="utf-8").replace("__DATA__", json.dumps(data))
(root / "static-demo.html").write_text(html, encoding="utf-8")
print("static-demo.html", len(html) // 1024, "KB")
