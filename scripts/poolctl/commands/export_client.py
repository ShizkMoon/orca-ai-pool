"""export-client：导出本地开发机环境变量与模型路由建议。"""

import json
from pathlib import Path

from ..console import _banner, _emit, _info, _ok, print_table
from ..fsenv import _env_secret, _gateway, atomic_write_text, load_env

def export_client(root):
    _banner("poolctl export-client")
    env = load_env(root)
    host, port = _gateway(env)
    master = _env_secret(env, "MASTER_API_KEY", root)
    base = "http://%s:%s" % (host, port)

    env_sh = (
        'export OPENAI_BASE_URL="%s/v1"\n'
        'export OPENAI_API_KEY="%s"\n'
        'export ANTHROPIC_BASE_URL="%s"\n'
        'export ANTHROPIC_AUTH_TOKEN="%s"\n'
    ) % (base, master, base, master)

    out_path = Path(root) / "export" / "env.sh"
    atomic_write_text(out_path, env_sh, mode=0o600)
    _ok("已写入 %s （权限 600，含密钥，勿提交到 Git）" % out_path)

    _emit("")
    _emit("---- export/env.sh ----")
    _emit(env_sh.rstrip("\n"))
    _emit("-----------------------")
    _emit("")
    _emit("本地使用: source export/env.sh")

    print_table(
        "模型路由建议",
        ["别名", "上游", "用途"],
        [
            ["devin/swe-2", "Devin OAuth (Fast Off)", "免费主脑，日常编码首选"],
            ["devin/gemini-4-argon", "Devin OAuth", "逆向工程主力（C/C++ 重构与文档）"],
            ["devin/swe-2-lightning", "Devin OAuth (Fast On)", "付费加速，仅在赶工时使用"],
            ["deepseek-v4.1-flash", "OpenCode Go + GOAT 聚合", "执行池，批量改代码/跑测试"],
            ["deepseek-v4.1-flash-jailbreak", "同上（同一上游）", "破甲版，注入 DeepSeek 4.1 提示词"],
        ],
    )
    _info("破甲模型名以 GET /v1/models 为准；在 accounts.yaml 打开 jailbreak_channel 后出现")

    devin_config = {"subagents": {"enabled": False}, "reasoningEffort": "high", "fastMode": False}
    _emit("")
    _emit("建议的本地 ~/.config/devin/config.json :")
    _emit(json.dumps(devin_config, ensure_ascii=False, indent=2))
    _emit("")
    _info(
        "关闭 subagents 可防止 Devin 内部套娃子代理引爆免费模型 2 分钟限流；"
        "reasoningEffort=high 避开 max 档吞额度；fastMode=false 只用免费池"
    )
    _emit("写入命令: mkdir -p ~/.config/devin && cat > ~/.config/devin/config.json <<'JSON'")
    _emit(json.dumps(devin_config, ensure_ascii=False, indent=2))
    _emit("JSON")
    return 0
