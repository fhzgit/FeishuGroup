"""
配置加载模块
从 .env 文件或环境变量中读取所有配置项
"""
import json
import os
import re
from dotenv import load_dotenv

load_dotenv()


def _get_required(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ValueError(f"必填环境变量 [{key}] 未配置，请检查 .env 文件")
    return value


def _get_list(key: str, default: str = "") -> list[str]:
    raw = os.getenv(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _dedupe_keep_order(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _normalize_open_ids(value) -> list[str]:
    if isinstance(value, str):
        return _dedupe_keep_order([item.strip() for item in value.split(",") if item.strip()])
    if isinstance(value, list):
        return _dedupe_keep_order(
            [item.strip() for item in value if isinstance(item, str) and item.strip()]
        )
    return []


_SIMPLE_DEPT_KEY_PATTERN = re.compile(r"^DEPARTMENT_(\d+)$")


def _load_department_handlers_from_simple_env(
    fallback_ids: list[str],
) -> tuple[dict, list[str]]:
    """
    从简化配置读取部门：
      DEPARTMENT_1=产品咨询|产品咨询|🛠|ou_xxx,ou_yyy
      DEPARTMENT_2=技术支持|技术支持|💻|ou_zzz

    字段顺序：name|button_name|icon|ids
    - button_name 省略时使用 name
    - icon 省略时使用 🏢
    - ids 省略时回退 HANDLER_OPEN_IDS
    """
    errors: list[str] = []
    handlers: dict = {}

    indexed_keys: list[tuple[int, str]] = []
    for env_key in os.environ.keys():
        match = _SIMPLE_DEPT_KEY_PATTERN.match(env_key)
        if match:
            indexed_keys.append((int(match.group(1)), env_key))

    indexed_keys.sort(key=lambda x: x[0])
    if not indexed_keys:
        return {}, []

    for index, env_key in indexed_keys:
        raw = os.getenv(env_key, "").strip()
        if not raw:
            errors.append(f"{env_key} 不能为空")
            continue

        parts = [p.strip() for p in raw.split("|")]
        name = parts[0] if len(parts) >= 1 else ""
        button_name = parts[1] if len(parts) >= 2 else ""
        icon = parts[2] if len(parts) >= 3 else ""
        ids_raw = parts[3] if len(parts) >= 4 else ""

        if not name:
            errors.append(f"{env_key} 缺少部门名称（第 1 段）")
            continue

        if not button_name:
            button_name = name
        if not icon:
            icon = "🏢"

        ids = _normalize_open_ids(ids_raw) or fallback_ids
        handlers[f"dept_{index}"] = {
            "name": name,
            "button_name": button_name,
            "icon": icon,
            "ids": ids,
        }

    if not handlers and not errors:
        errors.append("DEPARTMENT_1/2/... 已配置但未解析出有效部门")

    return handlers, errors


def _load_department_handlers_from_json(fallback_ids: list[str]) -> tuple[dict, list[str]]:
    """
    从 DEPARTMENT_HANDLERS_JSON 读取部门按钮配置
    支持两种格式：
      1) 对象：{"product":{"name":"产品咨询","button_name":"产品咨询","icon":"🛠","ids":["ou_xxx"]}}
      2) 数组：[{"key":"product","name":"产品咨询","button_name":"产品咨询","icon":"🛠","ids":"ou_xxx,ou_yyy"}]
    """
    raw = os.getenv("DEPARTMENT_HANDLERS_JSON", "").strip()
    if not raw:
        return {}, []

    errors: list[str] = []
    handlers: dict = {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return {}, [
            "DEPARTMENT_HANDLERS_JSON 解析失败："
            f"{e.msg} (line {e.lineno}, col {e.colno})"
        ]

    entries: list[tuple[str, dict]] = []
    if isinstance(parsed, dict):
        for key, item in parsed.items():
            entries.append((str(key), item))
    elif isinstance(parsed, list):
        for idx, item in enumerate(parsed, start=1):
            if not isinstance(item, dict):
                errors.append(
                    f"DEPARTMENT_HANDLERS_JSON[{idx}] 必须是对象，当前是 {type(item).__name__}"
                )
                continue
            key = str(item.get("key") or item.get("department") or f"dept_{idx}")
            entries.append((key, item))
    else:
        return {}, [
            "DEPARTMENT_HANDLERS_JSON 必须是 JSON 对象或数组"
        ]

    for idx, (key, item) in enumerate(entries, start=1):
        if not isinstance(item, dict):
            errors.append(
                f"DEPARTMENT_HANDLERS_JSON 第 {idx} 项必须是对象，当前是 {type(item).__name__}"
            )
            continue

        dept_key = key.strip()
        if not dept_key:
            errors.append(f"DEPARTMENT_HANDLERS_JSON 第 {idx} 项 key 不能为空")
            continue
        if dept_key in handlers:
            errors.append(f"DEPARTMENT_HANDLERS_JSON 存在重复 key: {dept_key}")
            continue

        name = str(item.get("name") or item.get("department_name") or "").strip()
        button_name = str(item.get("button_name") or item.get("button") or "").strip()
        icon = str(item.get("icon") or "🏢").strip() or "🏢"

        ids = _normalize_open_ids(item.get("ids"))
        if not ids:
            ids = _normalize_open_ids(item.get("handler_open_ids"))
        if not ids:
            ids = fallback_ids

        if not name:
            name = button_name or dept_key
        if not button_name:
            button_name = name

        handlers[dept_key] = {
            "name": name,
            "button_name": button_name,
            "icon": icon,
            "ids": ids,
        }

    if not handlers and not errors:
        errors.append("DEPARTMENT_HANDLERS_JSON 已配置但未解析出有效部门")

    return handlers, errors


# ── 飞书应用凭证 ──────────────────────────────────────────
APP_ID: str = _get_required("FEISHU_APP_ID")
APP_SECRET: str = _get_required("FEISHU_APP_SECRET")

CARD_VERIFICATION_TOKEN: str = os.getenv("FEISHU_CARD_VERIFICATION_TOKEN", "")
CARD_ENCRYPT_KEY: str = os.getenv("FEISHU_CARD_ENCRYPT_KEY", "")

# ── 业务配置 ──────────────────────────────────────────────
# 允许触发的群聊 ID（为空则允许所有群）
ALLOWED_CHAT_IDS: list[str] = _get_list("ALLOWED_CHAT_IDS")

# 服务群名称前缀
SERVICE_GROUP_PREFIX: str = os.getenv("SERVICE_GROUP_PREFIX", "服务群")

# 日志级别
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

# ── 按部门配置负责人 ──────────────────────────────────────
# 部门配置优先读取简化格式 DEPARTMENT_1/2/...，其次 DEPARTMENT_HANDLERS_JSON
_fallback_ids: list[str] = _get_list("HANDLER_OPEN_IDS")
_simple_department_handlers, _simple_department_errors = _load_department_handlers_from_simple_env(
    _fallback_ids
)
_json_department_handlers, _json_department_errors = _load_department_handlers_from_json(
    _fallback_ids
)

if _simple_department_handlers:
    DEPARTMENT_HANDLERS: dict = _simple_department_handlers
    _department_config_errors = _simple_department_errors
elif _json_department_handlers:
    DEPARTMENT_HANDLERS = _json_department_handlers
    _department_config_errors = _json_department_errors
else:
    DEPARTMENT_HANDLERS = {}
    _department_config_errors = (
        _simple_department_errors
        + _json_department_errors
        + ["未配置部门，请在 .env 中设置 DEPARTMENT_1/2/... 或 DEPARTMENT_HANDLERS_JSON"]
    )

# ── 归档功能配置 ──────────────────────────────────────────
BITABLE_APP_TOKEN: str = os.getenv("BITABLE_APP_TOKEN", "")
BITABLE_TABLE_ID: str = os.getenv("BITABLE_TABLE_ID", "")
BITABLE_STATS_TABLE_ID: str = os.getenv("BITABLE_STATS_TABLE_ID", "")
RESOLVE_KEYWORDS: list[str] = _get_list(
    "RESOLVE_KEYWORDS", "问题已解决,已解决,问题解决"
)


def validate():
    """启动时校验配置完整性"""
    errors = list(_department_config_errors)
    if not APP_ID or APP_ID.startswith("cli_xxx"):
        errors.append("FEISHU_APP_ID 未配置或仍为示例值")
    if not APP_SECRET or "xxx" in APP_SECRET:
        errors.append("FEISHU_APP_SECRET 未配置或仍为示例值")

    for key, dept in DEPARTMENT_HANDLERS.items():
        if not dept.get("name"):
            errors.append(f"部门[{key}] 缺少 name 配置")
        if not dept.get("button_name"):
            errors.append(f"部门[{key}] 缺少 button_name 配置")

    all_ids = [id_ for dept in DEPARTMENT_HANDLERS.values() for id_ in dept.get("ids", [])]
    if not all_ids:
        errors.append(
            "所有部门负责人 open_id 均未配置，请检查 "
            "DEPARTMENT_HANDLERS_JSON 或 HANDLER_OPEN_IDS 配置"
        )

    if errors:
        raise ValueError("配置校验失败：\n" + "\n".join(f"  - {e}" for e in errors))
