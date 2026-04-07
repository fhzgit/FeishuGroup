import json
import os
from dotenv import load_dotenv
import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
)

load_dotenv()
APP_ID = os.environ.get("FEISHU_APP_ID")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET")
client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()

card_dict = {
  "schema": "2.0",
  "config": {
    "update_multi": True,
    "wide_screen_mode": True
  },
  "header": {
    "title": {
      "tag": "plain_text",
      "content": "🤖 智能服务群机器人 - 使用指南"
    },
    "template": "blue"
  },
  "body": {
    "elements": [
      {
        "tag": "markdown",
        "content": "**欢迎使用智能服务群机器人！**\n我不仅能通过内置知识库为您全天候极速解答问题，还在您需要人工协助时支持一键拉起专家专属群，问题排查结果自动归档跟进。"
      },
      {
        "tag": "hr"
      },
      {
        "tag": "markdown",
        "content": "✨ **<font color='blue'>核心功能</font>**\n- **✅ 极速智能解答**：结合 Aily 知识归纳技术，对群内提问进行智能、精准的回复。\n- **👷‍♂️ 一键转专家群**：AI 无法解决的问题，可直接点击卡片按扭转接对应业务专家。新群聊直达上下文！\n- **♻️ 群生命周期管理**：转交拉群处理完毕后，如果服务群闲置判定超时，系统会自动清理释放保持列表极简。\n- **📊 知识无缝沉淀**：极速解决与人工兜底的每笔处理记录，自动化推送到飞书多维表分析归栏。"
      },
      {
        "tag": "hr"
      },
      {
        "tag": "markdown",
        "content": "🚀 **<font color='blue'>极简使用步骤</font>**"
      },
      {
        "tag": "markdown",
        "content": "- **<font color='green'>Step 1. 发起即时提问</font>**：在接入机器人的话题群中直接 **新建话题** 发出您的需求，（无需@机器人）底层大模型即会被唤醒检索给出解答。\n- **<font color='orange'>Step 2. AI 回答闭环</font>**：如果拦截方案可行，直接点击解答下方的 **✅ 已解决** 按钮即刻复盘结案，无需其它操作。\n- **<font color='purple'>Step 3. 专家对口流转</font>**：遇到复杂冷门难题？点击对应专业的 **转接人工** 按钮，机器人全自动为您拉取独立服务群连线探讨。 <font color='grey'>（注：讨论结束放置数分钟后，服务组内会触发自动释放回收）</font>"
      },
      {
        "tag": "hr"
      },
      {
        "tag": "markdown",
        "content": "<font color='grey'>🏷 致力于打造极简清爽的群客诉流转分发体验</font>"
      }
    ]
  }
}

req = CreateMessageRequest.builder() \
    .receive_id_type("chat_id") \
    .request_body(
        CreateMessageRequestBody.builder()
        .receive_id("oc_6fff1a09ad0315f0d77d516621a4e6ee")
        .msg_type("interactive")
        .content(json.dumps(card_dict))
        .build()
    ).build()

resp = client.im.v1.message.create(req)
if resp.success():
    print(f"✅ 发送成功 message_id: {resp.data.message_id}")
else:
    print(f"❌ 发送失败 ({resp.code}): {resp.msg}")
