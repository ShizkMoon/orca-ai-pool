"""控制台输出：PoolctlError 与 _emit/_info/_ok/_warn/_err、表格排版。"""

import os
import sys
import unicodedata

class PoolctlError(Exception):
    """可预期的用户侧错误（打印后以非零码退出，不打印堆栈）。"""


# ---------------------------------------------------------------------------
# 终端输出（纯 ASCII / 中文友好，不依赖第三方库）
# ---------------------------------------------------------------------------


def _emit(text=""):
    try:
        print(text, flush=True)
    except BrokenPipeError:
        # 下游管道被截断（如 `| head`）：静默退出，避免 Python 解释器再报 BrokenPipeError
        try:
            sys.stdout.close()
        except Exception:
            pass
        os._exit(0)


def _banner(title):
    _emit("")
    _emit("=== %s ===" % title)


def _info(text):
    _emit("[info] %s" % text)


def _ok(text):
    _emit("[ ok ] %s" % text)


def _warn(text):
    _emit("[warn] %s" % text)


def _err(text):
    _emit("[fail] %s" % text)


def _display_width(text):
    """计算终端显示宽度（CJK 全角字符按 2 列）。"""
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def _pad(text, width):
    text = "" if text is None else str(text)
    return text + " " * max(0, width - _display_width(text))


def print_table(title, headers, rows):
    """打印简单对齐表格（ASCII 边框，兼容中文）。"""
    if title:
        _emit("")
        _emit(str(title))
    headers = [str(h) for h in headers]
    norm_rows = [["" if c is None else str(c) for c in row] for row in rows]
    if not norm_rows:
        _emit("(无数据)")
        return
    widths = [_display_width(h) for h in headers]
    for row in norm_rows:
        for index, cell in enumerate(row[: len(widths)]):
            widths[index] = max(widths[index], _display_width(cell))
    separator = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    _emit(separator)
    _emit("| " + " | ".join(_pad(h, widths[i]) for i, h in enumerate(headers)) + " |")
    _emit(separator)
    for row in norm_rows:
        cells = row[: len(widths)] + [""] * (len(widths) - len(row[: len(widths)]))
        _emit("| " + " | ".join(_pad(c, widths[i]) for i, c in enumerate(cells)) + " |")
    _emit(separator)


def _short(text, limit=300):
    text = "" if text is None else str(text)
    text = text.replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "...(truncated)"


def _mask(secret):
    secret = str(secret or "")
    if len(secret) <= 10:
        return secret[:2] + "***"
    return secret[:6] + "***" + secret[-2:]
