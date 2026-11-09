"""accounts.yaml 结构读取，以及敏感字/访问 key 的体检。"""

import re
from pathlib import Path

import yaml

from .console import PoolctlError
from .constants import SENSITIVE_WORDS_BUILTIN

def load_accounts(root):
    """读取 accounts.yaml，缺失键容错但给出清晰报错。"""
    path = Path(root) / "accounts.yaml"
    if not path.exists():
        raise PoolctlError(
            "未找到 %s\n请先执行: cp accounts.example.yaml accounts.yaml，并填入账号信息" % path
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PoolctlError("accounts.yaml YAML 解析失败: %s" % exc)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise PoolctlError("accounts.yaml 顶层必须是映射(dict)，实际为 %s" % type(raw).__name__)
    return raw


def _section(config, name):
    value = config.get(name)
    if value is None:
        raise PoolctlError("accounts.yaml 缺少 %s 段" % name)
    if not isinstance(value, dict):
        raise PoolctlError("accounts.yaml 的 %s 必须是映射(dict)" % name)
    return value


def _as_list(value, label):
    if value is None:
        return []
    if not isinstance(value, list):
        raise PoolctlError("%s 必须是列表(list)" % label)
    return value



def _devin_accounts(pool):
    accounts = _as_list(pool.get("accounts"), "devin_pool.accounts")
    normalized = []
    seen = set()
    for index, item in enumerate(accounts, 1):
        if not isinstance(item, dict):
            raise PoolctlError("devin_pool.accounts 第 %d 项必须是映射(dict)" % index)
        account_id = str(item.get("id") or "").strip()
        if not account_id:
            raise PoolctlError("devin_pool.accounts 第 %d 项缺少 id" % index)
        if "/" in account_id or "\\" in account_id or account_id in (".", ".."):
            raise PoolctlError("devin_pool.accounts 第 %d 项 id 含非法路径字符: %r" % (index, account_id))
        if account_id in seen:
            raise PoolctlError("devin_pool.accounts 存在重复 id: %s" % account_id)
        seen.add(account_id)
        token = str(item.get("session_token") or "").strip()
        if not token:
            raise PoolctlError("devin_pool.accounts[%s] 缺少 session_token" % account_id)
        plan = str(item.get("plan") or "Pro").strip()
        if plan.lower() not in ("pro", "max"):
            raise PoolctlError(
                "devin_pool.accounts[%s].plan 只能是 Pro 或 Max，当前值: %r"
                % (account_id, plan)
            )
        plan = "Max" if plan.lower() == "max" else "Pro"
        weight_raw = item.get("weight")
        if weight_raw is None or weight_raw == "":
            weight = None
        else:
            try:
                weight = int(weight_raw)
            except (TypeError, ValueError):
                raise PoolctlError(
                    "devin_pool.accounts[%s].weight 必须是整数，当前值: %r" % (account_id, weight_raw)
                )
            if weight < 1:
                raise PoolctlError("devin_pool.accounts[%s].weight 必须 >= 1" % account_id)
        normalized.append(
            {
                "id": account_id,
                "email": str(item.get("email") or "").strip(),
                "plan": plan,
                "weight": weight,
                "session_token": token,
            }
        )
    return normalized


def _sensitive_words(pool):
    config = pool.get("sensitive_words") or {}
    if not isinstance(config, dict):
        raise PoolctlError("devin_pool.sensitive_words 必须是映射(dict)")
    if not config.get("enabled"):
        return []
    extra = _as_list(config.get("extra"), "devin_pool.sensitive_words.extra")
    words = []
    for word in list(SENSITIVE_WORDS_BUILTIN) + [str(w) for w in extra]:
        word = word.strip()
        if word and word not in words:
            words.append(word)
    return words


def _env_flag(env, key):
    return str(env.get(key) or "").strip().lower() in ("1", "true", "yes", "on")


# 已知的出厂默认值 / 示例值。命中即视为未轮换。
WEAK_SECRET_VALUES = frozenset(
    {
        "sk-123456",
        "sk-123456789",
        "changeme",
        "password",
        "admin",
        "test",
        "123456",
    }
)
WEAK_SECRET_MARKERS = (
    "change-me",
    "change_me",
    "changeme",
    "replace-me",
    "replace_me",
    "your-",
    "your_",
    "placeholder",
)
# 密钥最短长度（字符）。CPA 会原样接收，弱密钥的唯一防线在这里。
MIN_SECRET_LEN = 24


def _access_key_list(env, name="EXTRA_API_KEYS"):
    """解析额外访问 key（.env 里逗号/空白分隔），保持书写顺序。"""
    raw = str(env.get(name) or "")
    return [item for item in re.split(r"[,\s]+", raw) if item]


def _secret_problems(env):
    """列出 .env 里弱/复用/过短的密钥。返回 [(键名, 原因)]。"""
    problems = []
    seen = {}
    # DISABLE_AUTH=true 时 MASTER_API_KEY 不参与鉴权，无需校验强度。
    keys = [] if _env_flag(env, "DISABLE_AUTH") else ["MASTER_API_KEY"]
    keys += [
        "CPA_ADMIN_SECRET",
        "OC2API_SERVER_KEY",
        "OC2API_WEBUI_PASSWORD",
    ]
    if not _env_flag(env, "DISABLE_AUTH"):
        # 额外访问 key 同样进强度/复用校验，避免有人拿示例值当专用 key。
        extra = _access_key_list(env)
        if extra:
            keys.append("EXTRA_API_KEYS")
    for key in keys:
        values = _access_key_list(env) if key == "EXTRA_API_KEYS" else [str(env.get(key) or "")]
        for value in values:
            value = value.strip()
            if not value:
                problems.append((key, "为空"))
                continue
            if value.lower() in WEAK_SECRET_VALUES:
                problems.append((key, "是已知默认值"))
            elif any(marker in value.lower() for marker in WEAK_SECRET_MARKERS):
                problems.append((key, "仍是示例占位值"))
            elif len(value) < MIN_SECRET_LEN:
                problems.append((key, "长度 %d < %d" % (len(value), MIN_SECRET_LEN)))
            if value:
                seen.setdefault(value, []).append(key)
    for value, owners in seen.items():
        if len(owners) > 1:
            problems.append((" / ".join(owners), "复用了同一个密钥"))
    return problems
