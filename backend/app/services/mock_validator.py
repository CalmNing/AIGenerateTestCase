"""接口 Mock 的入参校验器。

项目级动态 Mock 命中接口后，按该接口文档的 request_schema 与参数定义校验入参，
不通过时返回 400 与具体错误明细。覆盖四类规则：

1. 必填字段（required / query 参数 required）
2. 字符串最小/最大长度（minLength / maxLength）
3. 数值最小/最大值（minimum / maximum，含 exclusive 变体）
4. 枚举值（enum）

只做「报错」方向的校验，不修改请求数据。所有错误信息为中文、带 JSONPath 风格定位。
"""
import json
from typing import Any


class ValidationFailed(Exception):
    """携带全部错误明细的校验失败异常。"""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _resolve_ref(spec: dict, schema: Any) -> Any:
    if not isinstance(schema, dict) or "$ref" not in schema:
        return schema
    ref = schema["$ref"]
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return schema
    cur: Any = spec
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(cur, dict) or part not in cur:
            return schema
        cur = cur[part]
    return cur


def _schema_type(schema: dict) -> str | None:
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return next((t for t in schema_type if t != "null"), None)
    return str(schema_type) if schema_type else None


def _is_number(value: Any) -> bool:
    """数字判断（排除 bool —— Python 中 bool 是 int 子类）。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _join(path: str, field: str) -> str:
    """拼接字段路径；顶层无前缀点号（如 amount 而非 .amount）。"""
    return f"{path}.{field}" if path else field


def _validate_value(value: Any, schema: dict, path: str, errors: list[str], depth: int = 0) -> None:
    """递归校验单个值；schema 为空或深度超限时跳过。"""
    if depth > 12 or not isinstance(schema, dict):
        return

    schema = _resolve_ref({}, schema)
    if not isinstance(schema, dict):
        return

    # 4. 枚举校验
    enum = schema.get("enum")
    if isinstance(enum, list) and enum and value is not None:
        # 数值枚举容忍 int/float 差异，其余严格比较
        def _in_enum(v: Any) -> bool:
            for item in enum:
                if item == v or (isinstance(item, (int, float)) and isinstance(v, (int, float)) and not isinstance(item, bool) and not isinstance(v, bool) and float(item) == float(v)):
                    return True
            return False
        if not _in_enum(value):
            errors.append(f"{path} 取值 {value!r} 不在允许的枚举范围内 [{', '.join(str(i) for i in enum[:10])}]")

    schema_type = _schema_type(schema)

    # 2. 字符串长度
    if schema_type == "string" and isinstance(value, str):
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if _is_number(min_length) and len(value) < min_length:
            errors.append(f"{path} 长度 {len(value)} 小于最小长度 {int(min_length)}")
        if _is_number(max_length) and len(value) > max_length:
            errors.append(f"{path} 长度 {len(value)} 超过最大长度 {int(max_length)}")

    # 3. 数值范围
    if schema_type in ("integer", "number") and isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        exclusive_min = schema.get("exclusiveMinimum")
        exclusive_max = schema.get("exclusiveMaximum")
        # OpenAPI 3 中 exclusiveMinimum/Maximum 是数值；JSON Schema 草案中是布尔修饰 minimum/maximum
        if _is_number(exclusive_min):
            if value <= exclusive_min:
                errors.append(f"{path} 取值 {value} 必须大于 {exclusive_min}")
        elif isinstance(exclusive_min, bool) and exclusive_min and _is_number(minimum):
            if value <= minimum:
                errors.append(f"{path} 取值 {value} 必须大于 {minimum}")
        elif _is_number(minimum) and value < minimum:
            errors.append(f"{path} 取值 {value} 小于最小值 {minimum}")

        if _is_number(exclusive_max):
            if value >= exclusive_max:
                errors.append(f"{path} 取值 {value} 必须小于 {exclusive_max}")
        elif isinstance(exclusive_max, bool) and exclusive_max and _is_number(maximum):
            if value >= maximum:
                errors.append(f"{path} 取值 {value} 必须小于 {maximum}")
        elif _is_number(maximum) and value > maximum:
            errors.append(f"{path} 取值 {value} 大于最大值 {maximum}")

    # 递归对象/数组
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        required = schema.get("required") or []
        for field in required:
            if isinstance(field, str) and field not in value:
                errors.append(f"{_join(path, field)} 为必填字段，不能缺失")
        for key, item in value.items():
            sub_schema = props.get(key)
            if sub_schema is not None:
                _validate_value(item, sub_schema, _join(path, key), errors, depth + 1)
    elif isinstance(value, list):
        items = schema.get("items")
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, (int, float)) and len(value) < min_items:
            errors.append(f"{path} 元素数量 {len(value)} 少于最小 {int(min_items)} 个")
        if isinstance(max_items, (int, float)) and len(value) > max_items:
            errors.append(f"{path} 元素数量 {len(value)} 超过最大 {int(max_items)} 个")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _validate_value(item, items, f"{path}[{index}]", errors, depth + 1)


def validate_request_body(body_text: str | None, request_schema: dict | None) -> None:
    """按 request schema 校验请求体；不通过抛出 ValidationFailed。

    请求体不是合法 JSON 时直接报错；schema 为空则跳过校验。
    """
    if not request_schema or not isinstance(request_schema, dict):
        return

    stripped = (body_text or "").strip()
    if not stripped:
        # 无请求体：仅当 schema 要求顶层必填字段时报错
        required = request_schema.get("required") or []
        properties = request_schema.get("properties") or {}
        missing = [f for f in required if isinstance(f, str)]
        if missing:
            raise ValidationFailed([f"{field} 为必填字段，不能缺失" for field in missing])
        if properties and _schema_type(request_schema) == "object":
            pass  # 空对象也允许缺可选字段
        return

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValidationFailed([f"请求体不是合法 JSON: {exc}"]) from exc

    errors: list[str] = []
    _validate_value(payload, request_schema, "", errors)
    if errors:
        raise ValidationFailed(errors)


def validate_query_params(
    query_params: dict[str, str],
    parameters: list[dict] | None,
    request_schema: dict | None,
) -> None:
    """校验 query 参数：必填缺失 + 按 parameter/request schema 的约束校验取值。

    endpoint.parameters 里 in=query 的项提供 required/type/enum/minimum 等；
    request_schema.properties 里同名属性可作为约束补充。
    """
    errors: list[str] = []
    constraints: dict[str, dict] = {}

    for param in parameters or []:
        if not isinstance(param, dict) or param.get("in") != "query":
            continue
        name = str(param.get("key") or param.get("name") or "")
        if not name:
            continue
        rule: dict = {}
        if param.get("required"):
            rule["required"] = True
        schema_part = param.get("schema")
        if isinstance(schema_part, dict):
            rule.update({k: v for k, v in schema_part.items() if k != "required"})
        else:
            for src_key, dst_key in (
                ("type", "type"), ("enum", "enum"), ("minimum", "minimum"),
                ("maximum", "maximum"), ("minLength", "minLength"), ("maxLength", "maxLength"),
            ):
                if src_key in param:
                    rule[dst_key] = param[src_key]
        constraints[name] = rule

    # request schema 的 properties 作为补充约束（OpenAPI 3 有时不把 query 写进 parameters）
    if isinstance(request_schema, dict):
        for name, prop in (request_schema.get("properties") or {}).items():
            if isinstance(prop, dict) and name not in constraints:
                constraints[name] = dict(prop)
            elif isinstance(prop, dict) and prop.get("required"):
                constraints[name]["required"] = True

    for name, rule in constraints.items():
        raw = query_params.get(name)
        if raw is None or raw == "":
            if rule.get("required"):
                errors.append(f"?{name} 为必填参数，不能缺失")
            continue
        # query 值都是字符串，数值/枚举校验前先尝试转型
        value: Any = raw
        if rule.get("type") in ("integer", "number"):
            try:
                value = int(raw) if rule["type"] == "integer" else float(raw)
            except ValueError:
                errors.append(f"?{name} 取值 {raw!r} 不是合法的 {rule['type']}")
                continue
        _validate_value(value, rule, f"?{name}", errors)

    if errors:
        raise ValidationFailed(errors)
