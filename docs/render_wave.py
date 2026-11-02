"""AsciiDoc block filter: WaveJSON on stdin, an embedded SVG image on stdout."""
import base64
import html
import json
import sys

import wavedrom


source = json.load(sys.stdin)
drawing = wavedrom.render(json.dumps(source))
encoded = base64.b64encode(drawing.tostring().encode()).decode()
alt = html.escape(source.get("head", {}).get("text", "Timing diagram"), quote=True)
print(f'<img alt="{alt}" src="data:image/svg+xml;base64,{encoded}">')
