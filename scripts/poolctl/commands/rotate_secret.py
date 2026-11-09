"""rotate-secret：生成强随机密钥写回 .env（不动运行时）。"""

import re
import secrets
import shutil
import time
from pathlib import Path

from ..console import PoolctlError, _emit, _info, _ok, _warn
from ..constants import SENSITIVE_FILE_MODE
from ..fsenv import atomic_write_text

def rotate_secret(root, which, rotate_extra=False):
    """生成强随机密钥写回 .env，并备份原文件。不自动 render。

    which = all | admin | master | server | webui
    rotate_extra = True 时额外签发一把专用 key 写入 EXTRA_API_KEYS。
    """
    root = Path(root)
    env_path = root / ".env"
    if not env_path.exists():
        raise PoolctlError("未找到 %s" % env_path)
    mapping = {
        "admin": ("CPA_ADMIN_SECRET", "sk-"),
        "master": ("MASTER_API_KEY", "sk-"),
        "server": ("OC2API_SERVER_KEY", "sk-"),
        "webui": ("OC2API_WEBUI_PASSWORD", "sk-"),
    }
    targets = list(mapping) if which in (None, "all") else [which]
    if rotate_extra and which is None:
        # rotate-secret --extra 单独使用时只追加专用 key，不顺手轮换主 key 与管理/内网
        # 密钥；显式指定目标（如 master --extra）时才同时轮换。
        targets = []
    for name in targets:
        if name not in mapping:
            raise PoolctlError(
                "未知目标: %s（可选 all/admin/master/server/webui）" % name
            )

    backup = root / (".env.bak.%s" % time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(env_path, backup)

    original = env_path.read_text(encoding="utf-8")
    updated = original
    changed = []
    for name in targets:
        key, prefix = mapping[name]
        # token_hex 的 24 字节 = 48 hex 字符，熵 192 bit；前面加 sk- 保持既有格式。
        new_value = prefix + secrets.token_hex(24)
        pattern = re.compile(r"^(%s=).*$" % re.escape(key), re.MULTILINE)
        if not pattern.search(updated):
            raise PoolctlError(".env 中找不到 %s，请手工补上后重试" % key)
        updated = pattern.sub(lambda m: m.group(1) + new_value, updated)
        changed.append(key)

    extra_key = None
    if rotate_extra:
        # 专用 key 与主 key 互不覆盖：追加一条，旧 key 失效前请先确认已无客户端在用。
        extra_key = "sk-" + secrets.token_hex(24)
        pattern = re.compile(r"^(EXTRA_API_KEYS=).*$", re.MULTILINE)
        if pattern.search(updated):
            updated = pattern.sub(lambda m: m.group(1) + extra_key, updated)
        else:
            updated = updated.rstrip("\n") + "\nEXTRA_API_KEYS=%s\n" % extra_key
        changed.append("EXTRA_API_KEYS")

    atomic_write_text(
        env_path,
        updated,
        mode=SENSITIVE_FILE_MODE,
    )
    for key in changed:
        _ok("已轮换 %s（192 bit）" % key)
    if extra_key:
        _emit("")
        _emit("新专用 key: %s" % extra_key)
    _info("原文件备份: %s" % backup)
    if "OC2API_SERVER_KEY" in changed:
        _warn("内网 key 变更后需重启 cliproxyapi 与 opencode2api，否则两端认证会不一致")
    _warn("密钥已写入 .env，但配置尚未重新渲染；下一步执行 poolctl render 并重启对应服务")
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
