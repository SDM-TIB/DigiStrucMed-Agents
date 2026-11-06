"""Copies the thesis repo's walker package (Walker V7) into app/vendor/walker, the code the Graph Walker page runs.

  py scripts/sync_walker.py [path to experiments/2026-10-31_browser_adapt_jev_walker/walker]

The source of truth is MASTERTHESIS-HEART, projects/03_reasoning_aware_guideline_retrieval/experiments/
2026-10-31_browser_adapt_jev_walker/walker. v6.py is not needed. The data (layout.json, props, topics) comes from
2026-11-05_proposition_graph/export_app_pack.py into app/data/walker.
"""
import shutil
import sys
from pathlib import Path

DEFAULT = Path(r"C:\Users\STEFFAN\Desktop\MASTERTHESIS\projects\03_reasoning_aware_guideline_retrieval\experiments\2026-10-31_browser_adapt_jev_walker\walker")
KEEP = ["__init__.py", "agent.py", "beam.py", "graph.py", "models.py", "rules.py", "search.py", "text.py", "v5.py", "v7.py"]


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    dst = Path(__file__).resolve().parent.parent / "app" / "vendor" / "walker"
    dst.mkdir(parents=True, exist_ok=True)
    for name in KEEP:
        shutil.copy(src / name, dst / name)
    print(f"copied {len(KEEP)} files to {dst}")


if __name__ == "__main__":
    main()
