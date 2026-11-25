"""Devin 模型目录补录。

CPA 判定模型归属只看 devin_models.json（二进制内置 + 每 3 小时从远端刷新）。
2026-11-25 上线的 SWE-2.1 还没进 router-for-me/models 的清单，registry 查不到
就 400 unknown provider，所以这里生成一份补录过的目录，由配置项
models.devin-catalog 指过去，容器里挂 cliproxy/catalog。

官方清单补上同 id 的条目后，这里按 id 去重，不会重复追加。
"""

import copy
import json
from pathlib import Path

from .httpx import _http_json

OFFICIAL_URLS = (
    "https://models.router-for.me/devin_models.json",
    "https://raw.githubusercontent.com/router-for-me/models/refs/heads/main/devin_models.json",
)

FETCH_TIMEOUT = 8

# clone_of：官方清单里同家族的条目，字段从它复制，只改 id 和 display_name。
# overrides：上游给的规格，跟模板不同就写死在这里，别跟着 swe-2 漂。
EXTRA_MODELS = (
    {
        "id": "swe-2.1",
        "display_name": "SWE-2.1",
        "clone_of": "swe-2",
        "overrides": {
            "context_length": 262144,
            "max_completion_tokens": 64000,
            # 上游只开 high 一档思考强度（官方目录写的是五档，以产品口径为准）
            "thinking": {"levels": ["high"]},
        },
    },
)


def load_official():
    """按顺序拉官方清单，返回 (payload, 源地址)。全部失败抛 OSError。"""
    problems = []
    for url in OFFICIAL_URLS:
        status, body = _http_json("GET", url, timeout=FETCH_TIMEOUT)
        if status != 200 or not isinstance(body, dict):
            detail = body if isinstance(body, str) else f"status={status}"
            problems.append(f"{url}: {detail}")
            continue
        models = body.get("devin")
        if isinstance(models, list) and models:
            return body, url
        problems.append(f"{url}: 清单结构异常（缺 devin 数组）")
    raise OSError("; ".join(problems))


def merge_extras(payload):
    """把 EXTRA_MODELS 补进 payload["devin"]，返回 (已补 id 列表, 跳过原因列表)。"""
    models = payload["devin"]
    index = {str(item.get("id", "")).lower(): item for item in models}
    added = []
    skipped = []
    for spec in EXTRA_MODELS:
        model_id = spec["id"]
        if model_id.lower() in index:
            continue
        template = index.get(spec["clone_of"].lower())
        if template is None:
            skipped.append(f"{model_id}（缺模板 {spec['clone_of']}）")
            continue
        entry = copy.deepcopy(template)
        entry["id"] = model_id
        entry["display_name"] = spec["display_name"]
        entry.update(copy.deepcopy(spec.get("overrides") or {}))
        models.append(entry)
        index[model_id.lower()] = entry
        added.append(model_id)
    return added, skipped


def _read_cached(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    models = payload.get("devin") if isinstance(payload, dict) else None
    return payload if isinstance(models, list) and models else None


def build(path):
    """生成补录目录。返回 (payload, added, source, warning)；拿不到数据时 payload 为 None。"""
    path = Path(path)
    try:
        payload, source = load_official()
    except OSError as exc:
        cached = _read_cached(path)
        if cached is None:
            return None, [], "", f"官方 Devin 目录拉取失败且无本地缓存：{exc}"
        return cached, [], "cache", f"官方 Devin 目录拉取失败，沿用 {path.name}：{exc}"

    added, skipped = merge_extras(payload)
    warning = "补录跳过：" + "、".join(skipped) if skipped else ""
    return payload, added, source, warning
