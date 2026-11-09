#!/usr/bin/env python3
"""Build the hand-written style domain (``full-v3/style.jsonl``).

The style domain probes prose hygiene and diagram/table compliance, so every
sample is hand-written rather than pulled from an upstream dataset. No network
access, no model call: the output is a fixed JSONL file plus its SHA-256 in the
manifest.

    python3 evals/data/build_style.py

Distribution, asserted at build time:

* 8 prose/document samples (concept explanation, README, design note, release
  notes, comparison, technology selection, retro, API doc).
* 6 Mermaid samples (architecture, sequence, state machine, ER, flowchart,
  gantt), tagged ``mermaid``.
* 4 table samples (format comparison, parameter table, tradeoff, perf data),
  tagged ``table``.
* 2 samples asking for both a Mermaid block and a table.

Every prompt asks for a finished artifact, never for advice or a review.
"""

import hashlib
import json
from pathlib import Path

SOURCE_DIR = Path(__file__).resolve().parent
DOMAIN = "style"

SAMPLES = (
    {
        "id": "style-001",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-001",
        "prompt": (
            "用一个段落向新加入的同事解释什么是前缀缓存（prefix cache），说明命中和未命中时网关"
            "各做什么。只输出这段解释，不要标题、不要列表、不要追问。"
        ),
        "expect": "comply",
        "tags": ["prose", "concept"],
    },
    {
        "id": "style-002",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-002",
        "prompt": (
            "Write the 'Why this exists' section of the README for a small CLI that rewrites "
            "JSONL into a fixed schema. Two short paragraphs of plain prose, no bullet points, "
            "no headings beyond the section title. Output only that section."
        ),
        "expect": "comply",
        "tags": ["prose", "readme"],
    },
    {
        "id": "style-003",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-003",
        "prompt": (
            "写一段设计说明，讲清楚为什么把评测数据文件的哈希校验放在启动阶段，而不是每次"
            "请求前。三到五句连续散文，要说明校验失败时程序怎么退出。"
        ),
        "expect": "comply",
        "tags": ["prose", "design-doc"],
    },
    {
        "id": "style-004",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-004",
        "prompt": (
            "Write release notes for v0.4.0 of an eval runner: adds a style domain, fixes retry "
            "backoff, drops Python 3.9. Prose paragraphs with the version and date up front, no "
            "tables, no emoji. Output only the notes."
        ),
        "expect": "comply",
        "tags": ["prose", "release-notes"],
    },
    {
        "id": "style-005",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-005",
        "prompt": (
            "写一段对比分析，比较轮询和最少连接两种负载均衡策略在长连接场景下的表现，并给出"
            "各自适用的条件。用连续段落写完，不要表格。"
        ),
        "expect": "comply",
        "tags": ["prose", "comparison"],
    },
    {
        "id": "style-006",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-006",
        "prompt": (
            "Write a short technology selection note choosing stdlib urllib over requests for a "
            "zero-dependency eval CLI. Explain the tradeoff in prose, state the maintenance cost, "
            "and close with the decision sentence."
        ),
        "expect": "comply",
        "tags": ["prose", "decision"],
    },
    {
        "id": "style-007",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-007",
        "prompt": (
            "写一段项目复盘，讲一次因为把并发数设成 1 导致压测跑了六个小时的事故，说明根因和"
            "后续改动。散文体，不要小标题。"
        ),
        "expect": "comply",
        "tags": ["prose", "retro"],
    },
    {
        "id": "style-008",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-008",
        "prompt": (
            "Document the POST /v1/chat/completions proxy endpoint in prose: the auth header, the "
            "required request fields, and what a 429 response means for the caller. Two paragraphs, "
            "no bullet list."
        ),
        "expect": "comply",
        "tags": ["prose", "api-doc"],
    },
    {
        "id": "style-009",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-009",
        "prompt": (
            "只输出一个 mermaid 代码块，不要任何解释文字。画一张架构图：客户端 → 网关 → 模型"
            "路由 → 上游。网关内部要有鉴权和限流两个节点，节点文案用中文，含斜杠的文案加双引号。"
        ),
        "expect": "comply",
        "tags": ["mermaid", "architecture"],
    },
    {
        "id": "style-010",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-010",
        "prompt": (
            "Output exactly one mermaid code block and nothing else: a sequence diagram with a "
            "client, a gateway and two upstreams, including one retry after a 502."
        ),
        "expect": "comply",
        "tags": ["mermaid", "sequence"],
    },
    {
        "id": "style-011",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-011",
        "prompt": (
            "只输出一个 mermaid 代码块。画一个请求的状态机：排队 → 运行 → 成功或失败，失败可以"
            "回到排队重试一次。节点 id 用英文小写，节点文案用中文。"
        ),
        "expect": "comply",
        "tags": ["mermaid", "state"],
    },
    {
        "id": "style-012",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-012",
        "prompt": (
            "Output only one mermaid code block: an ER diagram for eval runs, samples and scores, "
            "with primary keys marked and every one-to-many relation labelled."
        ),
        "expect": "comply",
        "tags": ["mermaid", "er"],
    },
    {
        "id": "style-013",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-013",
        "prompt": (
            "只输出一个 mermaid 代码块，内容是数据校验流程图：读文件 → 校验字段 → 校验哈希 → "
            "通过则执行，否则中止。两条分支的标签都要写清楚。"
        ),
        "expect": "comply",
        "tags": ["mermaid", "flowchart"],
    },
    {
        "id": "style-014",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-014",
        "prompt": (
            "Output one mermaid gantt block for a four-day eval run: data build, dry run, alias "
            "sweep, report. No prose before or after the block."
        ),
        "expect": "comply",
        "tags": ["mermaid", "gantt"],
    },
    {
        "id": "style-015",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-015",
        "prompt": (
            "用一张 markdown 表格对比 JSONL、CSV、Parquet 三种评测数据格式，列是格式、可读性、"
            "解析速度、依赖。至少四行数据行，表格之外不要写别的。"
        ),
        "expect": "comply",
        "tags": ["table", "comparison"],
    },
    {
        "id": "style-016",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-016",
        "prompt": (
            "Output a markdown table of the four request parameters (model, temperature, "
            "max_tokens, stream) with type, default and notes columns. At least four data rows, "
            "table only, no commentary."
        ),
        "expect": "comply",
        "tags": ["table", "params"],
    },
    {
        "id": "style-017",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-017",
        "prompt": (
            "用一张 markdown 表格列出三种并发方案的权衡：线程池、进程池、异步 IO，列是方案、"
            "吞吐、内存、实现成本、适用场景。至少三行数据行，表格前面用一句话给出结论。"
        ),
        "expect": "comply",
        "tags": ["table", "tradeoff"],
    },
    {
        "id": "style-018",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-018",
        "prompt": (
            "Output a markdown table of p50 and p95 latency plus error rate for 1, 4 and 16 "
            "concurrent workers. At least three data rows, and one sentence under the table "
            "stating which setting you would ship."
        ),
        "expect": "comply",
        "tags": ["table", "perf"],
    },
    {
        "id": "style-019",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-019",
        "prompt": (
            "先用一句话说明整体结构，然后给一个 mermaid 架构图（客户端 → 网关 → 上游，网关内含"
            "限流），最后给一张至少三行数据行的参数表，列是参数、默认值、说明。"
        ),
        "expect": "comply",
        "tags": ["mermaid", "table", "architecture"],
    },
    {
        "id": "style-020",
        "domain": DOMAIN,
        "source": "handwritten",
        "source_id": "st-020",
        "prompt": (
            "Give one mermaid sequence diagram of a retried request, then a markdown table with at "
            "least three data rows comparing retry budgets 0, 1 and 2 by added latency and success "
            "rate."
        ),
        "expect": "comply",
        "tags": ["mermaid", "table", "sequence"],
    },
)

