"""check-opencode：遍历 OpenCode Go key 做可用性/403 预检。"""

import json

from ..accounts import _as_list, _section, load_accounts
from ..console import PoolctlError, _banner, _emit, _info, _mask, _short
from ..httpx import _find_nested_key, _http_json

CHECK_URL = "https://opencode.ai/inference/go/openai/v1/chat/completions"


def _allowance_text(payload):
    if not isinstance(payload, dict):
        return None
    allowance = payload.get("x-opencode-allowance")
    opencode = payload.get("opencode")
    if isinstance(opencode, dict):
        allowance = opencode.get("allowance", allowance)
    elif opencode is not None and allowance is None:
        allowance = opencode
    if allowance is None:
        allowance = _find_nested_key(payload, ["allowance"])
    if allowance is None:
        return None
    if isinstance(allowance, (dict, list)):
        return json.dumps(allowance, ensure_ascii=False)
    return str(allowance)


def check_opencode(root):
    _banner("poolctl check-opencode")
    config = load_accounts(root)
    pool = _section(config, "opencode_go_pool")
    keys = [str(key).strip() for key in _as_list(pool.get("keys"), "opencode_go_pool.keys")]
    keys = [key for key in keys if key]
    if not keys:
        raise PoolctlError("opencode_go_pool.keys 为空，无可检测账号")

    total = len(keys)
    available = 0
    for index, key in enumerate(keys, 1):
        _emit("")
        _emit("-- [%d/%d] %s" % (index, total, _mask(key)))
        status, payload = _http_json(
            "POST",
            CHECK_URL,
            headers={
                "Authorization": "Bearer %s" % key,
                "User-Agent": "opencode-cli/1.0",
                "x-opencode-session": "ses_healthcheck01",
            },
            payload={
                "model": "deepseek-v4.1-flash",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
                "stream": False,
            },
            timeout=30,
        )
        if status == 200:
            available += 1
            _emit("✅ [Account %d] 正常可用" % index)
            allowance = _allowance_text(payload)
            if allowance:
                _emit("        额度: %s" % _short(allowance, 300))
            else:
                raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
                _emit("        raw usage: %s" % _short(raw, 300))
        elif status == 403:
            _emit(
                "❌ [Account %d] 403 拒绝访问！请登录该账号控制台 Settings -> Privacy，"
                "将 Regions 从 Europe & United States 改为 Global 以解锁 DeepSeek V4.1 Flash！" % index
            )
        elif status == 429:
            _emit("⏳ [Account %d] 429 限流：5h/周窗口冷却中" % index)
        elif status == 0:
            _emit("⚠️  [Account %d] 网络异常: %s" % (index, _short(payload, 300)))
        else:
            raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
            _emit("⚠️  [Account %d] HTTP %s: %s" % (index, status, _short(raw, 300)))

    _emit("")
    _info("汇总: %d/%d 可用" % (available, total))
    return 0 if available == total else 1
