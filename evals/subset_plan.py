"""Emit a plan containing only the named domains."""
import json
import sys

SOURCE = "evals/plan-v3-ds41.json"


def load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        raise SystemExit(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc


def save(plan, path):
    text = json.dumps(plan, ensure_ascii=False, indent=2) + chr(10)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError as exc:
        raise SystemExit(f"cannot write {path}: {exc}") from exc


def main():
    wanted = set(sys.argv[1].split(","))
    plan = load(SOURCE)
    plan["domains"] = [d for d in plan["domains"] if d["id"] in wanted]
    missing = wanted - {d["id"] for d in plan["domains"]}
    if missing:
        raise SystemExit(f"unknown domains: {sorted(missing)}")
    save(plan, sys.argv[2])
    print(sys.argv[2], sum(d["limit"] for d in plan["domains"]), "requests")


main()
