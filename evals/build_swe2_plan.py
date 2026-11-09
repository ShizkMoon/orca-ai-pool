"""Generate the v3 plan against the SWE-2 impersonal alias."""
import copy
import json

SOURCE = "evals/plan-v3-ds41.json"


def load_plan(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        raise SystemExit(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc


def emit(plan, path):
    text = json.dumps(plan, ensure_ascii=False, indent=2) + chr(10)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError as exc:
        raise SystemExit(f"cannot write {path}: {exc}") from exc
    print(path, sum(d["limit"] for d in plan["domains"]), "requests")


base = load_plan(SOURCE)
for domain in base["domains"]:
    domain["alias_model"] = "devin/swe-2-imp"
    domain["baseline_model"] = "devin/swe-2"

full = copy.deepcopy(base)
smoke = copy.deepcopy(base)
for domain in smoke["domains"]:
    domain["limit"] = 1

emit(full, "evals/plan-v3-swe2.json")
emit(smoke, "evals/plan-v3-swe2-smoke.json")
