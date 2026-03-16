"""
配置加载模块
从 .env 文件或环境变量中读取所有配置项
"""
import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()


def _get_required(key: str) -> str:
    """获取必填配置项，缺失时抛出异常"""
    value = os.getenv(key)
    if not value:
        raise ValueError(f"必填环境变量 [{key}] 未配置，请检查 .env 文件")
    return value


def _get_list(key: str, default: str = "") -> list[str]:
    """获取逗号分隔的列表配置"""
    raw = os.getenv(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# ── 飞书应用凭证 ──────────────────────────────────────────
APP_ID: str = _get_required("FEISHU_APP_ID")
APP_SECRET: str = _get_required("FEISHU_APP_SECRET")

# 卡片回调验证（Webhook 模式下需要，WebSocket 模式下可留空）
CARD_VERIFICATION_TOKEN: str = os.getenv("FEISHU_CARD_VERIFICATION_TOKEN", "")
CARD_ENCRYPT_KEY: str = os.getenv("FEISHU_CARD_ENCRYPT_KEY", "")

# ── 业务配置 ──────────────────────────────────────────────
# 负责人 open_id 列表（at 用户 + 创群后拉入）
HANDLER_OPEN_IDS: list[str] = _get_list("HANDLER_OPEN_IDS")

# 监听策略：all=所有消息，keyword=仅关键词触发
MONITOR_MODE: str = os.getenv("MONITOR_MODE", "all").lower()

# 关键词列表（MONITOR_MODE=keyword 时生效）
QUESTION_KEYWORDS: list[str] = _get_list(
    "QUESTION_KEYWORDS", "?,？,请问,咨询,帮忙,怎么,如何,问一下"
)

# 服务群名称前缀
SERVICE_GROUP_PREFIX: str = os.getenv("SERVICE_GROUP_PREFIX", "服务群")

# 日志级别
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()


def validate():
    """启动时校验配置完整性"""
    errors = []
    if not APP_ID or APP_ID.startswith("cli_xxx"):
        errors.append("FEISHU_APP_ID 未配置或仍为示例值")
    if not APP_SECRET or "xxx" in APP_SECRET:
        errors.append("FEISHU_APP_SECRET 未配置或仍为示例值")
    if not HANDLER_OPEN_IDS:
        errors.append("HANDLER_OPEN_IDS 未配置，机器人不知道要@谁")
    if MONITOR_MODE not in ("all", "keyword"):
        errors.append("MONITOR_MODE 仅支持 all 或 keyword")
    if errors:
        raise ValueError("配置校验失败：\n" + "\n".join(f"  - {e}" for e in errors))
