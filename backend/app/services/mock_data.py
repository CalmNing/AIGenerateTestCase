"""真实感 Mock 数据生成器。

针对接口 Mock 场景：按「字段名语义 + format + enum」生成贴近真实业务的随机数据，
而不是 _schema_example 那种 "string"/1 占位值。供 mock_server 的项目级动态 Mock 使用。

规则优先级（高 -> 低）：
1. enum / const —— 必须遵守，随机取一项
2. 有意义的 example / default（Swagger 自动填充的 "string"、"text"、空串等占位符会被忽略）
3. description 取值说明（如「奖罚类型：1奖励 2处罚」）—— 候选值限定为说明中的数字
4. 时间语义字段（字段名含 date/time、*at、create*/update*/sign*/start*/end* 开头，
   或 format 为 date/date-time）—— 统一返回毫秒级时间戳（整数）
5. format（email / uuid / uri）
6. 字段名语义（中文业务词库：公司名、人名、金额、比例、编号、文件名……）
7. 类型兜底（数字带合理范围、字符串取通用词库、数组 1~3 个元素）

每次调用都会产生新的随机值；同一接口文档更新后自动跟随。
"""
import json
import random
import re
import uuid
from datetime import datetime
from typing import Any

MAX_DEPTH = 12

# Swagger/OpenAPI 工具自动填充的占位 example，对 Mock 无意义，忽略之
_PLACEHOLDER_EXAMPLES = {"string", "text", "number", "integer", "boolean", "object", "array", "null", ""}

# ---- 中文业务词库 ------------------------------------------------------------

COMPANY_NAMES = [
    "中建三局集团有限公司", "华润置地有限公司", "中国建筑第八工程局有限公司",
    "万科企业股份有限公司", "招商局地产控股股份有限公司", "中铁十一局集团有限公司",
    "上海宝冶集团有限公司", "浙江省建工集团有限公司", "广州富力地产股份有限公司",
    "北京城建集团有限责任公司",
]

PERSON_NAMES = ["张伟明", "李建华", "王海涛", "刘志强", "陈思远", "杨光", "赵宇轩", "周文静", "吴丽娜", "郑凯"]

SECTION_NAMES = [
    "地基与基础工程", "主体结构工程", "建筑装饰装修工程", "建筑屋面工程",
    "建筑给水排水及供暖工程", "通风与空调工程", "建筑电气工程", "智能建筑工程",
    "建筑节能工程", "电梯工程",
]

CONTRACT_RESOURCE_NAMES = [
    "工程施工合同", "设备采购合同", "劳务分包合同", "材料供货合同",
    "监理服务合同", "勘察设计合同", "专业分包合同", "咨询服务合同",
]

PAYMENT_NAMES = ["预付款", "进度款", "结算款", "质保金", "履约保证金", "首付款", "尾款", "变更签证款"]

FILE_NAMES = ["施工图纸.pdf", "合同扫描件.pdf", "报价单.xlsx", "营业执照.jpg", "技术规范.docx", "验收报告.pdf"]

REMARKS = [
    "按合同约定节点支付，如有变更以双方签订的补充协议为准。",
    "本条款经双方确认签字盖章后生效。",
    "请于收到通知后五个工作日内完成相关确认手续。",
    "逾期未办理的，视为默认同意本次调整内容。",
    "相关附件材料需加盖单位公章后上传。",
]

PAY_CONDITIONS = [
    "按月度进度支付，支付比例为当月已完成产值的80%",
    "竣工验收合格后一次性支付",
    "预付合同总价的30%，剩余款项按进度分期支付",
    "每期支付前须提交等额增值税专用发票",
]

GENERIC_TEXTS = [
    "合同管理", "项目资料", "审批流程", "工程结算", "招标采购",
    "施工组织设计", "安全生产责任书", "质量验收标准",
]


def _looks_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in _PLACEHOLDER_EXAMPLES
    return False


def _meaningful_example(schema: dict) -> Any:
    """返回有业务意义的 example/default；Swagger 占位符返回 None。"""
    for key in ("example", "default"):
        if key in schema:
            value = schema[key]
            if not _looks_placeholder(value) and value != 0:
                return value
    examples = schema.get("examples")
    if isinstance(examples, list) and examples:
        value = examples[0]
        if not _looks_placeholder(value) and value != 0:
            return value
    return None


def _has_hint(field_name: str, *keywords: str) -> bool:
    lowered = field_name.lower()
    return any(keyword in lowered for keyword in keywords)


