# EVE 项目交接文档（v0.8.0）

> 给下一个对话用。读完这份就可以接着干，不需要翻聊天记录。
> 仓库：https://github.com/ws050525/EvE （分支 `main`，最新标签 `v0.8.0`）

---

## 0. 一句话现状

一个本地优先的个人 agent：**本地模型陪聊 + 操作电脑，云端模型干重活**。
界面（PySide6）、人格、事件通道、工具调用、云端接入都已跑通。
v0.6.0 修掉"上下文会爆"和"work 档工具是空的"；v0.7.0 补上成本可见与模型选择；
v0.8.0 把上下文预算从写死的 3500 改成**按窗口自动**（本地约 7.2k），步数上限从 6 提到 12，
两者都能在界面上点着调。
**下一步是 `app_launch` 支持开始菜单，再往记忆/语音走。**

---

## 1. 环境事实（别搞错）

| 项 | 值 |
| --- | --- |
| 工作目录 | `D:\1\EVE` |
| Python | **`D:\python\python.exe`（3.11.9，不在 PATH 里，必须写全路径）** |
| GUI | PySide6 6.9.2（全局已装） |
| GPU | RTX 4060 Laptop **8 GB**；内存 63.6 GB |
| 本地模型 | `models/Qwen3-VL-4B-Instruct-GGUF/.../Qwen3VL-4B-Instruct-Q4_K_M.gguf` + mmproj F16 |
| 推理引擎 | `D:/1/Aico AI - 1.1/llama.cpp/llama-server.exe`（**别的项目的目录，不能改动**；长期应复制到本项目 `tools/`） |
| 云端 | DeepSeek，模型 `deepseek-flash` / `deepseek-v4-pro` |
| 密钥 | `.data/keys.json`，DPAPI 加密，KeyStore id = `deepseek`（`.data/` 已 gitignore） |

网络实测：`api.deepseek.com` 通；`api.openai.com` **超时**；`cn.bing.com` / 百度通；DuckDuckGo 不通。

---

## 2. 怎么跑

```powershell
# 命令行
& 'D:\python\python.exe' eve_cli.py

# 图形界面
& 'D:\python\python.exe' eve_gui.py

# 单元测试（不需要模型；5 个文件共 63 项）
& 'D:\python\python.exe' tests\test_persona.py
& 'D:\python\python.exe' tests\test_context.py      # 上下文裁剪与摘要
& 'D:\python\python.exe' tests\test_workspace.py    # fs_* / shell_exec 的边界
& 'D:\python\python.exe' tests\test_cost.py         # 计费与用量账本
& 'D:\python\python.exe' tests\test_agent_loop.py   # 真配置 + 桩模型的主循环
```

常用斜杠命令：`/status`（运行时 + 上下文 + 会话花费）、`/cost`（按模型拆账）、
`/models`（含单价）、`/use <id>`（换当前档位的模型）、`/persona`、`/tools`。

### 开发期开关（都是环境变量）

| 变量 | 作用 |
| --- | --- |
| `EVE_MODE` / `EVE_MODE_DELAY` | 启动后自动切档（`chat`/`desktop`/`work`），单位毫秒 |
| `EVE_MODEL` / `EVE_MODEL_DELAY` | 启动后自动换模型（如 `deepseek-pro`） |
| `EVE_DEMO` / `EVE_DEMO_DELAY` | 自动发一句话，用于冒烟测试 |
| `EVE_AUTO_CONFIRM=1` | 跳过工具确认弹窗 |
| `EVE_SHOT` / `EVE_SHOT_DELAY` | 窗口自身截图后退出 |
| `EVE_SHOT_SCREEN` | 整屏截图（主窗收起成悬浮球时用） |
| `EVE_SHOT_ORB` | 只截悬浮球 |
| `EVE_STATUS=1` | 强制显示 CLI 状态行（非 TTY 时） |

### `config.toml` 里跟 agent 行为有关的旋钮（都在 `[app]`，`context_tokens` 也可以写在 `[modes.*]`）

