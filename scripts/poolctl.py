#!/usr/bin/env python3
"""orca-ai-pool 运维 CLI 门面。

实现位于 ``scripts/poolctl/`` 包内，这里只负责把仓库根放进 ``sys.path``、
再转交给 :func:`scripts.poolctl.__main__.main`。

两种调用方式等价::

    python3 scripts/poolctl.py render
    python3 -m scripts.poolctl render

子命令: render | check-opencode | quota | switch-mode | rotate-secret |
        sync-jailbreaks | export-client
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.poolctl.__main__ import main  # noqa: E402
from scripts.poolctl.console import PoolctlError  # noqa: E402
from scripts.poolctl.jailbreak import (  # noqa: E402
    _jailbreak_alias_names,
    _jailbreak_model_matches,
    load_jailbreak_config,
)
from scripts.poolctl.render import _build_cliproxy_config  # noqa: E402

__all__ = [
    "PoolctlError",
    "_build_cliproxy_config",
    "_jailbreak_alias_names",
    "_jailbreak_model_matches",
    "load_jailbreak_config",
    "main",
]

if __name__ == "__main__":
    sys.exit(main())
