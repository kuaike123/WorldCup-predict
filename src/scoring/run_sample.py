from __future__ import annotations

import argparse
import json
from pathlib import Path

from .engine import analyze_match
from .report_writer import save_outputs


def main() -> None:
    parser = argparse.ArgumentParser(description="Run v0.5 pre-match scoring for one match JSON file.")
    parser.add_argument("--input", required=True, help="Path to match input JSON.")
    parser.add_argument("--output-root", default=None, help="Optional output root, default is project outputs/.")
    args = parser.parse_args()

    input_path = Path(args.input)
    match = json.loads(input_path.read_text(encoding="utf-8"))
    analysis = analyze_match(match)
    result_path, report_path = save_outputs(analysis, Path(args.output_root) if args.output_root else None)
    print(json.dumps({
        "match_id": analysis["match_id"],
        "result_path": str(result_path),
        "report_path": str(report_path),
        "risk_level": analysis["risk_level"],
        "confidence": analysis["confidence"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