| 键 | 作用 |
| --- | --- |
| `context_tokens` | prompt 预算；`0` = 按窗口自动（窗口 − 输出 − 512，下限 1024、上限 24000） |
| `keep_recent_turns` | 压缩上下文时至少保留最近几轮原文，默认 4 |
| `tool_output_chars` | 单个工具结果最多入库多少字符，默认 6000 |
| `work_root` | `fs_*` 与 `shell_exec` 的根目录，越界一律拒绝，默认项目根 |
| `max_steps` | 单条消息最多走几步，默认 12（v0.8.0 前写死 6） |

预算的取值顺序：**运行时设定 > 档位 `context_tokens` > `[app].context_tokens` > 按窗口自动**，
最后再按 `模型窗口 − max_tokens − 128` 兜一次底，永远不会超窗。
运行时改：界面点顶栏 `ctx` 胶囊；CLI 用 `/context 8000` 或 `/context auto`。

---

## 3. 目录结构

```
eve_core/            核心，不含 UI 与 agent 循环
  config.py          解析 config.toml → AppConfig / ModelConfig / ModeConfig
  provider.py        OpenAI 兼容的 chat() 与 stream()（SSE）
  runtime.py         RuntimeManager：把 model_id 变成可用的 Provider
                     LocalEngine 负责拉起/停止 llama-server、动态端口、探活
  catalog.py         扫描 GGUF、配对 mmproj、解析量化等级
  secrets.py         Windows DPAPI 加密的 KeyStore
  pricing.py         单价 → 花费：usage 解析、缓存折扣、金额格式化
  usage.py           会话账本：每次调用记账，按模型汇总
  events.py          极简事件总线（agent → UI）
  version.py         0.8.0

eve_agent/           agent 层
  loop.py            Agent 主循环：人格注入、流式、工具调用、上下文预算、日志
  context.py         上下文预算：token 估算、按轮裁剪、滚动摘要、兜底截断
  persona.py         人格状态机：命令识别、抗议分级、颜文字节流
  tools/registry.py  工具注册表（含风险分级与确认策略）
  tools/builtin.py   app_launch、screen_capture
  tools/desktop.py   窗口枚举/激活/关闭、键鼠注入
  tools/workspace.py fs_read / fs_write / fs_list（限制在 work_root 内）
  tools/shell.py     shell_exec（底线黑名单 + 越界拦截）
  tools/web.py       web_search / http_get（双后端：DeepSeek API 或 Bing）

eve_ui/             界面层
  app.py             入口、开发期开关
  main_window.py     主窗口、空态→对话过渡、档位/模型菜单、成本标签、工具确认
  chat_view.py       气泡消息、工具调用行、流式追加
  mobius.py          莫比乌斯缎带（QPainter，参数化）
  orb.py             悬浮球 + 气泡（agent 模式）
  bridge.py          Agent 跑在 QThread，事件/确认信号桥；顺带整理模型清单
  theme.py           配色、字体、QSS

config.toml          模型（含单价）、档位、引擎、上下文预算、work_root
prompts/             chat / desktop / work 三份 system prompt
docs/                设计文档（见 §7）
tests/               纯逻辑与集成测试（5 个文件 / 63 项，不需要模型）
```

---

## 4. 核心设计决策（别推翻，除非有充分理由）

1. **手动切档，不做自动路由。** 阈值不可调，错判代价是双向的。档位 = prompt + 工具集 + 默认模型。
2. **模型是一等公民。** `[modes.*]` 只绑 `model_id`；本地/云端靠 `kind` 区分，换模型只改配置。
3. **本地引擎懒加载。** 首次真正要请求时才拉起 llama-server，动态端口，退出即释放。`/models` `/scan` 不加载模型（0.2 秒）。
4. **人格 = 硬编码底线 + 演化覆盖层（未实现）。** 底线（不动鼠标、不关机、关键处多问）永不可被生成内容覆盖。
5. **`key_type` 一律走剪贴板粘贴**，不用 `pyautogui.write`。原因：输入法会吞掉空格和组合键（实测 `hello eve` 变 `helloeve`）。
6. **工具确认走"子线程阻塞 + 主线程弹窗"**，结果必须**直接调 `resolve_confirm()`**，不能走信号——worker 阻塞时事件循环停摆，信号会死锁。
7. **低风险工具不弹确认**（读窗口、搜索、抓网页）；中风险（启动程序、打字）和高风险（点击、写文件、跑命令）才确认。
8. **上下文按轮裁，摘要只在超预算时写。** 每步请求前估算一次；system prompt 永不裁；tool 消息必须和它的 `assistant.tool_calls` 同进同出，否则请求体非法。
9. **work 档的边界写在代码里，不写在提示词里。** `fs_*` 和 `shell_exec` 都锁在 `work_root`，shell 另有底线黑名单。提示词管不住模型幻觉，路径检查和黑名单管得住。
10. **单价是一等配置，账本跟着 Agent 走。** 价格写 `[models.*]`，改价不动代码；用量按会话累计，切档**不清零**（不然"刚才犹豫切来切去花了多少"就看不出来了）。