# description 中的枚举说明，如「1奖励 2处罚」「1-待支付，2-已支付」「-1作废 0正常」
_DESCRIPTION_ENUM_RE = re.compile(
    r"(?<![\d.])(-?\d{1,9})\s*[-–—=~=>>→:：、.．]{0,3}\s*"
    r"([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9_]*)"
)


def _description_enum_values(schema: dict) -> list[int]:
    """从 description 解析「1奖励 2处罚」式的取值说明，返回候选值列表。

    至少出现两个不同取值才认为是枚举（避免把「最长30天」这类范围描述误判）。
    """
    text = str(schema.get("description") or "")
    if not text:
        return []
    values: list[int] = []
    seen: set[int] = set()
    for match in _DESCRIPTION_ENUM_RE.finditer(text):
        try:
            value = int(match.group(1))
        except ValueError:
            continue
        if value not in seen:
            seen.add(value)
            values.append(value)
        if len(values) >= 20:
            break
    return values if len(values) >= 2 else []


# ---- 字段名语义 -> 取值函数 ---------------------------------------------------

def _string_for_name(name: str, schema: dict) -> str:
    fmt = schema.get("format")
    lowered = name.lower()
    if fmt == "email" or _has_hint(lowered, "email", "mail"):
        return f"user{random.randint(10, 9999)}@example.com"

    if _has_hint(name, "uuid", "guid"):
        return uuid.uuid4().hex

    # 公司主体：partyAName / companyName / unitName ...
    if _has_hint(name, "party", "company", "corp", "supplier", "vendor", "contractor", "unitname"):
        if _has_hint(name, "name"):
            return random.choice(COMPANY_NAMES)
        return f"D{random.randint(10000000, 99999999)}"  # 主体编码类

    # 纯 ID 类字段：id / resId / sectionId / xxxId -> 形如 RES-48291056
    if lowered == "id" or lowered.endswith("id"):
        letters = re.sub(r"[^A-Za-z]", "", name)[:3].upper() or "ID"
        return f"{letters}-{random.randint(10_000_000, 99_999_999)}"

    if _has_hint(name, "filename", "file_name") or (_has_hint(name, "file") and _has_hint(name, "name")):
        return random.choice(FILE_NAMES)

    if _has_hint(name, "section") and _has_hint(name, "name"):
        return random.choice(SECTION_NAMES)

    if _has_hint(name, "payment") and _has_hint(name, "name"):
        return random.choice(PAYMENT_NAMES)

    if _has_hint(name, "resname") or (_has_hint(name, "resource") and _has_hint(name, "name")):
        return random.choice(CONTRACT_RESOURCE_NAMES)

    if _has_hint(name, "paycondition", "condition"):
        return random.choice(PAY_CONDITIONS)

    if _has_hint(name, "remark", "comment", "description", "memo"):
        return random.choice(REMARKS)

    if _has_hint(name, "person", "owner", "creator", "manager", "handler") and _has_hint(name, "name"):
        return random.choice(PERSON_NAMES)

    if _has_hint(name, "phone", "mobile", "tel"):
        return f"13{random.randint(0, 9)}{random.randint(10000000, 99999999)}"

    if _has_hint(name, "uri", "url", "link"):
        return f"https://example.com/files/{random.randint(1000, 9999)}.pdf"

    # 编号类：no / *no / *code / *num(非数量含义的字符串)
    if re.fullmatch(r"no", name.lower()) or _has_hint(name, "code", "serial", "billno"):
        prefixes = {"contract": "HT", "invoice": "FP", "order": "PO"}
        prefix = next((v for k, v in prefixes.items() if k in name.lower()), "NO")
        return f"{prefix}-{datetime.now().year}{random.randint(100000, 999999)}"

    if _has_hint(name, "name", "title"):
        return random.choice(GENERIC_TEXTS)

    return random.choice(GENERIC_TEXTS)


def _number_for_name(name: str, schema: dict, *, integer: bool) -> int | float:
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")

    def clamp(value: int | float) -> int | float:
        if minimum is not None:
            value = max(value, minimum)
        if maximum is not None:
            value = min(value, maximum)
        return int(value) if integer else round(float(value), 2)

    lowered = name.lower()
    if _has_hint(lowered, "ratio", "rate", "percent", "proportion"):
        return clamp(round(random.uniform(0, 20), 2))
    if _has_hint(lowered, "amount", "price", "cost", "fee", "sum", "money", "budget"):
        return clamp(round(random.uniform(1_000, 2_000_000), 2))
    if _has_hint(lowered, "filesize", "size"):
        return clamp(random.randint(1024, 52_428_800))
    if _has_hint(lowered, "version"):
        return clamp(random.randint(1, 3))
    if _has_hint(lowered, "count", "qty", "quantity", "total", "num"):
        return clamp(random.randint(1, 200))
    if _has_hint(lowered, "page"):
        return clamp(random.randint(1, 50))
    if integer:
        return clamp(random.randint(1, 100))
    return clamp(round(random.uniform(1, 10_000), 2))


