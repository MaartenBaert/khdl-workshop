"""Build an offline landing page and a separate HTML manual for each project."""
from importlib.util import find_spec
from pathlib import Path
import subprocess
import sys


def main():
    for package in ("asciidoc", "wavedrom"):
        if find_spec(package) is None:
            raise SystemExit(
                "Install the documentation dependencies with "
                f"{sys.executable} -m pip install -r docs/requirements.txt"
            )
    docs = Path(__file__).resolve().parent
    output_dir = docs.parent / "build/docs"
    output_dir.mkdir(parents=True, exist_ok=True)
    pages = [(docs / "index.adoc", "index.html")]
    pages.extend((source, source.parents[1].name + ".html")
                 for source in sorted((docs.parent / "projects").glob("*/docs/index.adoc")))
    for source, filename in pages:
        output = output_dir / filename
        attributes = ["encoding=UTF-8", "lang=en", "data-uri",
                      f"wave-filter={docs / 'render_wave.py'}",
                      f"manual-docinfo={docs / 'index-docinfo.html'}"]
        if filename != "index.html":
            attributes.extend(["toc", "toclevels=4", "numbered"])
        command = [sys.executable, "-m", "asciidoc", "-b", "html5",
                   "-f", str(docs / "wavedrom.conf")]
        for attribute in attributes:
            command.extend(["-a", attribute])
        command.extend(["-o", str(output), str(source)])
        result = subprocess.run(command, capture_output=True, text=True)
        # A broken include, reference, or diagram must not quietly ship a partial manual.
        if result.stdout or result.stderr:
            print(result.stdout + result.stderr, file=sys.stderr, end="")
        if result.returncode or result.stderr:
            raise SystemExit(result.returncode or 1)
        print(output)


if __name__ == "__main__":
    main()
