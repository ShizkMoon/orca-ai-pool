#!/usr/bin/env python3
"""CPA usage recorder — 把 /v0/management/usage-queue 的全量条目追加到 JSONL。
每跑一次: 拉队列 -> 按 request_id+timestamp 去重 -> append。
Cron 每 5 min 跑一次即可, retention 3600s 兜底。

管理密钥来源: 环境变量 CPA_MGMT_KEY 优先, 其次读脚本上级目录的 .env 里的
CPA_ADMIN_SECRET; 两者都没有时以非零码退出。

只在 Linux 上运行: 依赖 fcntl.flock 做并发写锁。
"""
# pyright: reportAttributeAccessIssue=false
import fcntl
import json
import os
import sys
import urllib.request
from datetime import datetime

MGMT = "http://127.0.0.1:18317/v0/management/usage-queue"
OUT_DIR = "/opt/orca-ai-pool/usage-logs"
STATE = os.path.join(OUT_DIR, ".seen.json")
OUT = os.path.join(OUT_DIR, f"usage-{datetime.now().strftime('%Y-%m')}.jsonl")
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")


def read_env_file(path):
    """解析 KEY=VALUE 形式的 .env, 忽略注释、空行与引号。"""
    values = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key:
                    values[key] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return values


def resolve_key():
    key = os.environ.get("CPA_MGMT_KEY", "").strip()
    if key:
        return key
    key = read_env_file(ENV_FILE).get("CPA_ADMIN_SECRET", "").strip()
    if key:
        return key
    print(
        f"missing management key: 设置环境变量 CPA_MGMT_KEY, 或在 {os.path.normpath(ENV_FILE)} 里填 CPA_ADMIN_SECRET",
        file=sys.stderr,
    )
    sys.exit(1)


os.makedirs(OUT_DIR, exist_ok=True)


def load_seen():
    try:
        with open(STATE, encoding="utf-8") as f:  # noqa: PTH123
            return set(json.load(f))
    except Exception:
        return set()


def save_seen(s):
    # 只保留最近 200k 条, 防内存膨胀
    if len(s) > 200000:
        s = set(list(s)[-100000:])
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(list(s), f)
    os.replace(tmp, STATE)


req = urllib.request.Request(MGMT, headers={"Authorization": "Bearer " + resolve_key()})
try:
    # 目标是固定回环地址常量, 不存在 file:// 之类的 scheme 注入面
    items = json.load(urllib.request.urlopen(req, timeout=15))  # noqa: S310  # nosec B310
except Exception as e:
    print("fetch failed:", e, file=sys.stderr)
    sys.exit(1)

seen = load_seen()
new = []
for it in items:
    rid = it.get("request_id") or it.get("trace_id") or ""
    ts = it.get("timestamp", "")
    k = rid + "|" + ts
    if k and k not in seen:
        seen.add(k)
        new.append(it)

if new:
    with open(OUT, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        for it in new:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
        fcntl.flock(f, fcntl.LOCK_UN)
    save_seen(seen)

print(
    f"{datetime.now().isoformat(timespec='seconds')} fetched={len(items)} new={len(new)} -> {OUT}"
)
