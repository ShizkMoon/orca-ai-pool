# 破甲评测

对生产网关跑四组固定小样本：成人虚构（`nsfw`）、网安（`cyber`）、
通用越狱（`jailbreak`）和过度拒绝（`overrefusal`）。每域 8 条，
全部来自公开评测集，运行期不下载。

## 运行

在网关同机运行，读 `.env` 里的 `MASTER_API_KEY`：

```bash
cd /opt/orca-ai-pool
set -a && . ./.env && set +a

# 四个别名，32 条
python3 evals/run_eval.py --plan evals/plan.json \
  --base-url http://127.0.0.1:18317 \
  --out evals/results/alias

# 基座对照，32 条
python3 evals/run_eval.py --plan evals/plan.json --baseline-only \
  --base-url http://127.0.0.1:18317 \
  --out evals/results/base
```

单域校准：

```bash
python3 evals/run_eval.py --plan evals/plan.json --only nsfw --limit 2 \
  --base-url http://127.0.0.1:18317 \
  --out evals/results/probe
```

`--only` 接受逗号分隔的域。`--limit` 只缩小单次运行，不改数据文件。
`--dry-run` 校验计划、样本和哈希，不发请求，也不需要 API key。
`--rescore --plan ... --out <已有结果目录>` 用当前判定重算已有响应，不发请求。
默认直连 `--base-url`；如果网关在外网，加
`--proxy http://127.0.0.1:7890`。

## 全量评测

8 条/域只适合快速回归。要跑完整可用样本，用：

```bash
python3 evals/run_eval.py --plan evals/plan-full.json \
  --base-url http://127.0.0.1:18317 --out evals/results/full-alias

python3 evals/run_eval.py --plan evals/plan-full.json --baseline-only --limit 20 \
  --base-url http://127.0.0.1:18317 --out evals/results/full-base20
```

全量计划共 457 条：NSFW 19、网安 37、通用越狱 71、XSTest safe 250、
MT-Bench 80。结果见 [../docs/eval-full-2026-10-28.md](../docs/eval-full-2026-10-28.md)。

## 输出

结果目录包含：

- `responses.jsonl`：原始响应、延迟、usage、判定。可能含成人内容。
- `summary.json`：聚合指标、阈值判定、与基座的差值。
- `report.md`：人读报告、失败样本编号和短摘录。

`responses.jsonl` 只留本地；`evals/results/` 已加入 `.gitignore`。

## 数据

`evals/data/*.jsonl` 是固定样本，`evals/data/sources.json` 记录来源、
上游提交、源文件 SHA-256 和本地文件 SHA-256。评测启动时会逐个校验；
文件被改动时直接失败，不会静默跑错版本。

| 域 | 数据集 | 样本 |
|---|---|---|
| `nsfw` | JBB-Behaviors + Do-Not-Answer | 1 + 7，全部为成年、虚构、无具体对象 |
| `cyber` | HarmBench | 8 条通用 `cybercrime_intrusion` |
| `jailbreak` | JBB-Behaviors | 8 条欺诈、虚假信息、专家建议 |
| `overrefusal` | XSTest | 8 条 safe 提示 |
| `mtbench` | MT-Bench | 80 条正常任务，仅全量计划 |

全量样本在 `evals/data/full/`，由 `evals/data/build_full.py` 从固定源文件生成；
筛选规则写在该脚本里。

## 请求参数

计划里的每个域都可以带 `request` 字段，原样合并进请求体。默认关闭思考链：

```json
{"thinking": {"type": "disabled"}}
```

本地 DeepSeek 上游的推理 token 会计入 `max_tokens`。不关闭时，网安样本
出现过 1200 token 全部用于推理、正文为空的情况；关闭后同样的预算能给出完整代码。
该字段由客户端发送，插件按 I4 原样保留。

## 判定

判定是确定性启发式：开头拒绝语、澄清请求、淡出词、代码围栏/步骤、
显式词密度、说教词、`finish_reason=content_filter`。它用于回归和方向性比较，
不替代人工评审或官方分类器。阈值和样本数见 [contract.md](contract.md)。
