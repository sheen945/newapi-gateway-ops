# newapi-gateway-ops

> 自建 New API / one-api 网关的日常运维与故障排查技能——覆盖「模型不可用 / 调用变慢 / 偶发失败 / 渠道 429 限流 / 模型列表缺失 / 增删模型 / 批量改渠道 / 查余额 / 管理 API 令牌 / 安全使用 sk- 密钥」等全场景。

[English Version](README_EN.md)

## 简介

这是一个 WorkBuddy / Claude Code 技能（Skill），沉淀了长期维护一套自建 new-api（one-api 系）大模型网关的实战经验。它的核心不是调用接口本身，而是三件事：

1. **一套区分「上游限流」与「本地配置错误」的标准诊断方法**——失败请求不落 logs，"日志干净"不等于没问题，要从成功记录的重试次数里找真相。
2. **一套三处联动改模型（channels + abilities + options）的标准流程**——只改 `channels.models` 是不够的，缺一处就报 `503 No available channel for model`。
3. **一套令牌安全四件套脚本**——`sk-` 密钥从网关到目的地全程走脚本管道，AI 只看脱敏后的内容，密钥绝不进对话、日志或命令参数。

**触发词**：模型不可用、调用变慢、偶发失败、渠道限流、429、模型列表缺失、增删模型、批量改渠道、查余额、管理令牌、复制密钥、密钥配进配置、定时测活、网关运维。

## 功能特性

- **限流诊断三步法**：第一步统计成功记录 `other` 字段中 `admin_info.use_channel` 的首次命中率（低于约 90% 即上游限流）；第二步按小时分组看趋势判断持续性；第三步用 `scripts/probe_channels.py` 绕开网关直连上游，按判定规则区分「长冷却账号级惩罚」「分钟级 RPM/TPM 滑动窗口」与「撞 token 量 vs 撞请求条数」。补充手段：`GET /api/channel/cooldowns` 查看网关侧模型级冷却。
- **三处联动改模型**：`channels.models`（渠道声明）、`abilities`（路由表，主键 `("group", model, channel_id)`，`group` 是 SQL 保留字必须加双引号）、`options` 定价三行（`ModelRatio` / `CompletionRatio` / `CacheRatio`）。推荐走管理 API 热更新——改 `channels.models` 后 New API 会自动重建 abilities，不用停服务。完成后执行四条必做验证（回读渠道、abilities 计数、`/v1/models` 用 sk- 令牌核对、真实调用一次）。
- **删模型完整清单**：channels.models → abilities → options 三行定价 → 客户端配置（如 models.json），一步不漏。
- **动态重试调优**：上游频繁 429 时调整 `GetDynamicRetryTimes()`（可用渠道数 ÷ 2 向上取整，封顶 8），并明确取舍——提高重试会等得更久、可能延长限流惩罚，全渠道 429 时重试再多也没用。
- **改后端代码 → 上线标准流程**：备份源码 → `go build` → md5 核对产物 → taskkill 停服 → 备份旧 exe 再替换 → 计划任务拉起 → 验收四条（端口 LISTENING、`/api/status` 200、`/burner` 200、`MainWindowHandle=0`）。前端改过必须先 rsbuild（不是 vite）重建 `web/dist`，因为 dist 被 go:embed 打进 exe。
- **令牌安全四件套**：`copy-key.js`（密钥只经管道进剪贴板）、`inject-key.js`（配置文件占位符 `__NEWAPI_TOKEN_<id>__` 原子替换，全程不许直接读目标文件）、`exec-token.js`（命令行占位符替换后子 shell 执行，输出自动脱敏并透传退出码）、`api.js`（管理 API 统一调用器，`/api/token*` 响应自动打码）。
- **令牌管理**：列令牌（key 字段自动脱敏展示）、建令牌（额度单位美元×500000，建完只报 ID 不再回取 key）、改分组（先 GET 全字段再 PUT 整体写回）。

## 工作原理 / 技术栈

