"""sync-jailbreaks：按固定 commit 下载破甲通道提示词。"""

import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ..console import PoolctlError, _banner, _emit, _info, _ok, _warn
from ..fsenv import atomic_write_text
from ..jailbreak import JAILBREAK_MANIFEST, _load_jailbreak_raw, _prompt_path

def sync_jailbreaks(root, force=False):
    """按 prompts/manifest.yaml 的固定 commit 下载提示词到 prompts/upstream/。"""
    root = Path(root)
    _banner("poolctl sync-jailbreaks")
    raw = _load_jailbreak_raw(root)
    source = raw.get("source") or {}
    if not isinstance(source, dict):
        raise PoolctlError("%s 的 source 必须是映射(dict)" % JAILBREAK_MANIFEST)
    repo = str(source.get("repo") or "").strip()
    ref = str(source.get("ref") or "").strip()
    base_url = str(source.get("base_url") or "https://raw.githubusercontent.com").rstrip("/")
    if not repo or not ref:
        raise PoolctlError("%s 的 source.repo / source.ref 不能为空" % JAILBREAK_MANIFEST)

    profiles = raw.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise PoolctlError("%s 的 profiles 必须是非空列表" % JAILBREAK_MANIFEST)

    prompts_dir = root / "prompts"
    written = 0
    skipped = 0
    seen_dest = set()
    for index, item in enumerate(profiles, 1):
        if not isinstance(item, dict):
            raise PoolctlError("profiles[%d] 必须是映射(dict)" % index)
        source_file = str(item.get("source_file") or "").strip()
        if not source_file:
            _info("profiles[%d] 没有 source_file，跳过" % index)
            skipped += 1
            continue
        dest = _prompt_path(prompts_dir, item.get("file"), "profiles[%d]" % index)
        if dest in seen_dest:
            _info("profiles[%d] 复用已下载文件，跳过: %s" % (index, dest.relative_to(root)))
            skipped += 1
            continue
        seen_dest.add(dest)
        url = "%s/%s/%s/%s" % (
            base_url,
            repo,
            ref,
            urllib.parse.quote(source_file),
        )
        _emit("-- [%d/%d] %s" % (index, len(profiles), source_file))
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "orca-ai-pool/poolctl"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
        except urllib.error.HTTPError as exc:
            raise PoolctlError("下载失败 HTTP %s: %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise PoolctlError("下载失败: %s (%s)" % (url, exc.reason))
        text = data.decode("utf-8")
        if not text.strip():
            raise PoolctlError("上游文件为空: %s" % url)

        if dest.exists() and not force:
            existing = dest.read_text(encoding="utf-8")
            if existing == text:
                _ok("已是最新: %s" % dest.relative_to(root))
            else:
                _warn("已存在且内容不同，保留本地文件: %s（--force 覆盖）" % dest.relative_to(root))
            skipped += 1
            continue
        atomic_write_text(dest, text)
        _ok("已写入 %s (%d bytes)" % (dest.relative_to(root), len(data)))
        written += 1

    _emit("")
    _info("来源: https://github.com/%s @ %s" % (repo, ref))
    _warn("上游仓库未声明许可证；使用与再分发前请自行确认条款。")
    _info("完成: 写入 %d，跳过 %d" % (written, skipped))
    return 0