def _is_time_field(name: str) -> bool:
    """字段名是否带日期/时间语义（createdAt、updateTime、signDate、startDate……）。"""
    lowered = name.lower()
    if lowered.endswith("by"):  # createdBy / updatedBy 等人员字段
        return False
    return (
        _has_hint(lowered, "date", "time")
        or lowered.endswith("at")
        or lowered.startswith(("create", "update", "sign", "start", "end"))
    )


def _ms_timestamp_for_time_field(name: str) -> int | None:
    """时间语义字段统一返回毫秒级时间戳（过去一年内随机时刻）；无语义返回 None。"""
    if not _is_time_field(name):
        return None
    now_ms = int(datetime.now().timestamp() * 1000)
    return now_ms - random.randint(0, 365) * 86_400_000 - random.randint(0, 86_399_000)


def _value_for_schema(
    schema: Any, field_name: str, *, depth: int, seen_refs: set[str], array_count: int | None = None
) -> Any:
    if depth > MAX_DEPTH:
        return None
    if not isinstance(schema, dict):
        return random.choice(GENERIC_TEXTS)

    # 循环引用残留的 $ref 占位：不再展开，给个通用值
    ref = schema.get("$ref")
    if isinstance(ref, str):
        if ref in seen_refs:
            return random.choice(GENERIC_TEXTS)
        seen_refs = {*seen_refs, ref}

    # 1. enum / const 必须遵守
    const = schema.get("const")
    if const is not None:
        return const
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return random.choice([item for item in enum if item is not None] or enum)

    # 2. 有意义的 example / default
    explicit = _meaningful_example(schema)
    if explicit is not None:
        return explicit

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((t for t in schema_type if t != "null"), "string")
    if not schema_type:
        if isinstance(schema.get("properties"), dict):
            schema_type = "object"
        elif "items" in schema:
            schema_type = "array"

    # 3. description 中的取值说明（如「奖罚类型：1奖励 2处罚」）
    if schema_type in {"string", "integer", "number"}:
        desc_values = _description_enum_values(schema)
        if desc_values:
            value = random.choice(desc_values)
            return str(value) if schema_type == "string" else value

    # 4. format 与时间语义
    fmt = schema.get("format")
    if schema_type == "string":
        time_ms = _ms_timestamp_for_time_field(field_name)
        if time_ms is not None:
            return time_ms
        if fmt == "uuid":
            return uuid.uuid4().hex
        return _string_for_name(field_name, schema)

    if schema_type in {"integer", "number"}:
        time_ms = _ms_timestamp_for_time_field(field_name)
        if time_ms is not None:
            return time_ms
        if schema_type == "integer":
            return _number_for_name(field_name, schema, integer=True)
        return _number_for_name(field_name, schema, integer=False)
    if schema_type == "boolean":
        return random.random() < 0.85

    if schema_type == "array":
        items = schema.get("items") or {}
        if array_count is not None:
            count = array_count
        else:
            min_items = max(schema.get("minItems") or 0, 1)
            max_items = max(schema.get("maxItems") or 3, min_items)
            count = random.randint(min(min_items, 3), min(max_items, 3))
        return [
            _value_for_schema(items, field_name, depth=depth + 1, seen_refs=seen_refs, array_count=None)
            for _ in range(count)
        ]

    if schema_type == "object":
        result = {}
        for key, prop in (schema.get("properties") or {}).items():
            result[key] = _value_for_schema(
                prop, str(key), depth=depth + 1, seen_refs=seen_refs, array_count=array_count
            )
        if not result and isinstance(schema.get("additionalProperties"), dict):
            result["key"] = _value_for_schema(
                schema["additionalProperties"], "key", depth=depth + 1, seen_refs=seen_refs
            )
        return result

    if schema_type == "null":
        return None

    # 无 type 信息：按字段名猜字符串
    return _string_for_name(field_name, schema)


def generate_mock_value(schema: dict, array_count: int | None = None) -> Any:
    """根据 response schema 生成一个真实感的随机值（未包裹外层结构）。

    array_count 指定时（分页请求），顶层及各数组按该条数生成。
    """
    return _value_for_schema(schema, "", depth=0, seen_refs=set(), array_count=array_count)


