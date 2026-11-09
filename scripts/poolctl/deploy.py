"""容器与进程编排：docker compose 调用、WebUI 重载、容器内配置同步。"""

import json
import shutil
import subprocess
from pathlib import Path

from .console import _info, _ok, _short, _warn
from .constants import (
    OC2API_CONTAINER_CONFIG_PATH,
    OC2API_SERVICE,
    OC2API_WEBUI_URL,
)
from .httpx import _cookies_from_headers, _find_nested_key, _http_request

def _compose_service_running(root, service=OC2API_SERVICE):
    """判断 docker compose 服务是否在运行（失败一律返回 False）。"""
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    text = (result.stdout or "").strip()
    entries = []
    if text:
        try:
            parsed = json.loads(text)
            entries = parsed if isinstance(parsed, list) else [parsed]
        except ValueError:
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        label = " ".join(str(entry.get(key) or "") for key in ("Service", "Name", "Names", "ID"))
        if service not in label:
            continue
        state = str(entry.get("State") or entry.get("Status") or "").lower()
        if not state or "run" in state or "up" in state:
            return True
    try:
        fallback = subprocess.run(
            ["docker", "compose", "ps", "-q", service],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return fallback.returncode == 0 and bool((fallback.stdout or "").strip())
    except Exception:
        return False


def _webui_reload(env):
    base = str(env.get("OC2API_WEBUI_URL") or OC2API_WEBUI_URL).rstrip("/")
    username = env.get("OC2API_WEBUI_USER") or "admin"
    password = env.get("OC2API_WEBUI_PASSWORD") or ""
    status, raw, response_headers = _http_request(
        "POST",
        base + "/api/auth/login",
        payload={"username": username, "password": password},
        timeout=15,
    )
    text = raw.decode("utf-8", "replace") if raw else ""
    if status == 0:
        raise RuntimeError("无法连接 WebUI %s: %s" % (base, _short(text, 200)))
    if status >= 400:
        raise RuntimeError("WebUI 登录失败 HTTP %s: %s" % (status, _short(text, 200)))
    cookie = _cookies_from_headers(response_headers)
    body = None
    try:
        body = json.loads(text) if text else {}
    except ValueError:
        body = {}
    csrf = _find_nested_key(body if isinstance(body, dict) else {}, ["csrf", "csrf_token", "csrfToken"])
    headers = {}
    if cookie:
        headers["Cookie"] = cookie
    if csrf:
        headers["X-CSRF-Token"] = str(csrf)
    status, raw, _headers = _http_request(
        "POST", base + "/api/config/reload", headers=headers, payload={}, timeout=30
    )
    text = raw.decode("utf-8", "replace") if raw else ""
    if status == 0:
        raise RuntimeError("config/reload 请求失败: %s" % _short(text, 200))
    if status >= 400:
        raise RuntimeError("config/reload HTTP %s: %s" % (status, _short(text, 200)))
    _ok("WebUI 配置热重载成功 (%s/api/config/reload)" % base)


def _sync_opencode2api_container(root, config_path, env):
    root = Path(root)
    if shutil.which("docker") is None:
        _info("未检测到 docker 命令，跳过容器同步。")
        _info("部署机上请手工执行: docker compose restart opencode2api")
        return
    if not (root / "docker-compose.yml").exists():
        _info("未找到 %s，跳过容器同步。" % (root / "docker-compose.yml"))
        return
    try:
        running = _compose_service_running(root)
    except Exception as exc:
        _warn("检测容器状态失败: %s" % exc)
        running = False
    if not running:
        _info("容器 %s 未运行，跳过 docker compose cp 与热重载。" % OC2API_SERVICE)
        _info("配置已写入 %s；启动容器后请执行: docker compose restart opencode2api" % config_path)
        return
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "cp",
                "%s/config.json" % OC2API_SERVICE,
                "%s:%s" % (OC2API_SERVICE, OC2API_CONTAINER_CONFIG_PATH),
            ],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(_short((result.stderr or result.stdout or "").strip(), 300))
        _ok("已同步配置进容器: %s:%s" % (OC2API_SERVICE, OC2API_CONTAINER_CONFIG_PATH))
    except Exception as exc:
        _warn("docker compose cp 失败: %s" % exc)
        _info("请手工执行: docker compose restart opencode2api")
        return
    # docker compose cp 以 root 写入，容器内的应用进程通常不是 root；
    # 不同步属主会让 /api/config/reload 报 permission denied。
    try:
        owner = subprocess.run(
            [
                "docker", "exec", "-u", "0", OC2API_SERVICE,
                "stat", "-c", "%u:%g", "/var/lib/opencode2api",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        permission_cmd = (
            ["chown", owner, OC2API_CONTAINER_CONFIG_PATH]
            if owner
            else ["chmod", "0644", OC2API_CONTAINER_CONFIG_PATH]
        )
        fixed = subprocess.run(
            ["docker", "exec", "-u", "0", OC2API_SERVICE] + permission_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if fixed.returncode != 0:
            raise RuntimeError(_short((fixed.stderr or fixed.stdout or "").strip(), 300))
    except Exception as exc:
        _warn("修正容器内配置权限失败: %s" % exc)
        _info(
            "请手工执行: docker exec -u 0 %s chmod 0644 %s"
            % (OC2API_SERVICE, OC2API_CONTAINER_CONFIG_PATH)
        )
        return
    try:
        _webui_reload(env)
    except Exception as exc:
        _warn("WebUI 热重载失败: %s" % exc)
        _info("请手工执行: docker compose restart opencode2api")
