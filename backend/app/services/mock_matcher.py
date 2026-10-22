"""Mock 匹配内核：主机、接口地址与路径匹配的唯一实现。

调用方：
- app.routes.mock_server：平台内 /api/mock/** 路由与项目级 Mock
- app.services.mock_proxy：正向代理按真实流量匹配

设计原则：地址（主机 + 路径）优先，路径兜底；所有模式语法只在这里解析一次，
避免代理与平台各写一套导致语义漂移。
"""
import re
from typing import Optional
from urllib.parse import urlsplit

MOCK_API_PREFIX = "/api/mock"


def match_host(rule_host: str, request_host: str) -> bool:
    """主机模式匹配：精确域名/IP（大小写不敏感）或 *.example.com 通配子域。

    *.example.com 只匹配任意层级子域（a.example.com、a.b.example.com），
    不匹配根域 example.com 本身。
    """
    rule = (rule_host or "").strip().lower().rstrip(".")
    host = (request_host or "").strip().lower().rstrip(".")
    if not rule or not host:
        return False
    if ":" in host:
        host = host.split(":", 1)[0]
    if rule.startswith("*."):
        suffix = rule[1:]
        return host.endswith(suffix) and len(host) > len(suffix)
    return rule == host


def compile_path_pattern(pattern: str):
    """路径模式编译为正则（配合 fullmatch 使用）。

    - 空 / *        ：匹配一切路径
    - {param}       ：单段占位符 [^/]+（与 MockConfig 语义一致）
    - 尾斜杠 /dir/  ：目录前缀语义，匹配 /dir/ 下任意深度
    - 其余字符按字面转义
    返回编译后的 Pattern；非法输入回退为匹配一切。
    """
    pattern = (pattern or "").strip()
    if not pattern or pattern == "*":
        return re.compile(r".*")

    pieces = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "{":
            j = pattern.find("}", i)
            if j != -1:
                param_name = pattern[i + 1:j]
                pieces.append(r"(?P<param_" + re.escape(param_name) + r">[^/]+)")
                i = j + 1
                continue
            pieces.append(re.escape(ch))
            i += 1
        elif ch == "*":
            pieces.append(r".*")
            i += 1
        else:
            pieces.append(re.escape(ch))
            i += 1

    regex = "".join(pieces)
    if pattern != "/" and pattern.endswith("/"):
        regex += r".*"

    try:
        return re.compile(regex)
    except re.error:
        return re.compile(r".*")


def path_matches(pattern, path: str) -> bool:
    """用 compile_path_pattern 的产物对请求路径做 fullmatch。"""
    try:
        return pattern.fullmatch(path) is not None
    except Exception:
        return False


def normalize_mock_path(path: str) -> str:
    """归一化路径：去掉 /api/mock 前缀，保证前导 /，空路径变 /。"""
    p = (path or "").strip()
    if "?" in p:
        p = p.split("?", 1)[0]
    if p.startswith(MOCK_API_PREFIX):
        p = p[len(MOCK_API_PREFIX):]
    if not p:
        return "/"
    if not p.startswith("/"):
        p = "/" + p
    return p


def _param_pattern(path: str) -> str:
    """把 /users/{id} 这类路径转成正则（{param} 单段占位），其余字面转义。"""
    pieces = []
    i = 0
    while i < len(path):
        if path[i] == "{":
            j = path.find("}", i)
            if j != -1:
                pieces.append(r"(?P<param_" + re.escape(path[i + 1:j]) + r">[^/]+)")
                i = j + 1
                continue
            pieces.append(re.escape(path[i]))
            i += 1
        else:
            pieces.append(re.escape(path[i]))
            i += 1
    return "".join(pieces)


def match_config_path(config_path: str, request_path: str, service_prefix: str = ""):
    """配置路径与请求路径匹配，返回 (是否匹配, 路径参数)。

    两侧都先去掉 /api/mock 前缀，因此以下组合等价：
    - 配置 /users/{id} <-> 请求 /users/123、/api/mock/users/123
    - 配置 /api/mock/users/{id} <-> 请求 /users/123、/api/mock/users/123

    service_prefix 非空时，请求路径还会额外尝试去掉该服务前缀后的形态，
    使配置 /update 能命中真实流量 /service/contract/update。
    """
    cfg = normalize_mock_path(config_path)
    req = normalize_mock_path(request_path)

    for candidate in path_candidates(req, service_prefix):
        try:
            found = re.fullmatch(_param_pattern(cfg), candidate)
        except re.error:
            if cfg == candidate:
                return True, {}
            continue
        if not found:
            continue
        params = {}
        for key, value in found.groupdict().items():
            if key.startswith("param_"):
                params[key[len("param_"):]] = value
        return True, params
    return False, {}


def strip_service_prefix(path: str, prefix: str) -> Optional[str]:
    """去掉服务前缀；不匹配返回 None。prefix 空表示不剥离。"""
    p = normalize_mock_path(path)
    pre = (prefix or "").strip().rstrip("/")
    if not pre:
        return None
    if not pre.startswith("/"):
        pre = "/" + pre
    if p == pre:
        return "/"
    if p.startswith(pre + "/"):
        return p[len(pre):]
    return None


