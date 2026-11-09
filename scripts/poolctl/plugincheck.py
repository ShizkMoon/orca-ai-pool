"""插件漂移检测：比较 cliproxy 插件快照，判断是否需要重启容器。"""

import hashlib
import json
import shutil
import subprocess

from .console import _info, _warn
from .constants import OC2API_SERVICE
from .fsenv import atomic_write_json

# ---------------------------------------------------------------------------
# CPA 插件的“配置已改但进程没重载”检测
#
# C ABI 插件在 CPA 里是进程内 .so，plugins.configs 只在**进程启动**时被读取。
# 管理面板保存配置会触发 host.ApplyConfig → plugin.reconfigure（热生效），
# 但直接改文件走 fsnotify 重载（internal/watcher/config_reload.go）**不会**
# 调用 pluginHost.ApplyConfig，于是面板上的插件数据静默停在旧值——不报错、
# 不告警，只是少了一个 key。这里记下“进程启动时那份配置”，下次 render 比对。
# ---------------------------------------------------------------------------
PLUGIN_STATE_FILE = ".poolctl-plugin-state.json"
PLUGIN_CONFIG_IDS = ("opencode-go-quota", "jailbreak-inject")


def _plugin_snapshot(cliproxy_config):
    configs = ((cliproxy_config.get("plugins") or {}).get("configs") or {})
    snap = {}
    for pid in PLUGIN_CONFIG_IDS:
        value = configs.get(pid)
        if value is None:
            continue
        snap[pid] = hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
    return snap


def _compose_service_started_at(root, service=OC2API_SERVICE):
    """返回服务容器的启动时间字符串；拿不到就返回空串。"""
    if shutil.which("docker") is None:
        return ""
    try:
        ids = subprocess.run(
            ["docker", "compose", "ps", "-q", service],
            cwd=str(root), capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return ""
    container_id = (ids.stdout or "").strip().splitlines()
    if ids.returncode != 0 or not container_id:
        return ""
    try:
        info = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.StartedAt}}", container_id[0].strip()],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return ""
    if info.returncode != 0:
        return ""
    return (info.stdout or "").strip()


def _check_plugin_reload(root, cliproxy_config):
    """检测“插件配置已写盘、但跑着的 CPA 进程还没重新加载”。

    判据两条一起用，避免把只改了 api-keys 这类无关配置也算成插件失效：
      * 当前 plugins.configs 的哈希与进程上次加载的那份不同；
      * 且这份配置是在该进程启动之后才写下的。

    提醒时不回写状态，所以重启之前每次 render 都会继续提醒。
    """
    state_path = root / PLUGIN_STATE_FILE
    current = _plugin_snapshot(cliproxy_config)
    if not current:
        return
    started_at = _compose_service_started_at(root, "cliproxyapi")
    try:
        previous = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    previous_hashes = previous.get("hashes") if isinstance(previous, dict) else None
    if not isinstance(previous_hashes, dict):
        previous_hashes = {}
    previous_start = previous.get("started_at") if isinstance(previous, dict) else None

    def remember(start):
        atomic_write_json(state_path, {"hashes": current, "started_at": start})

    if not started_at:
        # CPA 没在跑：写下的配置就是下次启动会加载的那份，直接记账。
        remember("")
        return

    changed = [pid for pid, digest in current.items() if previous_hashes.get(pid) != digest]
    if changed and previous_start == started_at:
        # 同一个进程，配置却变了 —— 这就是 C ABI 插件不重载的现场。
        _warn(
            "CPA 插件配置已变更但进程未重载：%s。C ABI 插件只在进程启动时读取 "
            "plugins.configs；继续下去面板会静默显示旧数据。" % "、".join(changed)
        )
        _info("  执行 docker compose restart cliproxyapi 使其生效（挑空闲窗口重启，不影响在途流）")
        return
    remember(started_at)


# ---------------------------------------------------------------------------
# check-opencode
# ---------------------------------------------------------------------------
