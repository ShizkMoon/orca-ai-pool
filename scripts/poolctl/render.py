"""配置渲染：先算产物（plan），再落盘并触发重载（apply）。"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from .accounts import (
    _access_key_list,
    _as_list,
    _devin_accounts,
    _env_flag,
    _section,
    _secret_problems,
    _sensitive_words,
    load_accounts,
)
from .console import PoolctlError, _banner, _emit, _info, _ok, _warn
from .constants import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_INTERNAL_PORT,
    DEVIN_EXCLUDED_MODELS,
    DEVIN_MODEL_SETTINGS,
    GOAT_MODELS,
    OC2API_GO_MODELS,
    REQUIRED_ENV_KEYS,
    SENSITIVE_DIR_MODE,
    SENSITIVE_FILE_MODE,
    YAML_HEADER,
)
from .deploy import _sync_opencode2api_container
from .fsenv import (
    _int_env,
    _require_env,
    _warn_private_file_permissions,
    atomic_write_json,
    atomic_write_text,
    load_env,
)
from .jailbreak import (
    _hide_plain_aliases,
    _jailbreak_alias_names,
    _jailbreak_oauth_aliases,
    _jailbreak_plugin_aliases,
    _models_with_client_aliases,
    _models_with_jailbreak,
    _public_model_entries,
    _validate_jailbreak_routes,
    load_jailbreak_config,
)
from .plugincheck import _check_plugin_reload
from .templates import NGINX_CONF, NGINX_SNIPPET

def _build_cliproxy_config(env, mode, words, goat_keys, go_keys, monitor, jailbreak=None):
    if jailbreak:
        _validate_jailbreak_routes(jailbreak, include_goat=bool(goat_keys))
    # DISABLE_AUTH=true（.env，测试环境用）：access.api-keys 留空 = 客户端免鉴权。
    # 管理接口仍走 CPA_ADMIN_SECRET（secret-key 置空会把 /v0/management 整个关掉）。
    # EXTRA_API_KEYS：专用 key 列表（逗号/空白分隔），与主 key 并列，权限完全相同。
    access_keys = (
        [] if _env_flag(env, "DISABLE_AUTH") else [env["MASTER_API_KEY"]] + _access_key_list(env)
    )
    config = {
        "config-version": 8,
        # 公网口由 nginx 前置，管理面才能被限流；CPA 只监听内部端口。
        # host 留空（容器内 0.0.0.0），回环限制由 docker-compose 的端口映射保证：
        #   "127.0.0.1:18317:18317"
        "server": {
            "host": "",
            "port": _int_env(
                env, "GATEWAY_INTERNAL_PORT", DEFAULT_INTERNAL_PORT
            ),
            # 0 means no limit. Keep this explicit so request-body growth does
            # not silently reintroduce a proxy-side cap.
            "max-request-body-bytes": 0,
        },
        "management": {"allow-remote": True, "secret-key": env["CPA_ADMIN_SECRET"]},
        "access": {"api-keys": access_keys},
        "routing": {
            "strategy": "weighted-round-robin",
            "session-affinity": True,
            "session-affinity-ttl": "2h",
            # CPA 默认 true（sdk/cliproxy/service_config.go:69）。显式写死：
            # 子会话（Claude Code / Codex 的 subagent）必须继承父会话的 key 绑定，
            # 否则子请求被重新路由，前缀缓存整段失效。
            "session-affinity-subagents": True,
            # quota：额度耗尽才改绑；瞬时错误（5xx/超时）保留原绑定。
            # 默认 any 会把一次抖动变成一次换号 = 一次冷缓存。
            "session-affinity-rebind-on": "quota",
            "retry": {
                "request-retry": 3,
                "max-retry-credentials": 0,
                "max-retry-interval": 30,
            },
            "cooldown": {
                "disable-cooling": False,
                "save-cooldown-status": True,
                "transient-error-cooldown-seconds": 60,
            },
        },
        "requests": {"passthrough-headers": False},
        "observability": {"usage": {"usage-statistics-enabled": True}},
        "oauth": {
            "auth-dir": "/root/.cli-proxy-api",
            "settings": {"devin": list(DEVIN_MODEL_SETTINGS)},
            "excluded-models": {"devin": list(DEVIN_EXCLUDED_MODELS)},
            "request-scoped-errors": {
                "devin": [
                    {
                        "status": 429,
                        "match": ["Reached free model rate limit", "resource_exhausted"],
                        "action": "continue-and-cooldown",
                    },
                    {
                        "status": 500,
                        "match": ["Reached free model rate limit", "resource_exhausted"],
                        "action": "continue-and-cooldown",
                    },
                ]
            },
            "providers": {"devin": {"sensitive-words": words}},
        },
        "api-keys": {
            "openai-compatibility": [
                {
                    "name": "opencode-go-pool",
                    "base-url": "http://opencode2api:8080/v1",
                    "support-prompt-cache-key": True,
                    "models": _hide_plain_aliases(
                        _models_with_jailbreak(
                            _models_with_client_aliases(OC2API_GO_MODELS), jailbreak
                        )
                    ),
                    "keys": [{"api-key": env["OC2API_SERVER_KEY"], "weight": 1}],
                }
            ]
        },
    }

    if jailbreak:
        oauth_aliases = _jailbreak_oauth_aliases(jailbreak)
        if oauth_aliases:
            config["oauth-model-alias"] = {"devin": oauth_aliases}

    if goat_keys:
        config["api-keys"]["openai-compatibility"].append(
            {
                "name": "command-code-goat",
                "base-url": "https://api.commandcode.ai/provider/v1",
                "support-prompt-cache-key": True,
                "models": _hide_plain_aliases(
                    _models_with_jailbreak(
                        _models_with_client_aliases(GOAT_MODELS), jailbreak
                    )
                ),
                "keys": [{"api-key": item["api_key"], "weight": item["weight"]} for item in goat_keys],
            }
        )

    plugin_configs = {}
    if monitor.get("enabled", True):
        # 自研 QuotaProvider+AuthProvider 插件：每个 Go key 成为 CPAMC
        # 配额管理页的一等凭据（render 生成的 opencode-go-*.json），
        # quota.fetch 按凭据单 key 查询，原生卡片渲染。
        plugin_configs["opencode-go-quota"] = {
            "enabled": True,
            "priority": 1,
            "opencode_api_base": "https://opencode.ai/zen/go/v1",
            "opencode_api_keys": list(go_keys),
            # 配额窗口是 5h/weekly/monthly 量级，60s 与 300s 的观测精度没有差别，
            # 但 4 key × 60s ≈ 5760 次/天 的 quota 请求会跟业务流量抢同一个 key。
            "refresh_seconds": 300,
        }

    if jailbreak:
        # 破甲通道：客户端请求 <原模型名><suffix>，CPA 通过上方 alias 映射回
        # 原模型/原上游；插件只负责按 RequestedModel 注入提示词。
        #
        # model_aliases：OAuth 派生别名（如 devin/swe-2-imp）不在 /v1/models 里，
        # CPA 直接路由会报 unknown provider；插件的 model.route hook 接管这些
        # 别名、改写为 oauth provider 上的真实模型名。
        plugin_configs["jailbreak-inject"] = {
            "enabled": True,
            "priority": 2,
            "suffix": jailbreak["suffix"],
            "unmapped": jailbreak["unmapped"],
            "default_position": jailbreak.get("default_position", "system"),
            "profiles": jailbreak["profiles"],
            "model_aliases": _jailbreak_plugin_aliases(jailbreak),
        }

    if plugin_configs:
        config["plugins"] = {
            "enabled": True,
            "dir": "plugins",
            "configs": plugin_configs,
        }
    return config



def _build_nginx_conf(env):
    return NGINX_CONF % {
        "port": _int_env(env, "GATEWAY_PORT", DEFAULT_GATEWAY_PORT),
        "internal_port": _int_env(
            env, "GATEWAY_INTERNAL_PORT", DEFAULT_INTERNAL_PORT
        ),
        # 管理面稳态速率（每 IP）。突发额度见 burst：UI 资源 100，管理 API 30。
        "mgmt_rate": env.get("NGINX_MGMT_RATE") or "5r/s",
        "quota_rate": env.get("NGINX_QUOTA_RATE") or "12r/m",
    }


def _build_opencode2api_config(env, go_keys, proxies):
    return {
        "listen": "0.0.0.0:8080",
        "server_keys": [env["OC2API_SERVER_KEY"]],
        "zen_keys": [],
        "go_keys": list(go_keys),
        "anonymous": False,
        "proxies": list(proxies),
        "prefer": "go",
        "upstream": {
            "zen": "https://opencode.ai/zen",
            "go": "https://opencode.ai/zen/go",
            "anonymous": "https://opencode.ai/inference",
        },
        "retry": {"max_attempts": 3, "timeout_seconds": 900},
        "models": {"refresh_seconds": 300, "protocols": {}, "free_only": False},
        "performance": {
            "max_idle_conns": 2048,
            "max_idle_conns_per_host": 256,
            "max_conns_per_host": 0,
            "idle_conn_timeout_seconds": 120,
            "connect_timeout_seconds": 5,
            "failure_cooldown_seconds": 15,
            "attempt_timeout_seconds": 0,
            # 0 = 关闭。上游挂住时一条请求会占满整个 timeout_seconds(300s) 才失败；
            # 90s 内没有首个事件就换号，比干等 300s 划算。
            "first_event_timeout_seconds": 90,
            "max_anonymous_exits": 8,
            "free_tier_block_seconds": 900,
        },
        "logging": {"level": "info", "ring_size": 2000, "dump_request_bodies": False},
        "webui": {
            "enabled": True,
            "listen": "0.0.0.0:8081",
            "username": env.get("OC2API_WEBUI_USER") or "admin",
            "password": env["OC2API_WEBUI_PASSWORD"],
            "session_ttl_minutes": 720,
        },
    }



def _is_devin_auth_file(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and str(data.get("type", "")).lower() == "devin"


def _remove_stale_auth_files(auths_dir, expected_devin, expected_go):
    auths_dir = Path(auths_dir)
    for stale in sorted(auths_dir.glob("opencode-go-*.json")):
        if stale.name in expected_go:
            continue
        try:
            stale.unlink()
            _warn("已删除过期凭据: %s" % stale.name)
        except OSError as exc:
            _warn("删除过期凭据失败 %s: %s" % (stale, exc))

    for stale in sorted(auths_dir.glob("*.json")):
        if stale.name in expected_devin:
            continue
        if stale.name.startswith("opencode-go-"):
            continue
        if not (
            stale.name.startswith("devin-")
            or _is_devin_auth_file(stale)
        ):
            continue
        try:
            stale.unlink()
            _warn(
                "已删除过期凭据: %s（不在当前 accounts.yaml 清单中）"
                % stale.name
            )
        except OSError as exc:
            _warn("删除过期凭据失败 %s: %s" % (stale, exc))


def _sync_nginx_models(source):
    """把公开模型表同步到 nginx 读取的位置；非 root / 未装 nginx 时只提示。"""
    dest = Path("/etc/nginx/orca-models.json")
    if not dest.parent.is_dir():
        return False
    text = Path(source).read_text(encoding="utf-8")
    try:
        if dest.is_file() and dest.read_text(encoding="utf-8") == text:
            return False
        atomic_write_text(dest, text, mode=0o644)
    except PermissionError:
        _warn(
            "无法写入 %s（权限不足）；部署机上请执行 "
            "sudo bash scripts/install-nginx.sh 同步公开模型表" % dest
        )
        return False
    return True


@dataclass
class RenderPlan:
    """一次 render 的纯计算产物：要写哪些文件、内容是什么。

    只读输入（accounts.yaml / .env / manifest / 提示词正文 / 插件探测），不写任何产物；
    缺 .env 等情况下仍可能有告警输出。落盘交给 :func:`apply`。
    """

    root: Path
    env: dict
    mode: str
    accounts: list
    go_keys: list
    proxies: list
    goat_keys: list
    words: list
    monitor: dict
    jailbreak: object
    cliproxy_config: dict
    cliproxy_text: str
    oc2api_config: dict
    nginx_conf: str
    models_payload: dict
    auth_files: list
    go_pool_credential: dict | None
    secret_warnings: list
    max_count: int
    pro_count: int


def plan(root):
    """从 accounts.yaml + .env 算出全部产物，不落盘。"""
    root = Path(root)
    config = load_accounts(root)
    env = load_env(root)
    _require_env(env, REQUIRED_ENV_KEYS, root)
    secret_warnings = [
        "%s %s；请执行 poolctl rotate-secret 轮换" % (key, reason)
        for key, reason in _secret_problems(env)
    ]

    devin_pool = _section(config, "devin_pool")
    mode = str(devin_pool.get("mode") or "pro-only").strip().lower()
    if mode not in ("pro-only", "max-hybrid"):
        raise PoolctlError(
            "devin_pool.mode 必须是 pro-only 或 max-hybrid，当前值: %r" % devin_pool.get("mode")
        )
    accounts = _devin_accounts(devin_pool)

    opencode_pool = _section(config, "opencode_go_pool")
    go_keys = [str(key).strip() for key in _as_list(opencode_pool.get("keys"), "opencode_go_pool.keys")]
    go_keys = [key for key in go_keys if key]
    proxies = [
        str(proxy).strip() for proxy in _as_list(opencode_pool.get("proxies"), "opencode_go_pool.proxies")
    ]
    proxies = [proxy for proxy in proxies if proxy] or ["direct"]

    goat_pool = config.get("command_code_goat_pool") or {}
    if not isinstance(goat_pool, dict):
        raise PoolctlError("accounts.yaml 的 command_code_goat_pool 必须是映射(dict)")
    goat_keys = []
    for index, item in enumerate(_as_list(goat_pool.get("keys"), "command_code_goat_pool.keys"), 1):
        if isinstance(item, str):
            api_key, weight, name = item.strip(), 1, "goat-%d" % index
        elif isinstance(item, dict):
            api_key = str(item.get("api_key") or "").strip()
            name = str(item.get("name") or "goat-%d" % index)
            weight_raw = item.get("weight", 1)
            try:
                weight = int(weight_raw if weight_raw is not None else 1)
            except (TypeError, ValueError):
                raise PoolctlError(
                    "command_code_goat_pool.keys 第 %d 项 weight 必须是整数，当前值: %r"
                    % (index, weight_raw)
                )
            if weight < 1:
                raise PoolctlError(
                    "command_code_goat_pool.keys 第 %d 项 weight 必须 >= 1" % index
                )
        else:
            raise PoolctlError("command_code_goat_pool.keys 第 %d 项必须是字符串或映射" % index)
        if not api_key:
            raise PoolctlError("command_code_goat_pool.keys 第 %d 项缺少 api_key" % index)
        goat_keys.append({"name": name, "api_key": api_key, "weight": weight})

    # --- 1. Devin 凭据 JSON ------------------------------------------------
    max_count = 0
    pro_count = 0
    auth_files = []
    for account in accounts:
        plan_lower = account["plan"].lower()
        if plan_lower == "max":
            max_count += 1
        elif plan_lower == "pro":
            pro_count += 1
        if mode == "max-hybrid" and plan_lower == "max":
            weight = account["weight"] if account["weight"] is not None else 7
        else:
            weight = account["weight"] if account["weight"] is not None else 1
        credential = {
            "type": "devin",
            "api_key": account["session_token"],
            "email": account["email"],
            "plan": account["plan"],
            "weight": weight,
        }
        if mode == "max-hybrid" and plan_lower == "pro":
            # 凭据级 excluded_models 会整表覆盖 oauth.excluded-models（不合并），
            # 所以 Pro 号要把家族前缀排除也一并带上——否则 max-hybrid 下 Pro
            # 反而会暴露全部 Devin 模型。
            credential["excluded_models"] = list(DEVIN_EXCLUDED_MODELS)
        auth_files.append(("%s.json" % account["id"], credential, account["plan"], weight))

    # opencode-go-quota 插件 AuthProvider 凭据：池综合卡一张，
    # quota.fetch 聚合所有 key（顶部综合三行 + 逐 key 分组）。
    go_pool_credential = (
        {"type": "opencode-go", "api_key": "__pool__", "name": "OpenCode Go Pool"}
        if go_keys
        else None
    )

    monitor = config.get("monitoring") or {}
    if not isinstance(monitor, dict):
        raise PoolctlError("accounts.yaml 的 monitoring 必须是映射(dict)")
    monitor.setdefault("enabled", True)
    jailbreak = load_jailbreak_config(root, config.get("jailbreak_channel"))
    if jailbreak:
        jailbreak_plugin = (
            root / "cliproxy" / "plugins" / "linux" / "amd64" / "jailbreak-inject.so"
        )
        if not jailbreak_plugin.exists():
            raise PoolctlError(
                "破甲通道已启用，但未找到 %s\n"
                "先执行: cd plugin/jailbreak-inject && make install"
                % jailbreak_plugin
            )

    # --- 2. cliproxy/config.yaml ------------------------------------------
    words = _sensitive_words(devin_pool)
    cliproxy_config = _build_cliproxy_config(
        env, mode, words, goat_keys, go_keys, monitor, jailbreak
    )
    cliproxy_text = YAML_HEADER + yaml.safe_dump(
        cliproxy_config,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    )

    # --- 3. opencode2api/config.json --------------------------------------
    oc2api_config = _build_opencode2api_config(env, go_keys, proxies)

    # --- 4. nginx 入口配置 -------------------------------------------------
    models_payload = {
        "object": "list",
        "data": _public_model_entries(
            jailbreak,
            include_goat=bool(goat_keys),
            include_devin=bool(accounts),
        ),
    }
    return RenderPlan(
        root=root,
        env=env,
        mode=mode,
        accounts=accounts,
        go_keys=go_keys,
        proxies=proxies,
        goat_keys=goat_keys,
        words=words,
        monitor=monitor,
        jailbreak=jailbreak,
        cliproxy_config=cliproxy_config,
        cliproxy_text=cliproxy_text,
        oc2api_config=oc2api_config,
        nginx_conf=_build_nginx_conf(env),
        models_payload=models_payload,
        auth_files=auth_files,
        go_pool_credential=go_pool_credential,
        secret_warnings=secret_warnings,
        max_count=max_count,
        pro_count=pro_count,
    )


def apply(render_plan):
    """把 :func:`plan` 的产物落盘，并触发容器/插件重载。"""
    root = render_plan.root
    env = render_plan.env
    mode = render_plan.mode
    accounts = render_plan.accounts
    go_keys = render_plan.go_keys
    proxies = render_plan.proxies
    goat_keys = render_plan.goat_keys
    words = render_plan.words
    jailbreak = render_plan.jailbreak

    _warn_private_file_permissions(root)
    for line in render_plan.secret_warnings:
        _warn(line)

    auths_dir = root / "cliproxy" / "auths"
    auths_dir.mkdir(
        parents=True,
        exist_ok=True,
        mode=SENSITIVE_DIR_MODE,
    )
    os.chmod(auths_dir, SENSITIVE_DIR_MODE)
    expected_names = set()
    for filename, credential, plan_label, weight in render_plan.auth_files:
        expected_names.add(filename)
        atomic_write_json(
            auths_dir / filename,
            credential,
            mode=SENSITIVE_FILE_MODE,
        )
        _ok("凭据 %s (plan=%s, weight=%d%s)"
            % (
                (auths_dir / filename).relative_to(root),
                plan_label,
                weight,
                ", 已排除付费模型" if "excluded_models" in credential else "",
            ))

    expected_g = set()
    if render_plan.go_pool_credential:
        pname = "opencode-go-pool.json"
        atomic_write_json(
            auths_dir / pname,
            render_plan.go_pool_credential,
            mode=SENSITIVE_FILE_MODE,
        )
        expected_g.add(pname)
        _ok(f"凭据 {pname} (opencode-go 综合配额)")
    _remove_stale_auth_files(auths_dir, expected_names, expected_g)

    if not accounts:
        _warn("devin_pool.accounts 为空，未生成任何 Devin 凭据")

    cliproxy_path = root / "cliproxy" / "config.yaml"
    atomic_write_text(
        cliproxy_path,
        render_plan.cliproxy_text,
        mode=SENSITIVE_FILE_MODE,
    )
    _ok("已生成 %s (config-version 8, mode=%s)" % (cliproxy_path.relative_to(root), mode))
    if jailbreak:
        _ok(
            "破甲通道已启用: 默认后缀 %s，profiles=%d（%s）"
            % (
                jailbreak["suffix"],
                len(jailbreak["profiles"]),
                " / ".join(item["name"] for item in jailbreak["profiles"]),
            )
        )
        for item in jailbreak["profiles"]:
            _info(
                "profile %s: suffix=%s domain=%s position=%s mode=%s sha256=%s"
                % (
                    item["name"],
                    item["suffix"],
                    item["content_domain"],
                    item["position"],
                    item["mode"],
                    item["prompt_sha256"][:12],
                )
            )
            if item.get("reminder"):
                _info(
                    "  reminder: position=%s mode=%s bytes=%d sha256=%s"
                    % (
                        item["reminder_position"],
                        item["reminder_mode"],
                        len(item["reminder"].encode("utf-8")),
                        item["reminder_sha256"][:12],
                    )
                )
        _info(
            "新增模型别名: %s"
            % ", ".join(_jailbreak_alias_names(jailbreak, include_goat=bool(goat_keys)))
        )
    else:
        _info("破甲通道未启用（accounts.yaml 的 jailbreak_channel.enabled=false）")

    oc2api_path = root / "opencode2api" / "config.json"
    atomic_write_json(
        oc2api_path,
        render_plan.oc2api_config,
        mode=SENSITIVE_FILE_MODE,
    )
    _ok("已生成 %s (go_keys=%d, proxies=%d)" % (oc2api_path.relative_to(root), len(go_keys), len(proxies)))

    nginx_dir = root / "nginx" / "conf.d"
    nginx_dir.mkdir(parents=True, exist_ok=True)
    nginx_path = nginx_dir / "orca.conf"
    atomic_write_text(nginx_path, render_plan.nginx_conf)
    models_path = root / "nginx" / "models.json"
    atomic_write_json(
        models_path,
        render_plan.models_payload,
    )
    if _sync_nginx_models(models_path):
        _ok("已同步 /etc/nginx/orca-models.json")
    snippet_dir = root / "nginx" / "snippets"
    snippet_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(snippet_dir / "orca-proxy.conf", NGINX_SNIPPET)
    _ok(
        "已生成 %s / models.json (公网 :%s + TLS 443 → CPA :%s，仅管理面限流)"
        % (
            nginx_path.relative_to(root),
            env.get("GATEWAY_PORT") or DEFAULT_GATEWAY_PORT,
            env.get("GATEWAY_INTERNAL_PORT") or DEFAULT_INTERNAL_PORT,
        )
    )

    if len(go_keys) > 5 and proxies == ["direct"]:
        _warn(
            "OpenCode Go key 数 %d > 5 且 proxies 仍为 [\"direct\"]："
            "建议在 accounts.yaml 的 opencode_go_pool.proxies 配置 2~3 个静态 SOCKS5/HTTP 代理以防封号"
            % len(go_keys)
        )
    if goat_keys:
        _info("command-code-goat 上游组已启用，key 数 %d" % len(goat_keys))
    else:
        _info("command_code_goat_pool 为空，已省略 command-code-goat 上游组")

    _emit("")
    _info(
        "汇总: Devin 账号 %d（Pro %d / Max %d），模式 %s"
        % (len(accounts), render_plan.pro_count, render_plan.max_count, mode)
    )
    if mode == "max-hybrid" and render_plan.max_count == 0:
        _warn("max-hybrid 模式下没有 Max 号，所有流量仍会落在 Pro 号上")
    if words:
        _info("Devin 敏感词混淆已启用，词条数 %d" % len(words))
    else:
        _info("Devin 敏感词混淆未启用（sensitive_words.enabled=false）")

    _sync_opencode2api_container(root, oc2api_path, env)
    _check_plugin_reload(root, render_plan.cliproxy_config)
    return 0


def render(root):
    """从 accounts.yaml + .env 渲染三处产物。返回 0 表示成功。"""
    _banner("poolctl render")
    return apply(plan(root))
