# newapi-gateway-ops

> 自建 New API / one-api 网关的日常运维与故障排查。用于「模型不可用 / 调用变慢 / 偶发失败 / 渠道 429 限流 / 模型列表缺失 / 增删模型 / 批量改渠道 / 查余额 / 管理 API 令牌 / 安全使用 sk- 密钥（复制、写入配置、命令行替换，不暴露明文）」等场景。核心是一套三处联动改模型（channels + abilities + options）的标准流程、区分「上游限流」与「本地配置错误」的诊断方法，以及令牌安全四件套脚本。

## 安装

把本仓库的目录内容复制到你的技能目录下，文件夹名保持 `newapi-gateway-ops`：

- WorkBuddy / CodeBuddy：`~/.workbuddy/skills/newapi-gateway-ops/`
- Claude Code：`~/.claude/skills/newapi-gateway-ops/`

重启会话后即可通过触发词自动匹配。

## 技能说明

以下为 `SKILL.md` 正文。

---

# New API 网关运维与限流诊断

维护自建 new-api（one-api 系）网关：诊断上游渠道限流、增删模型、批量改渠道、排查"模型不可用/变慢"。

## 0. 开场必做：确认环境（不要硬编码，先问或先探）

- **网关地址**：默认 `http://127.0.0.1:3000`
- **真实数据库**：`<网关安装目录>/one-api.db` —— 工作区里若有一份同名文件，通常是 0 字节占位，**不是真库**
- **API 令牌**：调用 `/v1/*` 用用户令牌（`sk-` 开头）；调 `/api/*` 管理接口用 `users.id=1` 的 `access_token`
- ⚠️ **别搞混**：`/v1/models` 用管理员 access_token 会返回 **401**，容易误判成"模型列表为空"。查模型列表必须用 sk- 令牌

## 1. 诊断：模型"变慢/偶发失败"的标准三步

### 第一步：看成功记录里的重试次数（最关键）
失败请求**不落 logs**，所以"日志干净"≠没问题。要看成功记录里 `other` 字段的 `admin_info.use_channel`：

```python
import sqlite3, json
con = sqlite3.connect(DB); cur = con.cursor()
cur.execute("SELECT other FROM logs WHERE type=2 AND model_name=? ORDER BY created_at DESC LIMIT 100", (model,))
first = total = 0
for (o,) in cur.fetchall():
    ch = (json.loads(o).get('admin_info', {}).get('use_channel', []) or []) if o else []
    total += 1
    first += 1 if len(ch) <= 1 else 0
print('首次命中 %d/%d = %.0f%%' % (first, total, 100.0*first/total))
```
`use_channel` 长度 > 1 → 该请求失败重试过。首次命中率低于 ~90% 就说明上游在限流。

> `logs.type`：2=消费、3=管理、7=登录。SQLite 无 `LEFT()` 函数，用 `substr()`。

### 第二步：按小时看趋势，判断是否持续性
```sql
SELECT strftime('%m-%d %H', created_at,'unixepoch','localtime') h, COUNT(*)
FROM logs WHERE type=2 AND model_name=? AND created_at > strftime('%s','now','-2 days')
GROUP BY h ORDER BY h;
```
注意：**别把 `other` 和 `COUNT(*)` 一起 GROUP BY 后直接取**，聚合会稀释数据，要按小时分组后回表逐行统计。

### 第三步：逐渠道直连探测（区分限流类型）
用 `scripts/probe_channels.py`，直接打上游 base_url，绕开网关：

```bash
python scripts/probe_channels.py --db <one-api.db> --base-url https://token.sensenova.cn \
       --models kimi-k3 --interval 5
```

**判定规则**：
- 间隔 5~10 秒仍**持续 429** → 长冷却 / 账号级惩罚（要等几十小时）
- 隔 1 分钟就恢复、或时好时坏 → **分钟级 RPM/TPM 滑动窗口**（正常波动）
- 错误码带 `EndpointTPMExceeded` / `TpmRateLimitExceeded` → 撞的是 **token 量**，大请求（几万 token）最容易触发
- 错误码带 `RpmRateLimitExceeded` → 撞的是**请求条数**

### 补充：看网关的模型级冷却
`GET /api/channel/cooldowns`（管理员 token）→ 返回 `{channel_id, model_name, fail_streak, cooldown_until, remaining_seconds}`。有记录说明调度层已在主动避开。

