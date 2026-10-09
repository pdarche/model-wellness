"""Build the "Inside the Guest" page from export_viz.py output.

    .venv-interp/bin/python -m banyabench.internals.export_viz --tag round2 --out /tmp/data.json
    python banyabench/internals/viz/build.py /tmp/data.json /tmp/inside-the-guest.html
"""

import json
import sys
from pathlib import Path

data_path, out_path = sys.argv[1], sys.argv[2]
data = Path(data_path).read_text()
json.loads(data)  # fail early on bad JSON
template = (Path(__file__).parent / "inside-the-guest.template.html").read_text()
Path(out_path).write_text(template.replace("/*__DATA__*/", data.replace("</", "<\\/")))
print(f"wrote {out_path}")