---

## 5. 已完成（按标签）

| 版本 | 内容 |
| --- | --- |
| v0.1 | 骨架：配置、provider 抽象、多轮对话、切档、日志 |
| v0.2 | desktop 档：窗口枚举/激活、键鼠注入、中文输入 |
| v0.3 | 重复启动复用窗口、window_close |
| v0.4 | 模型抽象：ModelCatalog + RuntimeManager、热切换、DPAPI 密钥 |
| v0.4.1 | 修复窗口标题异步变化导致的定位失败 |
| v0.4.2 | 按功能拆包 `eve_core` / `eve_agent` / `eve_ui` |
| v0.4.3 | 事件总线 + 人格状态机 + 10 项单测 |
| v0.5.0 | PySide6 主窗口、参数化莫比乌斯、agent 跑子线程 |
| v0.5.1 | 流式输出（SSE）、空态建议入口 |
| v0.5.2 | 头像 + 左右气泡 + 时间戳 + 淡入 |
| v0.5.3 | 空态→对话过渡：莫比乌斯飞入顶栏 |
| v0.5.4 | 档位菜单、工具确认弹窗、工具调用行 |
| v0.5.5 | 悬浮球（玻璃球 + 光点眼 + 进度气泡），agent 模式自动收起 |
| v0.5.6 | 联网工具 web_search / http_get |
| v0.5.7 | 云端接入（DeepSeek Flash/Pro）+ DeepSeek 官方 Search/Fetch 后端 |
| v0.6.0 | 上下文预算（按轮裁剪 + 滚动摘要 + 兜底截断）；work 档工具落地（fs_read / fs_write / fs_list / shell_exec） |
| v0.7.0 | 成本可见（单价进配置、会话账本、CLI 与顶栏展示）；模型选择（点模型名切档内模型，带单价）；流式也能拿到 usage |
| v0.8.0 | 上下文预算改为自适应（本地 ≈7.2k，云端 12k），可在界面/CLI 运行时调整；步数上限 6 → 12 且可调 |

### 上下文开销实测（v0.8.0）

token 数用 `eve_agent.context.estimate_tokens` 估算（偏保守，实测比 llama.cpp 真实分词高约 20%）。

| 档位 | system prompt | 工具 schema | 固定开销 | prompt 预算 | 留给对话 |
| --- | --- | --- | --- | --- | --- |
| chat | 397 | 1（无工具） | 398 | 7168（自动） | ≈6770 |
| desktop | 410 | 1091（10 个工具） | 1501 | 7168（自动） | ≈5660 |
| work | 244 | 1106（6 个工具） | 1350 | 12000（档位指定） | ≈10650 |

结论：**固定开销 400–1500 tokens**，工具多的档位四分之一预算被 schema 吃掉。
本地 8192 窗口下 auto 给到 7168，输出留 512、兜底再留 128，不会超窗。
云端的 12000 不是窗口限制（131072），是**钱**的限制：pro 跑满 12k prompt 约 ¥0.11 / 轮。

### 实测数据

| 项 | 数值 |
| --- | --- |
| 本地生成速度 | 约 66 tok/s（Qwen3-VL-4B Q4_K_M） |
| 本地引擎加载 | 3.4–3.8 秒 |
| 热态首字延迟 | 0.06 秒（流式） |
| 云端单轮（flash） | 约 1.3 秒 |
| 桌面自动化（开记事本→输入→全选→复制） | 6 步，约 4 秒，剪贴板验证通过 |
| 研究任务·本地 | 5 步 / 约 7 秒，总结正确但偏教科书 |
| 研究任务·云端 pro | 3 步 / 约 28 秒，选源更准、总结更有洞察 |
| 上下文压缩（本地 4B） | 10194 → 471 tokens，摘要 46 字，关键事实（暗号 42）保住 |
| 云端流式计费（flash，一句话） | 344 tokens（入 323 / 出 21，缓存命中 128）→ ¥0.0006 |
| 本地流式 usage | 支持，llama.cpp 会回 prompt/completion + 缓存命中数 |

