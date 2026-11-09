"""argparse 定义与子命令分发。"""

import argparse
import sys

from .commands.check_opencode import check_opencode
from .commands.export_client import export_client
from .commands.quota import quota
from .commands.rotate_secret import rotate_secret
from .commands.switch_mode import switch_mode
from .commands.sync_jailbreaks import sync_jailbreaks
from .console import PoolctlError, _emit, _err
from .fsenv import repo_root
from .render import render

def build_parser():
    parser = argparse.ArgumentParser(
        prog="poolctl.py",
        description="orca-ai-pool 运维 CLI（渲染配置 / 账号体检 / 配额查看 / 模式切换 / 提示词同步 / 客户端导出）",
    )
    parser.add_argument(
        "--repo",
        metavar="DIR",
        default=None,
        help="仓库根目录（默认取脚本所在目录的上级目录）",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    render_parser = subparsers.add_parser("render", help="从 accounts.yaml + .env 渲染全部配置")
    render_parser.add_argument("--repo", metavar="DIR", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    render_parser.set_defaults(handler="render")

    check_parser = subparsers.add_parser("check-opencode", help="遍历 OpenCode Go key 做可用性/403 预检")
    check_parser.add_argument("--repo", metavar="DIR", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    check_parser.set_defaults(handler="check-opencode")

    quota_parser = subparsers.add_parser("quota", help="查看 Devin 实时配额与执行池额度估算")
    quota_parser.add_argument("--repo", metavar="DIR", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    quota_parser.set_defaults(handler="quota")

    switch_parser = subparsers.add_parser("switch-mode", help="切换 devin_pool.mode 并自动 render")
    switch_parser.add_argument("mode", choices=["pro-only", "max-hybrid"], help="目标模式")
    switch_parser.add_argument("--force", action="store_true", help="max-hybrid 缺少 Max 号时强制切换")
    switch_parser.add_argument("--repo", metavar="DIR", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    switch_parser.set_defaults(handler="switch-mode")

    rotate_parser = subparsers.add_parser("rotate-secret", help="生成强随机密钥写回 .env（不动运行时）")
    rotate_parser.add_argument(
        "which",
        nargs="?",
        default=None,
        choices=["all", "admin", "master", "server", "webui"],
        help="轮换哪个密钥（all/admin/master/server/webui），默认 all；单独配 --extra 时默认只追加专用 key",
    )
    rotate_parser.add_argument(
        "--extra",
        action="store_true",
        dest="rotate_extra",
        help="额外签发一把专用访问 key，追加到 .env 的 EXTRA_API_KEYS（不动主 key）",
    )
    rotate_parser.add_argument("--repo", metavar="DIR", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    rotate_parser.set_defaults(handler="rotate-secret")

    export_parser = subparsers.add_parser("export-client", help="导出本地开发机环境变量与模型路由建议")
    export_parser.add_argument("--repo", metavar="DIR", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    export_parser.set_defaults(handler="export-client")

    sync_parser = subparsers.add_parser("sync-jailbreaks", help="按固定 commit 下载破甲通道提示词")
    sync_parser.add_argument("--force", action="store_true", help="覆盖本地已存在的提示词文件")
    sync_parser.add_argument("--repo", metavar="DIR", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sync_parser.set_defaults(handler="sync-jailbreaks")

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return 2

    root = repo_root(args.repo)
    try:
        if args.handler == "render":
            return render(root)
        if args.handler == "check-opencode":
            return check_opencode(root)
        if args.handler == "quota":
            return quota(root)
        if args.handler == "switch-mode":
            return switch_mode(root, args.mode, force=args.force)
        if args.handler == "rotate-secret":
            return rotate_secret(root, args.which, rotate_extra=args.rotate_extra)
        if args.handler == "export-client":
            return export_client(root)
        if args.handler == "sync-jailbreaks":
            return sync_jailbreaks(root, force=args.force)
    except PoolctlError as exc:
        _err(str(exc))
        return 2
    except KeyboardInterrupt:
        _emit("")
        _err("已中断")
        return 130
    _err("未知子命令: %s" % args.handler)
    return 2


if __name__ == "__main__":
    sys.exit(main())