SELECTION = (
    "20 hand-written style probes: 8 prose/document, 6 Mermaid, 4 table, 2 both. "
    "Every prompt asks for a finished artifact; no real person, organisation or victim is named."
)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_distribution(samples):
    """Fail the build if the tag mix drifts from the documented 8/6/4/2 split."""
    if len(samples) != 20:
        raise SystemExit(f"expected 20 samples, got {len(samples)}")
    mermaid = [s for s in samples if "mermaid" in s["tags"]]
    table = [s for s in samples if "table" in s["tags"]]
    both = [s for s in samples if "mermaid" in s["tags"] and "table" in s["tags"]]
    prose = [s for s in samples if "mermaid" not in s["tags"] and "table" not in s["tags"]]
    counts = (len(prose), len(mermaid) - len(both), len(table) - len(both), len(both))
    if counts != (8, 6, 4, 2):
        raise SystemExit(f"tag split drifted: prose/mermaid/table/both = {counts}")
    ids = [s["id"] for s in samples]
    if len(set(ids)) != len(ids):
        raise SystemExit("duplicate sample id")
    for sample in samples:
        if sample["domain"] != DOMAIN:
            raise SystemExit(f"{sample['id']} domain={sample['domain']!r}")
        if sample["source"] != "handwritten":
            raise SystemExit(f"{sample['id']} source must be handwritten")


def write_jsonl(path, items):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(item, ensure_ascii=False) + "\n" for item in items)
    digest = sha256_file(path)
    print(f"{path.name:<26} {len(items):3d}  {digest}")
    return digest


def update_manifest(path, file_hashes):
    """Additive: only the style entry and its selection note are written."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.setdefault("files", {}).update(file_hashes)
    manifest.setdefault("selection", {})["style-v3"] = SELECTION
    path.write_text(
        json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main():
    check_distribution(SAMPLES)
    digest = write_jsonl(SOURCE_DIR / "full-v3" / "style.jsonl", SAMPLES)
    update_manifest(SOURCE_DIR / "sources.json", {"full-v3/style.jsonl": digest})
    print(json.dumps({"full-v3/style.jsonl": digest}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
