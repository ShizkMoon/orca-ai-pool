"""破甲通道（jailbreak）manifest 的加载、校验、编译与模型名派生。"""

import fnmatch
import hashlib
from pathlib import Path

import yaml

from .console import PoolctlError
from .constants import (
    CLIENT_COMPAT_ALIASES,
    CLIENT_COMPAT_BASE_ALIAS,
    DEVIN_MODELS,
    GOAT_MODELS,
    HIDE_PLAIN_ALIASES,
    JAILBREAK_DOMAINS,
    JAILBREAK_FALLBACKS,
    JAILBREAK_MANIFEST,
    JAILBREAK_MODES,
    JAILBREAK_POSITIONS,
    MAX_JAILBREAK_PROMPT_BYTES,
    MAX_JAILBREAK_REMINDER_BYTES,
    OC2API_GO_MODELS,
)

def _load_jailbreak_raw(root):
    path = Path(root) / JAILBREAK_MANIFEST
    if not path.exists():
        raise PoolctlError(
            "未找到 %s\n破甲通道开启后需要提示词清单；请先执行: "
            "python3 scripts/poolctl.py sync-jailbreaks" % path
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PoolctlError("%s YAML 解析失败: %s" % (path, exc))
    if not isinstance(raw, dict):
        raise PoolctlError("%s 顶层必须是映射(dict)" % path)
    return raw


def _prompt_path(prompts_dir, file_rel, label):
    file_rel = str(file_rel or "").strip()
    if not file_rel:
        raise PoolctlError("%s 缺少 file" % label)
    rel = Path(file_rel)
    if rel.is_absolute() or ".." in rel.parts:
        raise PoolctlError("%s.file 必须是 prompts/ 下的相对路径，当前值: %r" % (label, file_rel))
    path = (prompts_dir / rel).resolve()
    try:
        path.relative_to(prompts_dir.resolve())
    except ValueError:
        raise PoolctlError("%s.file 越出 prompts/ 目录: %r" % (label, file_rel))
    return path


def _reject_unknown_keys(mapping, allowed, label):
    unknown = sorted(str(key) for key in mapping if key not in allowed)
    if unknown:
        raise PoolctlError("%s 包含未知字段: %s" % (label, ", ".join(unknown)))


def _read_jailbreak_text(path, label, max_bytes=MAX_JAILBREAK_PROMPT_BYTES):
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise PoolctlError("%s 不是合法 UTF-8: %s" % (label, path))
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise PoolctlError("%s 为空: %s" % (label, path))
    size = len(text.encode("utf-8"))
    if size > max_bytes:
        raise PoolctlError(
            "%s 超过 %d bytes 上限: %s (%d bytes)"
            % (label, max_bytes, path, size)
        )
    return text


def _validate_jailbreak_suffix(value, label):
    suffix = str(value or "").strip()
    if not suffix or any(char.isspace() for char in suffix):
        raise PoolctlError("%s 不能为空且不能含空白，当前值: %r" % (label, suffix))
    return suffix


def _jailbreak_profile_suffix(profile, default_suffix):
    return str(profile.get("suffix") or default_suffix).strip()


def load_jailbreak_config(root, channel):
    """把 accounts.yaml 的开关 + prompts/manifest.yaml 编译成插件配置。

    关闭时返回 None；开启但提示词缺失时抛 PoolctlError，避免破甲模型
    静默退化成普通模型。
    """
    if channel is None:
        channel = {}
    if not isinstance(channel, dict):
        raise PoolctlError("accounts.yaml 的 jailbreak_channel 必须是映射(dict)")
    if not channel.get("enabled"):
        return None

    raw = _load_jailbreak_raw(root)
    _reject_unknown_keys(
        raw,
        {
            "version",
            "suffix",
            "unmapped",
            "default_mode",
            "default_position",
            "shared_overlay",
            "source",
            "profiles",
        },
        JAILBREAK_MANIFEST,
    )
    version = raw.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise PoolctlError("%s 的 version 只支持 1，当前值: %r" % (JAILBREAK_MANIFEST, version))

    suffix = _validate_jailbreak_suffix(raw.get("suffix") or "-jailbreak", "suffix")
    unmapped = str(raw.get("unmapped") or "error").strip().lower()
    default_mode = str(raw.get("default_mode") or "prepend").strip().lower()
    default_position = str(raw.get("default_position") or "system").strip().lower()
    if unmapped not in ("error", "pass"):
        raise PoolctlError("%s 的 unmapped 只能是 error 或 pass" % JAILBREAK_MANIFEST)
    if default_mode not in JAILBREAK_MODES:
        raise PoolctlError(
            "%s 的 default_mode 必须是 %s，当前值: %r"
            % (JAILBREAK_MANIFEST, " / ".join(JAILBREAK_MODES), default_mode)
        )
    if default_position not in JAILBREAK_POSITIONS:
        raise PoolctlError(
            "%s 的 default_position 必须是 %s，当前值: %r"
            % (JAILBREAK_MANIFEST, " / ".join(JAILBREAK_POSITIONS), default_position)
        )

    profiles_raw = raw.get("profiles")
    if not isinstance(profiles_raw, list) or not profiles_raw:
        raise PoolctlError("%s 的 profiles 必须是非空列表" % JAILBREAK_MANIFEST)

    prompts_dir = Path(root) / "prompts"
    shared_overlay_rel = str(raw.get("shared_overlay") or "").strip()
    shared_overlay = ""
    if shared_overlay_rel:
        shared_path = _prompt_path(prompts_dir, shared_overlay_rel, "shared_overlay")
        if not shared_path.exists():
            raise PoolctlError("破甲 shared_overlay 缺失: %s" % shared_path)
        shared_overlay = _read_jailbreak_text(shared_path, "shared_overlay")

    compiled = []
    seen_names = set()
    for index, item in enumerate(profiles_raw, 1):
        label = "profiles[%d]" % index
        if not isinstance(item, dict):
            raise PoolctlError("%s 必须是映射(dict)" % label)
        _reject_unknown_keys(
            item,
            {
                "name",
                "description",
                "enabled",
                "models",
                "file",
                "source_file",
                "overlay",
                "overlay_position",
                "mode",
                "position",
                "position_fallback",
                "depth",
                "reminder",
                "reminder_position",
                "reminder_depth",
                "reminder_mode",
                "suffix",
                "content_domain",
                "adult_only",
                "sha256",
            },
            label,
        )
        name = str(item.get("name") or "profile-%d" % index).strip()
        if not name:
            raise PoolctlError("%s.name 不能为空" % label)
        if name in seen_names:
            raise PoolctlError("%s.name 重复: %s" % (label, name))
        seen_names.add(name)

        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise PoolctlError("%s.enabled 必须是 bool" % label)
        if not enabled:
            continue

        models_raw = item.get("models")
        if isinstance(models_raw, str):
            models_raw = [models_raw]
        if not isinstance(models_raw, list):
            raise PoolctlError("%s.models 必须是列表或字符串" % label)
        models = [str(model).strip() for model in models_raw if str(model).strip()]
        if not models:
            raise PoolctlError("%s.models 不能为空" % label)

        profile_suffix = _validate_jailbreak_suffix(
            item.get("suffix") or suffix, label + ".suffix"
        )
        content_domain = str(item.get("content_domain") or "jailbreak").strip().lower()
        if content_domain not in JAILBREAK_DOMAINS:
            raise PoolctlError(
                "%s.content_domain 只能是 %s，当前值: %r"
                % (label, " / ".join(JAILBREAK_DOMAINS), content_domain)
            )
        adult_only = item.get("adult_only", False)
        if not isinstance(adult_only, bool):
            raise PoolctlError("%s.adult_only 必须是 bool" % label)
        if content_domain == "nsfw" and not adult_only:
            raise PoolctlError("%s 是 nsfw，必须声明 adult_only: true" % label)
        if content_domain != "nsfw" and adult_only:
            raise PoolctlError("%s 不是 nsfw，不能声明 adult_only: true" % label)

        position = str(item.get("position") or default_position).strip().lower()
        if position not in JAILBREAK_POSITIONS:
            raise PoolctlError(
                "%s.position 只能是 %s，当前值: %r"
                % (label, " / ".join(JAILBREAK_POSITIONS), position)
            )
        position_fallback = str(item.get("position_fallback") or "error").strip().lower()
        if position_fallback not in JAILBREAK_FALLBACKS:
            raise PoolctlError(
                "%s.position_fallback 只能是 %s，当前值: %r"
                % (label, " / ".join(JAILBREAK_FALLBACKS), position_fallback)
            )
        depth = item.get("depth", 0)
        if isinstance(depth, bool) or not isinstance(depth, int) or depth < 0:
            raise PoolctlError("%s.depth 必须是 >= 0 的整数" % label)
        if position != "in_chat" and depth != 0:
            raise PoolctlError("%s 只有 position=in_chat 时才能设置 depth" % label)

        prompt_path = _prompt_path(prompts_dir, item.get("file"), label)
        if not prompt_path.exists():
            raise PoolctlError(
                "破甲通道提示词缺失: %s\n先执行: python3 scripts/poolctl.py sync-jailbreaks"
                % prompt_path
            )
        prompt = _read_jailbreak_text(prompt_path, label + ".file")

        overlay_parts = []
        if shared_overlay:
            overlay_parts.append(shared_overlay)
        overlay_rel = str(item.get("overlay") or "").strip()
        if overlay_rel:
            overlay_path = _prompt_path(prompts_dir, overlay_rel, label + ".overlay")
            if not overlay_path.exists():
                raise PoolctlError("破甲 prompt overlay 缺失: %s" % overlay_path)
            overlay_parts.append(_read_jailbreak_text(overlay_path, label + ".overlay"))
        if overlay_parts:
            overlay_position = str(item.get("overlay_position") or "prepend").strip().lower()
            if overlay_position not in ("prepend", "append"):
                raise PoolctlError(
                    "%s.overlay_position 只能是 prepend 或 append，当前值: %r"
                    % (label, overlay_position)
                )
            overlay = "\n\n".join(overlay_parts)
            if overlay_position == "prepend":
                prompt = overlay + "\n\n" + prompt
            else:
                prompt = prompt + "\n\n" + overlay

        mode = str(item.get("mode") or default_mode).strip().lower()
        if mode not in JAILBREAK_MODES:
            raise PoolctlError(
                "%s.mode 必须是 %s，当前值: %r"
                % (label, " / ".join(JAILBREAK_MODES), mode)
            )
        reminder = ""
        reminder_position = "post_history"
        reminder_depth = 0
        reminder_mode = "append"
        reminder_rel = str(item.get("reminder") or "").strip()
        if reminder_rel:
            reminder_path = _prompt_path(prompts_dir, reminder_rel, label + ".reminder")
            if not reminder_path.exists():
                raise PoolctlError("破甲 reminder 缺失: %s" % reminder_path)
            reminder = _read_jailbreak_text(
                reminder_path, label + ".reminder", max_bytes=MAX_JAILBREAK_REMINDER_BYTES
            )
            reminder_position = str(item.get("reminder_position") or "post_history").strip().lower()
            if reminder_position not in JAILBREAK_POSITIONS:
                raise PoolctlError(
                    "%s.reminder_position 只能是 %s，当前值: %r"
                    % (label, " / ".join(JAILBREAK_POSITIONS), reminder_position)
                )
            reminder_depth = item.get("reminder_depth", 0)
            if isinstance(reminder_depth, bool) or not isinstance(reminder_depth, int) or reminder_depth < 0:
                raise PoolctlError("%s.reminder_depth 必须是 >= 0 的整数" % label)
            if reminder_position != "in_chat" and reminder_depth != 0:
                raise PoolctlError("%s 只有 reminder_position=in_chat 时才能设置 reminder_depth" % label)
            reminder_mode = str(item.get("reminder_mode") or "append").strip().lower()
            if reminder_mode not in JAILBREAK_MODES:
                raise PoolctlError(
                    "%s.reminder_mode 必须是 %s，当前值: %r"
                    % (label, " / ".join(JAILBREAK_MODES), reminder_mode)
                )

        compiled.append(
            {
                "name": name,
                "models": models,
                "suffix": profile_suffix,
                "content_domain": content_domain,
                "adult_only": adult_only,
                "mode": mode,
                "position": position,
                "position_fallback": position_fallback,
                "depth": depth,
                "prompt": prompt,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "reminder": reminder,
                "reminder_position": reminder_position,
                "reminder_depth": reminder_depth,
                "reminder_mode": reminder_mode,
                "reminder_sha256": (
                    hashlib.sha256(reminder.encode("utf-8")).hexdigest() if reminder else ""
                ),
            }
        )

    if not compiled:
        raise PoolctlError("%s 没有启用的 profile" % JAILBREAK_MANIFEST)

    return {
        "suffix": suffix,
        "unmapped": unmapped,
        "default_position": default_position,
        "profiles": compiled,
    }




def _jailbreak_model_matches(model, profiles):
    """模型名是否命中任一破甲 profile（大小写不敏感，支持 * / ?）。"""
    candidate = str(model or "").strip().lower()
    if not candidate:
        return False
    for profile in profiles:
        for pattern in profile.get("models", []):
            if fnmatch.fnmatchcase(candidate, str(pattern).lower()):
                return True
    return False


def _jailbreak_route_names(include_goat=False):
    """返回实际会出现在路由表里的客户端模型名。"""
    names = []
    model_groups = [list(OC2API_GO_MODELS)]
    if include_goat:
        model_groups.append(list(GOAT_MODELS))
    for models in model_groups:
        for model in models:
            client_name = str(model.get("alias") or model.get("name") or "").strip()
            if client_name:
                names.append(client_name)
    names.extend(DEVIN_MODELS)
    return names


def _validate_jailbreak_routes(jailbreak, include_goat=False):
    """校验同一基座模型的多个破甲后缀不会生成冲突别名。"""
    profiles = jailbreak["profiles"]
    default_suffix = jailbreak["suffix"]
    route_names = _jailbreak_route_names(include_goat)
    base_names = {name.lower() for name in route_names}
    owners = {}
    for name in route_names:
        for profile in profiles:
            if not _jailbreak_model_matches(name, [profile]):
                continue
            suffix = _jailbreak_profile_suffix(profile, default_suffix)
            alias = name + suffix
            key = alias.lower()
            if key in base_names:
                raise PoolctlError("破甲别名与真实模型名冲突: %s" % alias)
            if key in owners and owners[key] != profile["name"]:
                raise PoolctlError(
                    "破甲别名冲突: %s 同时来自 %s 和 %s"
                    % (alias, owners[key], profile["name"])
                )
            owners[key] = profile["name"]
    for name in route_names:
        lower = name.lower()
        for profile in profiles:
            suffix = _jailbreak_profile_suffix(profile, default_suffix)
            if lower.endswith(suffix.lower()):
                raise PoolctlError(
                    "真实模型名 %s 以破甲后缀 %s 结尾，无法区分别名"
                    % (name, suffix)
                )
    return owners


def _jailbreak_openai_models(model, jailbreak):
    """给 OpenAI 兼容 model 条目按 profile 复制带破甲后缀的 alias。"""
    client_name = str(model.get("alias") or model.get("name") or "").strip()
    if not client_name:
        return []
    aliases = []
    for profile in jailbreak["profiles"]:
        if not _jailbreak_model_matches(client_name, [profile]):
            continue
        suffix = _jailbreak_profile_suffix(profile, jailbreak["suffix"])
        aliased = dict(model)
        aliased["alias"] = client_name + suffix
        aliases.append(aliased)
    return aliases


def _jailbreak_oauth_aliases(jailbreak):
    """Devin OAuth 模型的破甲别名；fork=true 保留原模型。"""
    aliases = []
    for model in DEVIN_MODELS:
        for profile in jailbreak["profiles"]:
            if not _jailbreak_model_matches(model, [profile]):
                continue
            suffix = _jailbreak_profile_suffix(profile, jailbreak["suffix"])
            aliases.append({"name": model, "alias": model + suffix, "fork": True})
    return aliases


def _hide_plain_aliases(models):
    """剔除猫名调用会被拒的裸模型别名，派生别名原样保留。"""
    hidden = {name.lower() for name in HIDE_PLAIN_ALIASES}
    result = []
    for model in models:
        client_name = str(model.get("alias") or model.get("name") or "").strip().lower()
        if client_name in hidden:
            continue
        result.append(model)
    return result


def _models_with_jailbreak(models, jailbreak):
    result = [dict(model) for model in models]
    if not jailbreak:
        return result
    for model in models:
        result.extend(_jailbreak_openai_models(model, jailbreak))
    return result


def _models_with_client_aliases(models):
    """给基座模型追加仅用于路由、不对外展示的客户端兼容别名。"""
    result = [dict(model) for model in models]
    for model in models:
        client_name = str(model.get("alias") or model.get("name") or "").strip()
        if client_name != CLIENT_COMPAT_BASE_ALIAS:
            continue
        for alias in CLIENT_COMPAT_ALIASES:
            aliased = dict(model)
            aliased["alias"] = alias
            result.append(aliased)
    return result


def _jailbreak_alias_names(jailbreak, include_goat=False):
    """实际会出现在 /v1/models 里的破甲模型名（去重、保持顺序）。"""
    names = []
    if not jailbreak:
        return names
    for model in _jailbreak_route_names(include_goat):
        for profile in jailbreak["profiles"]:
            if not _jailbreak_model_matches(model, [profile]):
                continue
            suffix = _jailbreak_profile_suffix(profile, jailbreak["suffix"])
            alias = model + suffix
            if alias not in names:
                names.append(alias)
    return names


def _public_model_entries(jailbreak, include_goat=False, include_devin=False):
    """render 出来的公开模型表：排除只在 CPA 内部用于兼容的路由别名。"""
    entries = []
    seen = set()
    hidden = {name.lower() for name in CLIENT_COMPAT_ALIASES} | {
        name.lower() for name in HIDE_PLAIN_ALIASES
    }

    def add(name, owner):
        name = str(name or "").strip()
        key = name.lower()
        if not name or key in hidden or key in seen:
            return
        seen.add(key)
        entries.append(
            {
                "created": 0,
                "id": name,
                "object": "model",
                "owned_by": owner,
            }
        )

    def add_openai_group(models, owner):
        for model in models:
            client_name = str(model.get("alias") or model.get("name") or "").strip()
            add(client_name, owner)
            if not jailbreak:
                continue
            for profile in jailbreak["profiles"]:
                if not _jailbreak_model_matches(client_name, [profile]):
                    continue
                suffix = _jailbreak_profile_suffix(profile, jailbreak["suffix"])
                add(client_name + suffix, owner)

    add_openai_group(OC2API_GO_MODELS, "opencode-go-pool")
    if include_goat:
        add_openai_group(GOAT_MODELS, "command-code-goat")
    if include_devin:
        for model in DEVIN_MODELS:
            add(model, "devin")
            if not jailbreak:
                continue
            for profile in jailbreak["profiles"]:
                if not _jailbreak_model_matches(model, [profile]):
                    continue
                suffix = _jailbreak_profile_suffix(profile, jailbreak["suffix"])
                add(model + suffix, "devin")
    return entries