## 2. 改模型：三处联动（缺一处就报 503）

**只改 `channels.models` 是不够的**，必须同步 `abilities` 表，否则重启后模型列表不含该模型、调用报 `503 No available channel for model`。

| 位置 | 作用 | 写法 |
|---|---|---|
| `channels.models` | 渠道声明支持哪些模型 | 逗号分隔字符串 |
| `abilities` | 路由表，决定模型能否被调度到 | 主键 (`group`,`model`,`channel_id`) |
| `options` 表三行 | 定价 | `ModelRatio` / `CompletionRatio` / `CacheRatio` |

**列名易混**：`channels` 表**没有** `enabled` 列（启用状态叫 `status`，1=启用）；`abilities` 表才叫 `enabled`；`group` 是 SQL 保留字**必须加双引号**。

### 推荐走管理 API（热更新，不用停服务）
改 `channels.models` 后 New API 会**自动重建 abilities**，这是最省事的路径：

```python
# 1) 取渠道（注意分页结构 data.items）
GET /api/channel/?p=0&page_size=100

# 2) 构造 payload：直接拿 GET 到的对象，剔除只读字段
READONLY = {'status','balance','balance_updated_time','created_time','response_time',
            'test_time','used_quota','channel_info','other_info'}
payload = {k:v for k,v in ch.items() if k not in READONLY}
payload['key'] = ''            # 留空 = 不更新密钥（重要！否则可能把 key 写坏）
payload['models'] = '<新模型列表，逗号分隔>'
PUT /api/channel/

# 3) 改价
GET  /api/option/              # 返回 data 是 list[{key,value}]，不是 dict！
PUT  /api/option/  body={"key":"ModelRatio","value":"<JSON字符串>"}
```

**必做验证**（四条全过才算成功）：
1. 回读渠道 → `models` 已变，且 **`status` 和 `test_model` 没被清空**
2. `SELECT COUNT(*) FROM abilities WHERE model=?` → 增删后数字正确（增 = 渠道数；删 = 0）
3. `/v1/models`（用 sk- 令牌）→ 模型数正确
4. 真实调用过一次该模型

## 3. 删模型的完整清单（别漏）
1. 各渠道 `channels.models` 移除
2. `abilities` 移除（走管理 API 会自动做；直接改库则要自己删）
3. `options` 的 `ModelRatio` / `CompletionRatio` / `CacheRatio` 移除
4. 检查**客户端配置**（如 WorkBuddy 的 `models.json`）是否也列了该模型

## 3.5 调优：动态重试次数（上游限流时的应对）

上游按账号/模型组频繁 429 时，提高重试上限可提升成功率（网关会自动换下一个渠道重试）。

**位置**：`service/channel_select.go` 的 `GetDynamicRetryTimes()`
**调用点**：`controller/relay.go` 两处（普通 relay + 流式/任务 relay），两处注释也要同步改

```go
// 当前策略（2026-09-30 起）：可用渠道数 ÷ 2 向上取整，封顶 8，最少 1
const maxDynamicRetryTimes = 8
times := (count + 1) / 2       // 12 渠道 -> 6 次重试，共 7 次尝试
```

历史策略：2026-09-27 前为 `(count+2)/3`（12 渠道 → 4 次）。`count` = `model.CountSatisfiedChannels()`，即该模型在组内**启用渠道数**（查 `abilities WHERE model=? AND enabled=1`）。

**取舍（务必向用户说清）**：
- 提高重试 = 卡顿时**等得更久**，且会向上游发更多请求，**可能延长限流惩罚**
- **全渠道都 429 时重试再多也没用**（没有可换的渠道），只对"部分渠道可用"有效
- 观察实证：日志 `use_channel` 长度分布能看出有多少请求撞到上限边界。若出现大量等于「旧上限」的记录，说明提升上限确有价值

## 3.6 改后端代码 → 上线标准流程

只改后端 Go（不动前端）时的完整链路：

