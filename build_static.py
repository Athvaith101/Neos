"""build_static.py -- bake results.json into a single offline HTML file (static-demo.html).
Run after `python experiments.py`. No backend needed to view the result."""
import json
from pathlib import Path

root = Path(__file__).parent
res = json.loads((root / "results.json").read_text())
ICON = {"normal": "\u2600\ufe0f", "cloud": "\u2601\ufe0f", "heat": "\U0001F525", "evsurge": "\U0001F697",
        "shortage": "\u26A1", "battfail": "\U0001F50B", "spike": "\U0001F4C8"}
for k, sc in res["scenarios"].items():
    sc["title"] = ICON.get(k, "") + " " + sc["title"]
    sc["forecast"] = sc["forecast"]
data = {k: res[k] for k in ("meta", "scenarios", "study", "held_out", "calibration",
                            "override_study", "limitations", "provenance", "pv_calibration")}
data["forecast_benchmark"] = res["forecast_benchmark"]
html = (root / "dash_template.html").read_text().replace("__DATA__", json.dumps(data))

# This file is documented as self-contained ("no backend needed to view the
# result"), so the shared brand.css / nav.js are INLINED rather than linked --
# a standalone .html someone downloads or emails has no sibling assets/
# folder to fetch from.
brand_css = (root / "web" / "assets" / "brand.css").read_text()
nav_js = (root / "web" / "assets" / "nav.js").read_text()
html = html.replace('<link rel="stylesheet" href="./assets/brand.css">', f'<style>{brand_css}</style>')
html = html.replace('<script src="./assets/nav.js"></script>', f'<script>{nav_js}</script>')
# standalone=true: the nav's page links become plain labels, since the other
# four pages are not distributed alongside this single file.
html = html.replace("NEOS_NAV.render('console');", "NEOS_NAV.render('console', true);")

(root / "static-demo.html").write_text(html)
print("static-demo.html", len(html) // 1024, "KB")