---

## 6. 已知问题（下一个对话大概率要碰）

### 高优先级

1. **`app_launch` 找不到开始菜单程序。** 只查 PATH 和绝对路径，所以「打开微信」这类会失败。修法：扫 `开始菜单\Programs\*.lnk`，或走 `shell:AppsFolder`。

### 中低优先级

2. 悬浮球气泡偶尔报 `QWindowsWindow::setGeometry` 警告（布局高度与请求不符），纯观感。
3. 悬浮球拖动没有边缘吸附。
4. 工具调用行只显示摘要，点不开详情。
5. 流式只在**无工具**的档位生效（工具调用参数分片拼装故意没做）。

### v0.6.0 引入的已知边界（不是 bug，是取舍）

6. 兜底截断会真的切 content：被切过的工具结果只剩前半段 + 一行提示，模型看不到后半段。
7. 摘要由当前档位的模型写。本地 4B 写得糙（事实能保住，措辞一般）；云端 pro 明显更好。
8. `fs_*` / `shell_exec` 都锁在 `work_root`（默认项目根）。要碰别处只能改配置，或让用户把文件搬进来。
9. `shell_exec` 是 cmd 语法 + 黑名单，**不是沙箱**：黑名单挡的是关机 / 格式化 / 删盘这类不可逆操作，挡不住普通误操作。确认弹窗仍是最后一道防线。

### v0.7.0 引入的已知边界

10. **成本是估算，不是账单。** 单价写在 `config.toml` 的 `[models.*]` 里，填的是高峰档上限，实际空闲档更便宜。DeepSeek 调价就得改配置。
11. **只算 token，不算 Search/Fetch 的按次收费**（各 5 元 / 1000 次）。量小可以忽略，但研究任务跑多了这部分会漏。
12. 账本在内存里，进程退出即清零；没有落盘，也没有跨天统计。
13. 顶栏的模型菜单只在**当前档位内**换模型（等价于 CLI 的 `/use`）；要换档位的默认模型仍然得改 `config.toml`。
14. 流式 usage 依赖 `stream_options.include_usage`。llama.cpp 和 DeepSeek 都支持；万一换成不支持的后端，代码会自动去掉该字段重试一次，代价是这一轮没有 token 数。

### v0.8.0 引入的已知边界

15. **预算再大也压不过模型窗口。** 手动设 24000 在本地 8192 的模型上会被夹到 7552，这是有意的兜底，不是没生效。
16. **改预算不会立即重算已压缩的历史。** 摘要已经写进 `_summary` 了，预算调大只是"以后不再压这么狠"，之前压掉的内容回不来（要重来就 `/reset`）。
17. 步数上限调大意味着长任务可能跑很久、烧很多 token（云端按轮计费），没有中途成本熔断——只有 `MAX_STEPS_CAP = 60` 和手动停止。
18. 界面上的 `ctx` 胶囊显示的永远是**当前生效值**（如 7.2k），不是"自动"这个设定；设定本身在 tooltip 与菜单勾选里。

---

## 7. 设计文档索引

| 文件 | 内容 |
| --- | --- |
| `docs/design-mvp.md` | 最早的架构决策、模型选型、桌面自动化原则 |
| `docs/design-v2-ui-voice.md` | 模型可换 / UI / 语音 / 打包的规划（语音尚未开始） |
| `docs/design-ui.md` | 视觉规范、莫比乌斯状态机、悬浮球与气泡规则 |
| `docs/design-mascot.md` | 形象系统：玻璃球 + 光点眼、表情参数、微行为 |
| `docs/design-persona.md` | 伊芙人格说明书（含底线条款） |
| `docs/design-memory.md` | 记忆与人格演化方案（**已设计，未实现**） |

---

## 8. 下一步：继续打磨 agent

排在前面的是"不做就会绊倒"的：

