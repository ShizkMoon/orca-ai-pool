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

# id 一律用官方清单里的写法（swe-2-1，不是 swe-2.1）。
# 官方还没有这个型号时按 clone_of 克隆；已有则原样覆盖 overrides 里的字段。
# overrides 放产品口径，跟官方目录不一致时以这里为准。
EXTRA_MODELS = (
    {
        "id": "swe-2-1",
        "display_name": "SWE-2.1",
        "clone_of": "swe-2",
        "overrides": {
            "context_length": 262144,
            "max_completion_tokens": 64000,
            # 只开 xhigh 一档思考强度（官方目录写 low/medium/high/xhigh/max 五档）
            "thinking": {"levels": ["xhigh"]},
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
    """补录缺失型号、覆盖已有型号字段。返回 (动作说明列表, 跳过原因列表)。"""
    models = payload["devin"]
    index = {str(item.get("id", "")).lower(): item for item in models}
    notes = []
    skipped = []
    for spec in EXTRA_MODELS:
        model_id = spec["id"]
        entry = index.get(model_id.lower())
        if entry is None:
            template = index.get(spec["clone_of"].lower())
            if template is None:
                skipped.append(f"{model_id}（缺模板 {spec['clone_of']}）")
                continue
            entry = copy.deepcopy(template)
            entry["id"] = model_id
            entry["display_name"] = spec["display_name"]
            models.append(entry)
            index[model_id.lower()] = entry
            notes.append(f"新增 {model_id}")
        else:
            notes.append(f"覆盖 {model_id}")
        overrides = spec.get("overrides") or {}
        if overrides:
            entry.update(copy.deepcopy(overrides))
    return notes, skipped


def _read_cached(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    models = payload.get("devin") if isinstance(payload, dict) else None
    return payload if isinstance(models, list) and models else None


def build(path):
    """生成补录目录。返回 (payload, notes, source, warning)；拿不到数据时 payload 为 None。"""
    path = Path(path)
    try:
        payload, source = load_official()
    except OSError as exc:
        cached = _read_cached(path)
        if cached is None:
            return None, [], "", f"官方 Devin 目录拉取失败且无本地缓存：{exc}"
        return cached, [], "cache", f"官方 Devin 目录拉取失败，沿用 {path.name}：{exc}"

    notes, skipped = merge_extras(payload)
    warning = "补录跳过：" + "、".join(skipped) if skipped else ""
    return payload, notes, source, warning
