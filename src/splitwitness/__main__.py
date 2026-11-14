import argparse
import json
import sys

from .core import AuditError, Limits, audit


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Audit fixed split assignments against declared co-location links."
    )
    parser.add_argument("records", help="UTF-8 CSV with exact header id,split")
    parser.add_argument("links", help="CSV: left_id,right_id,relation")
    parser.add_argument("--examples", type=int, default=0)
    parser.add_argument("--max-witness-edges", type=int, default=20)
    for name, value in vars(Limits()).items():
        parser.add_argument("--" + name.replace("_", "-"), type=int, default=value)
    args = parser.parse_args(argv)
    try:
        limits = Limits(**{name: getattr(args, name) for name in vars(Limits())})
        report = audit(
            args.records,
            args.links,
            examples=args.examples,
            max_witness_edges=args.max_witness_edges,
            limits=limits,
        )
        output = json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    except AuditError as exc:
        # AuditError messages contain only static labels and record numbers.
        print(f"splitwitness: audit incomplete; {exc}", file=sys.stderr)
        return 2
    except MemoryError:
        print("splitwitness: audit incomplete; memory exhausted", file=sys.stderr)
        return 2
    try:
        sys.stdout.write(output)
    except BrokenPipeError:
        return 2
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