```bash
# 1) 备份源码
cp service/xxx.go service/xxx.go.bak-<日期>

# 2) 编译（在源码根目录；前端未改则无需重建 web/dist）
go build -o new-api-vN-<用途>.exe .

# 3) 核对产物确实变了（大小可能巧合相同，务必比 md5）
md5sum new-api-vN-<用途>.exe <网关目录>/new-api.exe

# 4) 停服务（Git Bash 注意 //PID）
taskkill //PID <pid> //F          # pid 从 netstat -ano | grep :3000 取

# 5) 备份旧 exe 再替换
cp new-api.exe new-api.exe.bak-v<旧版本>-<日期>-pre-<新版本>
cp <源码目录>/new-api-vN-<用途>.exe new-api.exe
md5sum new-api.exe                # 确认与源码产物一致

# 6) 拉起（唯一入口是计划任务，本机拦截 Start-Process）
Start-ScheduledTask -TaskName "new-api-autostart"   # 用 PowerShell 工具
```

**验收四条**：① `netstat` 见 3000 LISTENING；② `/api/status` 200；③ `/burner` 200；④ `MainWindowHandle=0`（无窗口）。再补真实模型调用。

> 前端若改动过：必须先 `node node_modules/@rsbuild/core/bin/rsbuild.js build`（rsbuild **不是** vite）重建 `web/dist`，因为 dist 被 go:embed 打进 exe。顺序：**先前端后后端**。

## 4. 铁律与红线
- **改库/改配置前先备份**，文件名带日期。备份 SQLite 用 backup API 而不是裸 copy：
  ```python
  src = sqlite3.connect('one-api.db'); dst = sqlite3.connect('one-api.db.bak-<原因>-<日期>')
  src.backup(dst); dst.close(); src.close()
  ```
- **⛔ 按次计费的渠道（生图/生视频）绝不能纳入定时测活**——每测一次真扣一次钱。
- **⛔ 429 绝不能进渠道拉黑名单**（`AutomaticDisableStatusCodes` 只留 401 之类的鉴权错误）。分钟级限流的 429 一旦触发拉黑，会引发"重试 × 拉黑"雪崩。
- **不许擅自动「自动测试」类定时开关**（测活间隔、健康检查、定时任务）。要改先问、先说明钱的风险。
- 密钥只记位置不记明文；需要时走密钥保管箱。
- **⛔ `sk-` 密钥绝不进对话/日志/命令参数**：复制、写配置、命令行使用一律走 `docs/token-security.md` 的三脚本（剪贴板/占位符注入/命令替换），建令牌后不再回取 key。

## 5. Git Bash on Windows 踩坑
- `taskkill` / `tasklist` 的 `/PID`、`/FI` 会被 MSYS 路径转换，必须写成 `//PID`、`//FI`
- 同理，调 `scripts/api.js` 等脚本时 `/api/...` 路径参数也会被吃掉，必须加前缀 `MSYS_NO_PATHCONV=1`（2026-09-30 实测）
- Git Bash 里不能执行 `Out-File`、`type` 等 PowerShell 命令
- PowerShell 输出常不回显，需要重定向到文件再读

## 6. 脚本
- `scripts/probe_channels.py` —— 逐渠道直连探测（支持指定模型、间隔、只测某几个渠道），用于区分限流类型。
- `scripts/copy-key.js` / `inject-key.js` / `exec-token.js` —— 令牌安全三件套（剪贴板 / 配置文件占位符注入 / 命令行替换），用法见 `docs/token-security.md`。
- `scripts/api.js` —— 管理 API 统一调用器（`/api/token*` 响应自动打码）；`env.js` / `fetch-key.js` / `sanitize.js` 为其支撑模块，不单独调用。

## 7. 令牌安全使用（复制/注入配置/命令行，不暴露明文）

**触发条件**：用户要「复制某个令牌的密钥」「把网关 key 配进某个程序/配置文件」「命令行里要用 sk- 密钥」「看看配置文件里有没有泄密」「查余额/建令牌/改令牌分组」。

→ 读 `docs/token-security.md` 按其执行（安全准则 6 条 + 三脚本用法 + 令牌管理）。脚本在 `scripts/`（纯 JS 零依赖，node 直接跑；技能根 `.env` 已带本机配置，报 `[CONFIG_MISSING]` 才需从保管箱补）。

一句话原则：密钥从网关到目的地全程走脚本管道，AI 只看脱敏后的内容。

---
*令牌安全模块吸收自社区版 newapi 技能（[QuantumNous/skills](https://github.com/QuantumNous/skills)），2026-09-30 并入本技能，原技能已停用。*

