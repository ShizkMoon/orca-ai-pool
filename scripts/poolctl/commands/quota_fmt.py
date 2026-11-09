"""quota 的取数与格式化辅助：管理接口签名、额度字段归一化。"""

import datetime
import re

from ..httpx import _http_json

def _norm_key(key):
    text = re.sub(r"(?<!^)(?=[A-Z])", "_", str(key))
    return text.strip().lower().replace("-", "_").replace(" ", "_")


def _signals_map(item):
    quota = item.get("quota") if isinstance(item, dict) else None
    signals = quota.get("signals") if isinstance(quota, dict) else None
    mapping = {}
    if isinstance(signals, dict):
        for key, value in signals.items():
            mapping[_norm_key(key)] = value
    elif isinstance(signals, list):
        for entry in signals:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") or entry.get("key") or entry.get("type") or entry.get("signal")
            if not name:
                continue
            for value_key in ("value", "remaining_percent", "percent", "percent_remaining"):
                if value_key in entry:
                    mapping[_norm_key(name)] = entry[value_key]
                    break
    if not mapping and isinstance(quota, dict):
        for key, value in quota.items():
            if key == "signals":
                continue
            mapping[_norm_key(key)] = value
    return mapping


def _pick(mapping, *names):
    for name in names:
        value = mapping.get(name)
        if value is not None and value != "":
            return value
    return None


def _fmt_reset(value):
    if value is None or value == "":
        return "-"
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return str(value)
    if number <= 0:
        return "-"
    if number < 1_000_000_000:
        seconds = int(number)
        hours, remainder = divmod(seconds, 3600)
        minutes = remainder // 60
        if hours:
            return "+%dh%dm" % (hours, minutes)
        return "+%dm" % minutes
    if number > 1_000_000_000_000:
        number = number / 1000.0
    try:
        moment = datetime.datetime.fromtimestamp(number).astimezone()
    except (OverflowError, OSError, ValueError):
        return str(value)
    return moment.strftime("%Y-%m-%d %H:%M")


def _fmt_pct(value):
    if value is None or value == "":
        return "-"
    text = str(value).strip()
    if text.endswith("%"):
        return text
    return text + "%"


def _management_get(host, port, secret):
    url = "http://%s:%s/v0/management/auth-files" % (host, port)
    return _http_json("GET", url, headers={"Authorization": "Bearer %s" % secret}, timeout=20)


def _extract_files(payload):
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("files", "auth_files", "authFiles", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = _extract_files(value)
                if nested:
                    return nested
    return []
