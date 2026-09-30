# 令牌安全使用模块（不暴露 sk- 密钥）

> 场景：需要把网关的 `sk-` 令牌复制给别人/写进配置文件/塞进命令行，但又不能让密钥明文出现在对话、日志、命令参数里。
> 本模块吸收自社区版 newapi 技能（QuantumNous/skills，https://github.com/QuantumNous/skills），已做本机中文化适配。

## 安全准则（碰密钥必守）

1. **绝不**在聊天、文件、代码、日志、命令参数里暴露 `sk-` 密钥明文
2. 用密钥只走本模块的三个脚本（剪贴板/配置文件/命令替换），不自己 curl 取 key
3. **绝不**读取 `.env`、含凭据的环境变量、`copy-key.js` 之后的剪贴板
4. 看可能含密钥的配置文件时，先用 `--scan` 拿脱敏视图，不直接读原文件（scan 是尽力脱敏，不是 100% 保证）
5. 新建令牌后**不要**再调接口去取/列它的 key，直接告诉用户用三种安全方式之一取用
6. 不许改这些脚本来关闭打码或重定向输出

## 配置（首次使用前确认一次）

脚本需要三个变量，按优先级：环境变量 > 技能目录 `.env` > 项目根 `.env`：

```
NEWAPI_BASE_URL=http://127.0.0.1:3000
NEWAPI_ACCESS_TOKEN=<users.id=1 的 access_token>
NEWAPI_USER_ID=1
```

本机技能根目录已带 `.env`（从社区版技能平移而来，含本机网关配置），通常开箱即用。
若报 `[CONFIG_MISSING]`：停止重试，从密钥保管箱取 access_token 补进 `.env`（⚠️ `.env` 不许提交 git、不许读内容到对话里）。

运行时：纯 JS 零依赖，用 node 直接跑（本机 `node` 即可，脚本在 `scripts/` 下，以下 `$S` 代指该目录绝对路径）。

⚠️ **Git Bash 调用这些脚本时**，`/api/...` 开头的路径参数会被 MSYS 路径转换吃掉（拼成 `...PortableGit.../api/...` 导致 Failed to parse URL）。必须加前缀：
```bash
MSYS_NO_PATHCONV=1 node "$S/api.js" GET /api/user/self
```
（PowerShell / cmd 调用无此问题。2026-09-30 实测踩坑。）

## 三种安全用法

### 1. 复制密钥到剪贴板（给人用）

```bash
node "$S/copy-key.js" <token_id>
```

密钥只经管道进系统剪贴板（Windows 用 clip.exe），stdout 只输出固定成功文案。⛔ 之后不许读剪贴板。

### 2. 注入配置文件（给读文件的程序用，如 config.json / .env / yaml）

```bash
# 第 1 步（文件已存在时必做）：脱敏扫描，了解结构
node "$S/inject-key.js" --scan <文件路径>

# 第 2 步：编辑文件，把密钥字段写成占位符 __NEWAPI_TOKEN_<token_id>__
#   JSON: "apiKey": "__NEWAPI_TOKEN_42__"   ENV: OPENAI_API_KEY=__NEWAPI_TOKEN_42__

# 第 3 步：脚本取真实密钥替换占位符，原子写回（失败不动原文件）
node "$S/inject-key.js" <token_id> <文件路径>
```

⛔ 全程不许直接 Read/cat 目标文件，写完后也不许回读验证——信脚本的成功文案。占位符格式严格 `__NEWAPI_TOKEN_{id}__`，不变体。

### 3. 命令里安全替换（给 CLI 程序用，如 `xxx config set apiKey <key>`）

```bash
node "$S/exec-token.js" <token_id> -- <命令，密钥位置写 __NEWAPI_TOKEN_<token_id>__>
# 例：node "$S/exec-token.js" 42 -- somecli config set apiKey __NEWAPI_TOKEN_42__
```

脚本替换占位符后在子 shell 执行，stdout/stderr 脱敏后返回（sk-、Bearer、user:pass@host、敏感字段值都会打码），并透传子命令退出码。

## 令牌管理（列/建/改组）

统一调用器（`/api/token*` 的响应自动打码 key 字段）：

```bash
node "$S/api.js" <METHOD> <PATH> [JSON_BODY]
```

- **列令牌**：`node "$S/api.js" GET "/api/token/?p=0&page_size=20"`
  - 返回的 `key` 字段不带 `sk-` 前缀，展示时补上（如 `sk-reHR**********OspA`）；status：1=启用 2=禁用 3=过期
- **建令牌**：`node "$S/api.js" POST /api/token/ '{"name":"<名字>","group":"<组>","remain_quota":<额度>,"unlimited_quota":<是否无限>}'`
  - 额度单位是美元×500000；建完只报 ID/名字，⛔ 不许再调接口取 key
- **改分组**：先 `GET /api/token/<id>` 拿全字段，再 `PUT /api/token/` 整体写回（改 group 字段）

## 脱敏规则说明（sanitize.js）

`--scan` 和 `exec-token` 输出共用同一套打码：`sk-xxxx`、Bearer token、连接串里的 `user:pass@host`、敏感字段名（password/apiKey/secret/token/credential/auth/private_key/access_key/client_secret 等）的值。支持 JSON/YAML/ENV/TOML 类格式。属于风险削减手段，不是形式化解析器，极端格式可能漏——心里有数。