1. **`app_launch` 支持开始菜单**（见 §6.1）
   扫 `%APPDATA%\Microsoft\Windows\Start Menu\Programs\**\*.lnk` 和 `%ProgramData%` 那份，
   建立"中文名 → 目标 exe/lnk"的索引；找不到再退回 `shell:AppsFolder`。

2. **工具可观测性**：工具调用行点开看完整参数/输出；流式下拼装工具调用参数分片。

3. **成本再进一步**（v0.7.0 只做到"看得见"）：
   - 账本落盘，支持"今天花了多少 / 这个月花了多少"
   - 按次收费的 Search / Fetch 也记进去
   - 给 work 档设单轮成本上限，超了就提示换 flash

4. 之后可选：记忆系统落地（`design-memory.md`）、语音（两段式唤醒 + piper TTS）

### v0.6.0 / v0.7.0 / v0.8.0 留下的坑（做的时候顺手看一眼）

- 摘要质量依赖模型。本地 4B 写摘要能用但糙；如果以后要"高质量长期记忆"，考虑把摘要固定交给云端 flash 做，而不是当前档位模型。
- `MIN_SUMMARY_CHARS = 8` 是一道土办法护栏（挡"复读型摘要"）。真正的问题是模型把记录里的指令当成写给它的——已经用 `【对话记录】` 框住缓解了，换模型后要重测。
- 上下文预算的 token 估算是启发式的（CJK 1 token/字，其余 3.3 字符/token），偏保守约 20%。如果以后接 tiktoken 或 llama.cpp 的 tokenize 接口，可以换掉；换掉之后 auto 预算还能再往上放。
- 预算现在更大，压缩触发得更晚：一次压缩要压在更长的历史上，摘要输入会顶到 `MAX_SUMMARY_INPUT = 12000` 字符的上限。如果发现摘要开始丢细节，先看是不是被这个上限截了尾部。
- 摘要调用也会进账本（`call_kind="summary"`），界面上只更新累计金额、不更新"本次"那行——别以为是漏记了。

---

## 9. 开发约定（血泪教训）

### PowerShell 写文件

**不要用 `Set-Content -Encoding UTF8`**（会写 BOM，Python/TOML 都可能炸）。用：

```powershell
$enc = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($path, $text, $enc)
```

### TOML 表名

`[engines.llama.cpp]` 会被解析成嵌套表（点号是分隔符）。用 `[engines.llamacpp]`。

### git 提交信息

带引号、`+`、多行中文的 `-m` 可能被 shell 打断。用多个 `-m`，每段短句，避开 `"` 和 `+`。

### 管道喂中文给 Python

```powershell
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
```

### Qt 的坑

- 半透明窗口（`WA_TranslucentBackground`）隐藏子控件后**不会自动清屏**，要手动 `update()`。
- `QScrollArea` 的 viewport 有 palette 底色，必须用**对象名选择器**写 QSS 才能透明。
- 工具确认结果**不能通过信号回传**给正在阻塞的 worker（会死锁），要直接调用方法置位 `Event`。

### 工具开发的坑

- 输入文字一律走剪贴板（输入法会吞字符）。
- `app_launch` 拿到的窗口标题会异步变化（`记事本` → `无标题 - Notepad`），要等 0.6 秒重读，并优先用 hwnd。
- Bing 搜索用 `format=rss`，别抓 HTML；且**整句问句会返回垃圾结果**，要用短关键词。

---

## 10. 未决问题（需要用户拍板）

1. **记忆/人格演化要不要做？** 方案在 `design-memory.md`，用户当时的评价是"先想想"。
2. **云端成本策略**：`work` 档默认用 pro（贵），要不要默认 flash、手动升级？
   v0.7.0 之后顶栏点模型名就能临时换，但**默认值**还没定。
3. **UI 模型面板**只做了一半：能切档内模型、能看到单价、能调预算与步数；还不能选 GGUF、不能存 key（仍然靠 `/key` 和改配置）。
4. **预算与步数的默认值合不合适？** 现在本地 auto ≈7.2k、云端 work 12k、步数 12。
   如果长任务还是不够用，下一步应该做"按任务自动放大"，而不是继续调默认值。

---

## 11. 给下一个对话的开场白

> 读 `docs/HANDOFF.md`，我们接着打磨 agent。先做 `app_launch` 支持开始菜单（§8.1）。
