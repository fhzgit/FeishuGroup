# FeishuGroup

飞书群智能协同机器人，提供以下闭环能力：

- 监听指定飞书群的话题首条消息
- 调用 Aily 生成 AI 回答
- 支持卡片按钮确认 `AI 已解决`
- 支持按部门一键转人工，自动创建服务群并转发上下文
- 服务群支持自动解散、手动 `@机器人 + 关键词` 归档
- 处理结果写入飞书多维表格
- 机器人被拉入新监听群时，自动发送一次使用说明卡片

## 运行环境

- Python 3.10+
- 可访问飞书开放平台和 Aily 接口的网络环境
- 一个已创建的飞书开放平台应用

依赖安装：

```bash
pip install -r requirements.txt
```

## 项目结构

- [main.py](/d:/project/FeishuGroup/main.py): 入口，初始化日志、WebSocket 事件监听、卡片回调
- [config.py](/d:/project/FeishuGroup/config.py): `.env` 配置读取与校验
- [handlers/message_handler.py](/d:/project/FeishuGroup/handlers/message_handler.py): 监听群消息并触发 AI 回答
- [handlers/card_handler.py](/d:/project/FeishuGroup/handlers/card_handler.py): 处理卡片按钮点击
- [handlers/auto_dissolve.py](/d:/project/FeishuGroup/handlers/auto_dissolve.py): 服务群自动解散与归档
- [handlers/chat_member_handler.py](/d:/project/FeishuGroup/handlers/chat_member_handler.py): 机器人被拉入新群后的初始化动作
- [services/ai_service.py](/d:/project/FeishuGroup/services/ai_service.py): Aily 调用封装
- [services/feishu_api.py](/d:/project/FeishuGroup/services/feishu_api.py): 飞书 API 封装
- [send_summary.py](/d:/project/FeishuGroup/send_summary.py): 新监听群使用说明卡片

## 功能流程

### 1. 监听群提问

用户在监听群里新建话题，机器人会：

1. 发送一张“思考中”占位卡片
2. 调用 Aily 获取回答
3. 把原卡片更新为 AI 结果卡片
4. 同步写入“问答处理统计”表

### 2. AI 已解决

用户点击 `AI 已解决` 后：

1. 卡片变更为已解决状态
2. 统计表中的 `解决方式` 更新为 `AI自动解决`

### 3. 转人工

用户点击某个部门按钮后：

1. 机器人创建或复用对应服务群
2. 拉入提问人、点击人和该部门负责人
3. 发送欢迎卡片
4. 转发原始问题消息
5. 统计表中的 `解决方式` 更新为 `人工客服介入`

### 4. 服务群归档与解散

服务群支持两种触发方式：

- 自动解散：群闲置达到阈值后发卡片提醒，确认后归档并倒计时解散
- 手动归档：在服务群中 `@机器人 + 归档关键词`

归档时会把聊天记录写入“服务群归档记录”表。

### 5. 机器人入群说明

当机器人被拉入新的普通监听群时，会在该群中自动发送一次使用说明卡片。  
如果是机器人自己创建的服务群，则不会发送。

## 飞书开放平台配置步骤

### 1. 创建应用

在飞书开放平台创建自建应用，并获取：

- `App ID`
- `App Secret`

### 2. 开启机器人能力

至少确保应用具备以下能力：

- 机器人收发群消息
- 消息卡片回调
- 创建群聊
- 拉人入群
- 转发消息
- 读取群消息
- 解散群聊
- 多维表格读写

### 3. 配置事件订阅

当前代码使用 WebSocket 长连接，不需要公网回调地址，但需要开启这些事件：

- `im.message.receive_v1`
- `card.action.trigger`
- `im.chat.member.bot.added_v1`

### 4. 配置消息卡片

如果你需要卡片按钮生效，必须在飞书开放平台里配置消息卡片参数，并填入：

- `FEISHU_CARD_VERIFICATION_TOKEN`
- `FEISHU_CARD_ENCRYPT_KEY`

### 5. 准备 Aily 配置

你需要准备：

- `AILY_APP_ID`
- `AILY_APP_SECRET`
- `AILY_BOT_ID`

### 6. 准备多维表格

需要创建一个多维表格应用，并至少包含两张表。

如果你希望直接复用现成结构，可以使用这份模板底表：