# ---- 请求值回填与分页 -----------------------------------------------------------

PAGE_SIZE_KEYS = {"pagesize", "page_size", "size", "limit", "per_page"}
PAGE_KEYS = PAGE_SIZE_KEYS | {"page", "pagenum", "page_no", "pageno", "current", "currentpage"}


def _normalize_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def collect_request_values(
    request_body: Any,
    query_params: dict[str, str],
    parameters: list[dict] | None,
) -> dict[str, Any]:
    """收集请求中出现的所有「字段名 -> 值」（body 扁平化 + query/param），用于回填响应。"""
    values: dict[str, Any] = {}

    def _flatten(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, item in obj.items():
                if key not in values and not isinstance(item, (dict, list)):
                    values[key] = item
                _flatten(item)
        elif isinstance(obj, list):
            for item in obj:
                _flatten(item)

    _flatten(request_body)

    for name, raw in (query_params or {}).items():
        values.setdefault(str(name), raw)

    for param in parameters or []:
        if not isinstance(param, dict):
            continue
        name = str(param.get("key") or param.get("name") or "")
        value = param.get("value")
        if name and value not in (None, ""):
            values.setdefault(name, value)

    # 数值化的 query 值（pageSize=10 -> 10）
    result: dict[str, Any] = {}
    for name, value in values.items():
        if isinstance(value, str):
            try:
                result[name] = int(value) if value.isdigit() else float(value) if re.fullmatch(r"-?\d+\.\d+", value) else value
            except ValueError:
                result[name] = value
        else:
            result[name] = value
    return result


def apply_request_overrides(payload: Any, request_values: dict[str, Any]) -> Any:
    """把请求中出现且响应中也存在的同名字段替换为请求值（递归，含嵌套对象与数组）。"""
    if not request_values or not isinstance(payload, (dict, list)):
        return payload

    normalized_requests: dict[str, Any] = {}
    for name, value in request_values.items():
        normalized_requests[_normalize_key(name)] = value

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            result = {}
            for key, item in node.items():
                norm = _normalize_key(key)
                if norm in normalized_requests and not isinstance(item, (dict, list)):
                    result[key] = normalized_requests[norm]
                else:
                    result[key] = _walk(item)
            return result
        if isinstance(node, list):
            return [_walk(item) for item in node]
        return node

    return _walk(payload)


def _find_page_size(request_values: dict[str, Any]) -> int | None:
    """从请求值里找分页大小参数；找不到或非法返回 None。"""
    for name, value in request_values.items():
        if _normalize_key(name) in PAGE_SIZE_KEYS:
            try:
                size = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= size <= 1000:
                return size
    return None


def trim_array_by_page_size(payload: Any, page_size: int) -> Any:
    """递归把响应里的数组裁剪到 page_size 个元素（只处理长度超出的数组）。"""
    if isinstance(payload, dict):
        return {key: trim_array_by_page_size(item, page_size) for key, item in payload.items()}
    if isinstance(payload, list) and len(payload) > page_size:
        trimmed = [trim_array_by_page_size(item, page_size) for item in payload[:page_size]]
        return trimmed
    return payload


def build_mock_response_body(
    schema: dict,
    request_values: dict[str, Any] | None = None,
) -> str:
    """生成统一包裹 {code,msg,success,data} 的响应体 JSON 字符串。

    - 若 schema 本身就是 code/message/data 形状，其 data 内容直接放到外层 data 下。
    - request_values 中出现且响应里也存在的同名字段，用请求值替换。
    - 请求带 pageSize 等分页参数时，数组按该条数生成（pageSize 传多少就返回多少条）。
    """
    page_size = _find_page_size(request_values or {})
    payload = generate_mock_value(schema, array_count=page_size)
    # 顶层就是字符串 schema 时，直接返回一个 uuid hex
    if isinstance(payload, str):
        payload = uuid.uuid4().hex

    if request_values:
        payload = apply_request_overrides(payload, request_values)
    if page_size is not None:
        payload = trim_array_by_page_size(payload, page_size)

    if (
        isinstance(payload, dict)
        and "data" in payload
        and any(key in payload for key in ("code", "msg", "message", "success"))
    ):
        inner = payload.get("data")
    else:
        inner = payload
    return json.dumps(
        {"code": 200, "msg": "Success", "success": True, "data": inner},
        ensure_ascii=False,
        indent=2,
    )
