"""
配置加载模块
从 .env 文件或环境变量中读取所有配置项
"""
import os
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
# 每个部门读取对应环境变量，fallback 到 HANDLER_OPEN_IDS
_fallback_ids: list[str] = _get_list("HANDLER_OPEN_IDS")

DEPARTMENT_HANDLERS: dict = {
    "product": {
        "name": "产品咨询",
        "icon": "🛠",
        "ids": _get_list("DEPARTMENT_PRODUCT") or _fallback_ids,
    },
    "tech": {
        "name": "技术支持",
        "icon": "💻",
        "ids": _get_list("DEPARTMENT_TECH") or _fallback_ids,
    },
    "business": {
        "name": "商务合作",
        "icon": "🤝",
        "ids": _get_list("DEPARTMENT_BUSINESS") or _fallback_ids,
    },
    "finance": {
        "name": "财务对账",
        "icon": "💰",
        "ids": _get_list("DEPARTMENT_FINANCE") or _fallback_ids,
    },
}

# ── 归档功能配置 ──────────────────────────────────────────
BITABLE_APP_TOKEN: str = os.getenv("BITABLE_APP_TOKEN", "")
BITABLE_TABLE_ID: str = os.getenv("BITABLE_TABLE_ID", "")
RESOLVE_KEYWORDS: list[str] = _get_list(
    "RESOLVE_KEYWORDS", "问题已解决,已解决,问题解决"
)


def validate():
    """启动时校验配置完整性"""
    errors = []
    if not APP_ID or APP_ID.startswith("cli_xxx"):
        errors.append("FEISHU_APP_ID 未配置或仍为示例值")
    if not APP_SECRET or "xxx" in APP_SECRET:
        errors.append("FEISHU_APP_SECRET 未配置或仍为示例值")

    all_ids = [id_ for dept in DEPARTMENT_HANDLERS.values() for id_ in dept["ids"]]
    if not all_ids:
        errors.append("所有部门负责人 open_id 均未配置，请检查 HANDLER_OPEN_IDS 或 DEPARTMENT_* 配置")

    if errors:
        raise ValueError("配置校验失败：\n" + "\n".join(f"  - {e}" for e in errors))
