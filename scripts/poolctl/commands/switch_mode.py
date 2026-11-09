"""switch-mode：改写 devin_pool.mode 并重新渲染。"""

import os
import shutil
from pathlib import Path

import yaml

from ..accounts import _devin_accounts, _section, load_accounts
from ..console import PoolctlError, _banner, _emit, _info, _ok, _warn
from ..constants import SENSITIVE_FILE_MODE
from ..fsenv import atomic_write_text
from ..render import render

def switch_mode(root, target, force=False):
    _banner("poolctl switch-mode %s" % target)
    root = Path(root)
    path = root / "accounts.yaml"
    config = load_accounts(root)
    pool = _section(config, "devin_pool")
    accounts = _devin_accounts(pool)

    max_accounts = [account for account in accounts if account["plan"].lower() == "max"]
    pro_accounts = [account for account in accounts if account["plan"].lower() == "pro"]

    if target == "max-hybrid":
        if not max_accounts and not force:
            raise PoolctlError(
                "max-hybrid 模式要求至少 1 个 plan==\"Max\" 的 Devin 号；"
                "当前只有 %d 个 Pro 号。补齐 Max 号后重试，或加 --force 强制切换"
                % len(pro_accounts)
            )
        if not max_accounts:
            _warn("--force 已生效：没有任何 Max 号，付费模型仍会落在 Pro 号上")
    else:
        if max_accounts:
            _warn(
                "检测到 %d 个 Max 号：切到 pro-only 后 Max 号按 accounts 中的 weight（默认 1）参与轮询，"
                "不再独占付费模型" % len(max_accounts)
            )
        else:
            _info("当前没有 Max 号，pro-only 为纯 Pro 均衡模式")

    backup_path = path.with_name(path.name + ".bak")
    try:
        shutil.copy2(str(path), str(backup_path))
        os.chmod(backup_path, SENSITIVE_FILE_MODE)
        _ok("已备份原清单: %s" % backup_path)
    except OSError as exc:
        raise PoolctlError("备份 accounts.yaml 失败: %s" % exc)

    pool["mode"] = target
    config["devin_pool"] = pool
    text = yaml.safe_dump(config, allow_unicode=True, sort_keys=False, default_flow_style=False, width=120)
    atomic_write_text(
        path,
        text,
        mode=SENSITIVE_FILE_MODE,
    )
    _ok("accounts.yaml 的 devin_pool.mode 已更新为 %s" % target)
    _info("注意: 写回使用 yaml.safe_dump，原文件中的 YAML 注释已丢失（备份在 %s）" % backup_path.name)

    _emit("")
    _info("下一步自动执行 render ...")
    return render(root)


# ---------------------------------------------------------------------------
# export-client
# ---------------------------------------------------------------------------