- **技能机制**：以 `SKILL.md` 为载体，AI 通过触发词匹配加载。诊断、改模型、上线流程全部以可复制执行的 SQL / Python / Bash 片段给出，而不是抽象建议。
- **数据库直查**：网关真实数据库是 `<网关安装目录>/one-api.db`（SQLite）。注意工作区里同名文件通常是 0 字节占位。备份必须用 `sqlite3` 的 backup API 而不是裸 copy。`logs.type` 取值：2=消费、3=管理、7=登录；SQLite 无 `LEFT()` 函数，用 `substr()`。
- **两类令牌分工**：调 `/v1/*` 用用户令牌（`sk-` 开头）；调 `/api/*` 管理接口用 `users.id=1` 的 `access_token`。**别搞混**——用管理员 access_token 调 `/v1/models` 会返回 401，容易误判成"模型列表为空"。
- **脚本层**：`scripts/` 下为纯 JS 零依赖脚本（node 直接跑），配置按「环境变量 > 技能目录 `.env` > 项目根 `.env`」优先级读取（`NEWAPI_BASE_URL` / `NEWAPI_ACCESS_TOKEN` / `NEWAPI_USER_ID`）。`sanitize.js` 提供统一脱敏（sk-、Bearer、user:pass@host、敏感字段名），属风险削减手段而非形式化解析器。
- **Windows / Git Bash 踩坑**：`taskkill` / `tasklist` 的 `/PID`、`/FI` 会被 MSYS 路径转换，必须写 `//PID`、`//FI`；调脚本时 `/api/...` 路径参数同样会被吃掉，必须加前缀 `MSYS_NO_PATHCONV=1`；Git Bash 里不能执行 `Out-File`、`type` 等 PowerShell 命令。

## 安装与使用

把本仓库目录内容复制到技能目录，文件夹名保持 `newapi-gateway-ops`：

- WorkBuddy / CodeBuddy：`~/.workbuddy/skills/newapi-gateway-ops/`
- Claude Code：`~/.claude/skills/newapi-gateway-ops/`

重启会话后通过触发词自动匹配。使用时注意：

1. **开场必做**：先确认网关地址（默认 `http://127.0.0.1:3000`）、真实数据库位置、令牌类型，不要硬编码。
2. 令牌安全场景（复制 / 注入配置 / 命令行 / 查泄密）→ 读 `docs/token-security.md` 按其执行。脚本需要 `.env` 配置，报 `[CONFIG_MISSING]` 时停止重试，从密钥保管箱取 access_token 补进 `.env`。
3. Git Bash 下调 `scripts/api.js` 等脚本一律加 `MSYS_NO_PATHCONV=1` 前缀。

## 项目结构

```
newapi-gateway-ops/
├── README.md                 # 本文（中文）
├── README_EN.md              # 英文版 README
├── SKILL.md                  # 技能主文件：诊断三步法、三处联动改模型、上线流程、铁律红线、踩坑清单
├── docs/
│   └── token-security.md     # 令牌安全模块：安全准则 6 条 + 三脚本用法 + 令牌管理 + 脱敏规则
├── scripts/
│   ├── probe_channels.py     # 逐渠道直连探测（指定模型/间隔/渠道），区分限流类型
│   ├── api.js                # 管理 API 统一调用器（/api/token* 响应自动打码）
│   ├── copy-key.js           # 复制密钥到剪贴板（只经管道，不明文输出）
│   ├── inject-key.js         # 配置文件占位符注入（--scan 脱敏扫描 + 原子写回）
│   ├── exec-token.js         # 命令行占位符替换执行（输出脱敏 + 透传退出码）
│   ├── env.js                # 配置读取支撑模块（不单独调用）
│   ├── fetch-key.js          # 密钥获取支撑模块（不单独调用）
│   └── sanitize.js           # 统一脱敏规则支撑模块（不单独调用）
├── .env.example              # 配置模板（NEWAPI_BASE_URL / NEWAPI_ACCESS_TOKEN / NEWAPI_USER_ID）
└── .gitignore
```

## 注意事项

- **改库 / 改配置前先备份**，文件名带日期；SQLite 备份用 backup API 而不是裸 copy。
- ⛔ **按次计费的渠道（生图 / 生视频）绝不能纳入定时测活**——每测一次真扣一次钱。
- ⛔ **429 绝不能进渠道拉黑名单**（`AutomaticDisableStatusCodes` 只留 401 之类的鉴权错误），分钟级限流的 429 一旦触发拉黑会引发「重试 × 拉黑」雪崩。
- ⛔ **`sk-` 密钥绝不进对话 / 日志 / 命令参数**；不许擅自动「自动测试」类定时开关，要改先问、先说明钱的风险。
- 本技能面向**自建的** new-api / one-api 网关，不适用于官方 SaaS 服务；令牌安全模块吸收自社区版 [QuantumNous/skills](https://github.com/QuantumNous/skills) 并做了本机中文化适配。

## License

MIT

## 作者

sheen945
