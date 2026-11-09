"""标准库 HTTP 辅助：JSON 请求、cookie 提取、嵌套键查找。"""

import json
import urllib.error
import urllib.parse
import urllib.request

def _http_request(method, url, headers=None, payload=None, timeout=30):
    """底层请求，返回 (status, raw_bytes, response_headers)。网络异常 status=0。"""
    request_headers = {"Accept": "application/json", "User-Agent": "poolctl/1.0"}
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if headers:
        for key, value in headers.items():
            if value is not None:
                request_headers[str(key)] = str(value)
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except Exception:
            raw = b""
        return exc.code, raw, exc.headers
    except Exception as exc:  # URLError / timeout / ssl 等
        return 0, ("%s: %s" % (type(exc).__name__, exc)).encode("utf-8", "replace"), None


def _http_json(method, url, headers=None, payload=None, timeout=30):
    """返回 (status, json_or_text)；HTTPError 不抛异常，状态码与 body 一并返回。"""
    status, raw, _headers = _http_request(method, url, headers, payload, timeout)
    text = raw.decode("utf-8", "replace") if raw else ""
    if not text:
        return status, ""
    try:
        return status, json.loads(text)
    except (ValueError, TypeError):
        return status, text


def _cookies_from_headers(response_headers):
    if response_headers is None:
        return ""
    try:
        raw_headers = response_headers.get_all("Set-Cookie") or []
    except AttributeError:
        single = response_headers.get("Set-Cookie")
        raw_headers = [single] if single else []
    pairs = []
    for item in raw_headers:
        first = str(item).split(";")[0].strip()
        if first and "=" in first:
            pairs.append(first)
    return "; ".join(pairs)


def _find_nested_key(obj, candidates, depth=0):
    if depth > 3 or not isinstance(obj, dict):
        return None
    for candidate in candidates:
        if candidate in obj and obj[candidate]:
            return obj[candidate]
    for value in obj.values():
        if isinstance(value, dict):
            found = _find_nested_key(value, candidates, depth + 1)
            if found:
                return found
    return None