- 模板底表主页: `https://ccnj8ssvp8ta.feishu.cn/base/GYULbwK88ajzdnsQS56cHuUsn0e`
- 模板统计表直达: `https://ccnj8ssvp8ta.feishu.cn/base/GYULbwK88ajzdnsQS56cHuUsn0e?table=tblClsYzBH1rHuYw&view=vew3MKJR1x`
- 模板归档表直达: `https://ccnj8ssvp8ta.feishu.cn/base/GYULbwK88ajzdnsQS56cHuUsn0e?table=tblw98M2j6gyxpNs&view=vew2mjnPmm`

建议做法：

1. 打开模板底表
2. 在飞书多维表格里复制一份到你自己的空间
3. 把复制后底表的 `app_token`、`table_id` 写入 `.env`
4. 把飞书机器人添加为文档应用

#### 服务群归档记录

字段名必须与代码一致：

- `服务群名称`
- `服务群ID`
- `聊天记录`
- `消息条数`
- `归档时间`

#### 问答处理统计

字段名必须与代码一致：

- `话题消息ID`
- `提问内容`
- `提问时间`
- `所在群ID`
- `解决方式`
- `AI回答内容`
- `记录时间`
- `转入部门`
- `操作人`

准备完成后，把以下三个值写入 `.env`：

- `BITABLE_APP_TOKEN`
- `BITABLE_TABLE_ID`
- `BITABLE_STATS_TABLE_ID`

## `.env` 配置说明

建议从 [.env.example](/d:/project/FeishuGroup/.env.example) 复制一份为本地 `.env`，不要把真实凭证提交到仓库。

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

macOS / Linux:

```bash
cp .env.example .env
```

示例：

```env
# 1. 飞书机器人基础凭证
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxxx

# 2. Aily 配置
AILY_APP_ID=cli_xxx
AILY_APP_SECRET=xxxx
AILY_BOT_ID=spring_xxx
AILY_HOST=https://open.feishu.cn
AILY_REQUEST_TIMEOUT=20

# 3. 飞书卡片回调
FEISHU_CARD_VERIFICATION_TOKEN=
FEISHU_CARD_ENCRYPT_KEY=

# 4. 部门按钮配置
DEPARTMENT_1=产品咨询|产品咨询|🛠|ou_xxx
DEPARTMENT_2=技术支持|技术支持|💻|ou_xxx
DEPARTMENT_3=商务合作|商务合作|🤝|ou_xxx
DEPARTMENT_4=财务对账|财务对账|💰|ou_xxx

# 5. 群与运行行为
ALLOWED_CHAT_IDS=
SERVICE_GROUP_PREFIX=服务群
AUTO_DISSOLVE_IDLE_SECONDS=300
AUTO_DISSOLVE_COUNTDOWN_SECONDS=60
AUTO_DISSOLVE_FALLBACK_SECONDS=86400
AUTO_DISSOLVE_CHECK_INTERVAL_SECONDS=30
RESOLVE_KEYWORDS=问题已解决,已解决,问题解决
LOG_LEVEL=INFO

# 6. 多维表格
BITABLE_APP_TOKEN=app_token_xxx
BITABLE_TABLE_ID=tbl_xxx
BITABLE_STATS_TABLE_ID=tbl_xxx
```

### 关键配置说明

- `ALLOWED_CHAT_IDS` 为空时，允许所有群；填值后只监听指定群
- `DEPARTMENT_1..N` 格式为 `部门名|按钮名|图标|负责人open_id列表`
- `RESOLVE_KEYWORDS` 仅在服务群中生效
- `AUTO_DISSOLVE_*` 控制服务群自动解散时序

## 启动方式

```bash
python main.py
```

启动后会通过 WebSocket 连到飞书，不需要额外暴露 HTTP 服务。

## 运行中的本地状态文件

以下文件是运行期状态，不应提交到仓库：

- `.group_cache.json`: 卡片按钮建群缓存
- `.processed_msgs`: 消息去重缓存
- `.summary_sent_chats.json`: 已发送摘要卡片的群记录
- `.dissolve_state.json`: 服务群自动解散状态
- `*.log`: 运行日志

这些文件已经在 [.gitignore](/d:/project/FeishuGroup/.gitignore) 中排除。

## 提交前检查

上传代码前至少检查：

1. `.env` 未加入 Git
2. 没有把 `dist/`、日志文件、状态文件提交到仓库
3. 代码中没有硬编码 `App Secret`、`Aily Secret`、群 ID、用户 open_id
4. 多维表格字段名和代码写入字段保持一致

## 当前已知注意点

- 部门负责人 `open_id` 必须与当前飞书应用处于同一应用体系，否则可能出现 `open_id cross app`
- 服务群自动解散使用本地状态文件恢复流程，部署时建议保持单实例运行
- 如果修改 `.env`，进程会自动热重启
