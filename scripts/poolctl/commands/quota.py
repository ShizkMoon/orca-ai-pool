"""quota：查看 Devin 实时配额与执行池额度估算。"""

import datetime
import json

from ..accounts import _as_list, _section, load_accounts
from ..console import _banner, _emit, _err, _info, _short, _warn, print_table
from ..constants import (
    GO_POOL_5H_USD,
    GO_POOL_MONTHLY_USD,
    GO_POOL_WEEKLY_USD,
    GOAT_5H_USD,
    GOAT_MONTHLY_USD,
    GOAT_WEEKLY_USD,
)
from ..fsenv import _env_secret, _gateway, load_env
from ..httpx import _http_json
from .quota_fmt import (
    _extract_files,
    _fmt_pct,
    _fmt_reset,
    _management_get,
    _pick,
    _signals_map,
)

def quota(root):
    _banner("poolctl quota")
    config = load_accounts(root)
    env = load_env(root)
    host, port = _gateway(env)
    secret = _env_secret(env, "CPA_ADMIN_SECRET", root)

    devin_rows = []
    latest_files = []
    status, payload = _management_get(host, port, secret)
    management_ok = status == 200
    if status == 0:
        _err("管理接口不可达 http://%s:%s : %s" % (host, port, _short(payload, 200)))
        _err("请确认 CLIProxyAPI (CPA) 容器已运行，且 GATEWAY_HOST/GATEWAY_PORT 配置正确")
    elif status != 200:
        raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        _err("管理接口返回 HTTP %s: %s" % (status, _short(raw, 300)))
        _err("请确认 CPA_ADMIN_SECRET 与管理接口配置一致，且 CPA 已运行")
    else:
        files = _extract_files(payload)
        latest_files = files
        devin_files = [item for item in files if str(item.get("type") or "").lower() == "devin"]
        if not devin_files:
            _info("CPA 中未找到 type==devin 的凭据文件")
        if devin_files:
            for item in devin_files:
                auth_index = (
                    item.get("auth_index")
                    or item.get("authIndex")
                    or item.get("index")
                    or item.get("id")
                    or item.get("name")
                )
                if auth_index is None:
                    _warn("凭据 %r 缺少 auth_index，跳过 refresh" % item.get("name"))
                    continue
                refresh_status, refresh_payload = _http_json(
                    "POST",
                    "http://%s:%s/v0/management/auth-files/refresh" % (host, port),
                    headers={"Authorization": "Bearer %s" % secret},
                    payload={"auth_index": auth_index, "name": item.get("name") or auth_index},
                    timeout=45,
                )
                if refresh_status != 200:
                    raw = (
                        refresh_payload
                        if isinstance(refresh_payload, str)
                        else json.dumps(refresh_payload, ensure_ascii=False)
                    )
                    _warn(
                        "刷新 %r 失败 HTTP %s: %s"
                        % (item.get("name"), refresh_status, _short(raw, 200))
                    )

            status, payload = _management_get(host, port, secret)
            if status == 200:
                files = _extract_files(payload)
                latest_files = files
                devin_files = [
                    item
                    for item in files
                    if str(item.get("type") or "").lower() == "devin"
                ]
                for item in devin_files:
                    signals = _signals_map(item)
                    devin_rows.append(
                        [
                            item.get("name") or item.get("id") or "-",
                            item.get("email") or "-",
                            item.get("plan") or "-",
                            _fmt_pct(_pick(signals, "daily_quota_remaining_percent")),
                            _fmt_reset(
                                _pick(
                                    signals,
                                    "daily_quota_reset_at",
                                    "daily_quota_reset_at_unix",
                                )
                            ),
                            _fmt_pct(_pick(signals, "weekly_quota_remaining_percent")),
                            _fmt_reset(
                                _pick(
                                    signals,
                                    "weekly_quota_reset_at",
                                    "weekly_quota_reset_at_unix",
                                )
                            ),
                        ]
                    )
            else:
                _err("刷新后重新读取列表失败 HTTP %s" % status)

    print_table(
        "Devin 号池实时配额（CPA /v0/management/auth-files）",
        [
            "name",
            "email",
            "plan",
            "daily_quota_remaining_percent",
            "daily_quota_reset_at",
            "weekly_quota_remaining_percent",
            "weekly_quota_reset_at",
        ],
        devin_rows,
    )

    # OpenCode Go 估算
    pool = _section(config, "opencode_go_pool")
    go_keys = [str(key).strip() for key in _as_list(pool.get("keys"), "opencode_go_pool.keys")]
    go_count = len([key for key in go_keys if key])
    goat_pool = config.get("command_code_goat_pool") or {}
    if not isinstance(goat_pool, dict):
        goat_pool = {}
    goat_count = len(_as_list(goat_pool.get("keys"), "command_code_goat_pool.keys"))

    # OpenCode Go 实时余量（自研 opencode-go-quota 插件，逐凭据 quota.fetch）
    usage_rows = []
    go_auths = []
    for item in latest_files:
        provider = str(
            item.get("provider") or item.get("type") or ""
        ).lower()
        if provider == "opencode-go":
            go_auths.append(item)
    for entry in go_auths:
        fname = entry.get("name") or entry.get("id") or "-"
        auth_index = entry.get("auth_index")
        if not auth_index:
            continue
        q_status, q_payload = _http_json(
            "POST",
            "http://%s:%s/v0/management/plugins/opencode-go-quota/quota" % (host, port),
            headers={"Authorization": "Bearer %s" % secret},
            payload={"auth_index": auth_index, "provider": "opencode-go", "auth_id": fname},
            timeout=60,
        )

        def _bucket(buckets, *wnames):
            for b in _as_list(buckets, ",".join(wnames)):
                if isinstance(b, dict) and b.get("window") in wnames:
                    frac = b.get("remainingFraction")
                    if frac is None or frac == "":
                        return "-", "-"
                    try:
                        pct_text = "%.1f%%" % (float(frac) * 100)
                    except (TypeError, ValueError):
                        return "-", "-"
                    reset_at = b.get("resetTime")
                    secs = None
                    if reset_at:
                        try:
                            dt = datetime.datetime.fromisoformat(str(reset_at).replace("Z", "+00:00"))
                            secs = max(0, int((dt - datetime.datetime.now(dt.tzinfo)).total_seconds()))
                        except ValueError:
                            pass
                    return pct_text, _fmt_reset(secs)
            return "-", "-"

        def _append_usage_row(label, state, buckets):
            roll_pct, roll_reset = _bucket(buckets, "5h", "rolling")
            week_pct, week_reset = _bucket(buckets, "weekly")
            month_pct, month_reset = _bucket(buckets, "monthly")
            usage_rows.append(
                [
                    label,
                    state,
                    roll_pct,
                    roll_reset,
                    week_pct,
                    week_reset,
                    month_pct,
                    month_reset,
                ]
            )

        if q_status == 200 and isinstance(q_payload, dict):
            groups = _as_list(q_payload.get("groups"), "groups")
            if not groups:
                _append_usage_row(fname, "empty", [])
            for group in groups:
                if not isinstance(group, dict):
                    continue
                buckets = group.get("buckets") or []
                has_status_error = any(
                    isinstance(bucket, dict)
                    and bucket.get("window") == "status"
                    for bucket in _as_list(buckets, "buckets")
                )
                _append_usage_row(
                    group.get("displayName") or fname,
                    "error" if has_status_error else "ok",
                    buckets,
                )
        else:
            state = "fail:%s" % _short(q_payload if q_status else "unreachable", 60)
            _append_usage_row(fname, state, [])
    if not go_auths:
        _warn("未发现 opencode-go 凭据（先运行 poolctl render 并重启 CPA）")

    print_table(
        "OpenCode Go 实时余量（opencode-go-quota 插件，剩余百分比 + 窗口重置倒计时）",
        [
            "key_id",
            "state",
            "rolling_5h_remaining",
            "rolling_reset",
            "weekly_remaining",
            "weekly_reset",
            "monthly_remaining",
            "monthly_reset",
        ],
        usage_rows,
    )

    print_table(
        "执行池额度估算（按单号公开额度线性累加，非实时查询）",
        ["池", "key 数", "单号月额度", "单号 5h", "单号周", "池月总计", "池 5h 总计", "池周总计"],
        [
            [
                "OpenCode Go (DS4.1F)",
                go_count,
                "$%d" % GO_POOL_MONTHLY_USD,
                "$%d" % GO_POOL_5H_USD,
                "$%d" % GO_POOL_WEEKLY_USD,
                "$%d" % (GO_POOL_MONTHLY_USD * go_count),
                "$%d" % (GO_POOL_5H_USD * go_count),
                "$%d" % (GO_POOL_WEEKLY_USD * go_count),
            ],
            [
                "Command Code GOAT (DS4.1F)",
                goat_count,
                "$%d" % GOAT_MONTHLY_USD,
                "$%d" % GOAT_5H_USD,
                "$%d" % GOAT_WEEKLY_USD,
                "$%d" % (GOAT_MONTHLY_USD * goat_count),
                "$%d" % (GOAT_5H_USD * goat_count),
                "$%d" % (GOAT_WEEKLY_USD * goat_count),
            ],
        ],
    )

    _emit("")
    _info(
        "完整可视化面板请用浏览器打开 http://%s:%s （CPAMC，使用 CPA_ADMIN_SECRET 登录）" % (host, port)
    )
    _info(
        "多账号余量: CPAMC 配额管理页 opencode-go-* 凭据卡片"
    )
    return 0 if management_ok else 1
