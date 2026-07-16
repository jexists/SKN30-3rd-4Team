"""Execute code cells from a notebook without requiring nbconvert."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from IPython.display import display
except ImportError:
    display = print


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument("notebook", type=Path)
    parser.add_argument("--compile-only", action="store_true")
    parser.add_argument(
        "--cells",
        help="실행할 셀 인덱스(쉼표 구분). 생략하면 모든 코드 셀을 실행한다.",
    )
    args = parser.parse_args()

    selected_cells = None
    if args.cells:
        selected_cells = {int(value.strip()) for value in args.cells.split(",")}

    path = args.notebook.resolve()
    notebook = json.loads(path.read_text(encoding="utf-8"))
    namespace = {"__name__": "__main__", "display": display}

    for index, cell in enumerate(notebook.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        if selected_cells is not None and index not in selected_cells:
            continue
        source = "".join(cell.get("source", []))
        if not source.strip():
            continue
        code = compile(source, f"{path.name}#cell-{index}", "exec")
        if args.compile_only:
            print(f"[cell {index}] 문법 확인")
            continue
        print(f"[cell {index}] 실행")
        exec(code, namespace)


if __name__ == "__main__":
    main()