def path_candidates(request_path: str, service_prefix: str = ""):
    """按固定顺序给出待匹配路径：原样路径 -> 去服务前缀后的路径。"""
    raw = normalize_mock_path(request_path)
    out = [raw]
    stripped = strip_service_prefix(raw, service_prefix)
    if stripped is not None and stripped not in out:
        out.append(stripped)
    return out


def rule_fixed_prefix(path_pattern: str) -> str:
    """从规则的路径模式里取固定前缀，用于自动回填 service_prefix。

    /contract/          -> /contract
    /service/contract/* -> /service/contract
    /users/{id}         -> /users
    空 / *              -> 空串
    """
    pattern = (path_pattern or "").strip()
    if not pattern or pattern == "*":
        return ""
    cut = len(pattern)
    for token in ("{", "*"):
        idx = pattern.find(token)
        if idx != -1:
            cut = min(cut, idx)
    prefix = pattern[:cut]
    if not prefix.endswith("/"):
        last = prefix.rfind("/")
        prefix = prefix[:last] if last > 0 else ""
    prefix = prefix.rstrip("/")
    if prefix == "":
        return ""
    return "/" + prefix.lstrip("/")


class ParsedAddress:
    """用户配置的代理拦截地址。host 为 None 表示只按路径匹配。"""

    def __init__(self, host: Optional[str], path: str):
        self.host = host
        self.path = path

    def __repr__(self) -> str:
        return "ParsedAddress(host=%r, path=%r)" % (self.host, self.path)

    def key(self):
        return (self.host or "", self.path)


def parse_address(value: str) -> Optional[ParsedAddress]:
    """解析用户配置的接口地址，支持：

    - 完整 URL       http://192.168.13.178:18896/contract/update
    - 省略协议       192.168.13.178:18896/contract/update
    - 主机 + 路径    192.168.13.178/contract/update
    - 仅路径         /contract/update 或 /api/mock/update

    端口与 query 不参与匹配（代理按主机 + 路径识别接口）。
    """
    raw = (value or "").strip()
    if not raw:
        return None

    if "://" in raw:
        split = urlsplit(raw)
        return ParsedAddress(split.hostname or None, split.path or "/")

    if raw.startswith("/"):
        return ParsedAddress(None, raw.split("?", 1)[0] or "/")

    if "/" in raw:
        host_part, _, rest = raw.partition("/")
        path = "/" + rest
        host = host_part.split(":", 1)[0] or None
        return ParsedAddress(host, path.split("?", 1)[0] or "/")

    return ParsedAddress(raw.split(":", 1)[0] or None, "/")


def match_address(address: str, request_host: str, request_path: str,
                  service_prefix: str = "") -> tuple:
    """判断请求是否命中用户配置的接口地址。

    主机：配置了主机时必须匹配（支持 *.example.com），未配置主机则只按路径。
    路径：在 path_candidates 上做 {param} 兼容匹配；/ 视为该主机全部接口。
    """
    parsed = parse_address(address)
    if parsed is None:
        return False, {}

    if parsed.host is not None and not match_host(parsed.host, request_host):
        return False, {}

    if parsed.path in ("/", ""):
        return True, {}

    # 1) 严格匹配（含规则服务前缀剥离）：用户配置的地址与请求一一对齐
    address_paths = path_candidates(parsed.path, service_prefix)
    request_paths = path_candidates(request_path, service_prefix)
    for addr_path in address_paths:
        for req_path in request_paths:
            hit, params = _match_two_paths(addr_path, req_path)
            if hit:
                return True, params

    # 2) 兜底：地址与请求携带的服务前缀写法不同（内网 /contract 与网关 /service/contract）。
    #    仅当「地址去掉前若干段后」正好是请求路径的尾部时才放行，避免 /get 之类过度匹配。
    hit, params = _match_by_suffix_alignment(parsed.path, request_paths)
    if hit:
        return True, params
    return False, {}


def _match_two_paths(addr_path: str, req_path: str) -> tuple:
    """两个具体路径按 {param} 语义比较。"""
    cfg = normalize_mock_path(addr_path)
    req = normalize_mock_path(req_path)
    try:
        found = re.fullmatch(_param_pattern(cfg), req)
    except re.error:
        return (cfg == req), {}
    if not found:
        return False, {}
    params = {}
    for key, value in found.groupdict().items():
        if key.startswith("param_"):
            params[key[len("param_"):]] = value
    return True, params


def _match_by_suffix_alignment(addr_path: str, request_paths: list) -> tuple:
    """地址路径逐段剥离前缀，看剩余部分是否与某个请求候选完全对齐。

    要求剥离后至少保留 2 段（避免 /get 这类单段过度匹配），
    且剥离掉的段必须构成请求路径的连续前缀（或为请求的前缀的一部分）。
    """
    addr = normalize_mock_path(addr_path)
    segs = [s for s in addr.split("/") if s]
    if len(segs) < 2:
        return False, {}
    for keep in range(len(segs) - 1, 1, -1):
        candidate = "/" + "/".join(segs[-keep:])
        for req_path in request_paths:
            hit, params = _match_two_paths(candidate, req_path)
            if hit:
                return True, params
    return False, {}
