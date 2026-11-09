"""仓库定位、原子写入、.env 解析与取值校验。"""

import json
import os
from pathlib import Path

from .console import PoolctlError, _warn
from .constants import DEFAULT_GATEWAY_PORT

def repo_root(override=None):
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def atomic_write_text(path, text, mode=None):
    """先写 .tmp 再 os.replace 原子替换。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        if mode is not None:
            os.fchmod(handle.fileno(), mode)
        handle.write(text)
    os.replace(tmp, path)


def atomic_write_json(path, data, mode=None):
    atomic_write_text(
        path,
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        mode=mode,
    )



def parse_env_file(path):
    """.env 手写解析器：KEY=VALUE、# 注释、可选 export 前缀与引号。"""
    values = {}
    text = Path(path).read_text(encoding="utf-8").lstrip("\ufeff")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            for marker in (" #", "\t#"):
                index = value.find(marker)
                if index >= 0:
                    value = value[:index]
            value = value.strip()
        values[key] = value
    return values


def load_env(root):
    """读取 .env（缺失容错），并允许进程环境变量兜底。"""
    values = dict(os.environ)
    path = Path(root) / ".env"
    if path.exists():
        values.update(parse_env_file(path))
    else:
        _warn("未找到 %s，使用进程环境变量与默认值" % path)
    values.setdefault("GATEWAY_PORT", str(DEFAULT_GATEWAY_PORT))
    values.setdefault("OC2API_WEBUI_USER", "admin")
    return values


def _require_env(env, keys, root):
    missing = [key for key in keys if not str(env.get(key) or "").strip()]
    if missing:
        raise PoolctlError(
            "缺少必要环境变量: %s\n请检查 %s/.env（可参考 .env.example）"
            % (", ".join(missing), root)
        )


def _warn_private_file_permissions(root):
    for name in (".env", "accounts.yaml"):
        path = Path(root) / name
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            continue
        if mode & 0o077:
            _warn("%s 权限为 %04o，建议执行 chmod 600 %s" % (path, mode, name))


def _int_env(env, key, default, minimum=1, maximum=65535):
    raw = str(env.get(key) or default).strip()
    if not raw.isdigit():
        raise PoolctlError("%s 必须是整数，当前值: %r" % (key, raw))
    value = int(raw)
    if value < minimum or value > maximum:
        raise PoolctlError(
            "%s 必须在 %d..%d 之间，当前值: %d"
            % (key, minimum, maximum, value)
        )
    return value


def _gateway(env):
    host = str(env.get("GATEWAY_HOST") or "").strip()
    if not host:
        raise PoolctlError("缺少 GATEWAY_HOST，请检查 .env")
    port = _int_env(env, "GATEWAY_PORT", DEFAULT_GATEWAY_PORT)
    return host, str(port)


def _env_secret(env, key, root):
    value = str(env.get(key) or "")
    if not value.strip():
        raise PoolctlError("缺少环境变量 %s，请检查 %s/.env" % (key, root))
    return value


# ---------------------------------------------------------------------------
# HTTP 封装
# ---------------------------------------------------------------------------
