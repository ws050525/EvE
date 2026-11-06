# EVE 项目交接文档（v0.5.7）

> 给下一个对话用。读完这份就可以接着干，不需要翻聊天记录。
> 仓库：https://github.com/ws050525/EvE （分支 `main`，最新标签 `v0.5.7`）

---

## 0. 一句话现状

一个本地优先的个人 agent：**本地模型陪聊 + 操作电脑，云端模型干重活**。
界面（PySide6）、人格、事件通道、工具调用、云端接入都已跑通；**下一步是打磨 agent 本身**。

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

# 单元测试（不需要模型）
& 'D:\python\python.exe' tests\test_persona.py
```

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
  events.py          极简事件总线（agent → UI）
  version.py         0.5.7

eve_agent/           agent 层
  loop.py            Agent 主循环：人格注入、流式、工具调用、日志
  persona.py         人格状态机：命令识别、抗议分级、颜文字节流
  tools/registry.py  工具注册表（含风险分级与确认策略）
  tools/builtin.py   app_launch、screen_capture
  tools/desktop.py   窗口枚举/激活/关闭、键鼠注入
  tools/web.py       web_search / http_get（双后端：DeepSeek API 或 Bing）

eve_ui/             界面层
  app.py             入口、开发期开关
  main_window.py     主窗口、空态→对话过渡、档位菜单、工具确认
  chat_view.py       气泡消息、工具调用行、流式追加
  mobius.py          莫比乌斯缎带（QPainter，参数化）
  orb.py             悬浮球 + 气泡（agent 模式）
  bridge.py          Agent 跑在 QThread，事件/确认信号桥
  theme.py           配色、字体、QSS

config.toml          模型、档位、引擎
prompts/             chat / desktop / work 三份 system prompt
docs/                设计文档（见 §7）
tests/               纯逻辑测试
```

---

## 4. 核心设计决策（别推翻，除非有充分理由）

1. **手动切档，不做自动路由。** 阈值不可调，错判代价是双向的。档位 = prompt + 工具集 + 默认模型。
2. **模型是一等公民。** `[modes.*]` 只绑 `model_id`；本地/云端靠 `kind` 区分，换模型只改配置。
3. **本地引擎懒加载。** 首次真正要请求时才拉起 llama-server，动态端口，退出即释放。`/models` `/scan` 不加载模型（0.2 秒）。
4. **人格 = 硬编码底线 + 演化覆盖层（未实现）。** 底线（不动鼠标、不关机、关键处多问）永不可被生成内容覆盖。
5. **`key_type` 一律走剪贴板粘贴**，不用 `pyautogui.write`。原因：输入法会吞掉空格和组合键（实测 `hello eve` 变 `helloeve`）。
6. **工具确认走"子线程阻塞 + 主线程弹窗"**，结果必须**直接调 `resolve_confirm()`**，不能走信号——worker 阻塞时事件循环停摆，信号会死锁。
7. **低风险工具不弹确认**（读窗口、搜索、抓网页）；中风险（启动程序、打字）和高风险（点击）才确认。

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

---

## 6. 已知问题（下一个对话大概率要碰）

### 高优先级

1. **上下文会爆。** `Agent.send()` 每轮把完整历史发出去，没有裁剪。本地 8192 窗口，长任务（研究任务已到 4165 tokens）很快就会撞墙。**这是当前最该修的问题。**
2. **`work` 档的工具是空的。** `config.toml` 里写着 `fs_read` / `fs_write` / `shell_exec`，但**这三个工具根本没实现**；`Agent` 会静默过滤掉缺失工具，所以 work 档实际只有 `web_search` / `http_get`。要么实现，要么从配置里删掉。
   （实测：已注册工具共 10 个 —— app_launch / window_list / window_focus / window_close / key_type / key_hotkey / mouse_click / screen_capture / web_search / http_get）
3. **`app_launch` 找不到开始菜单程序。** 只查 PATH 和绝对路径，所以「打开微信」这类会失败。修法：扫 `开始菜单\Programs\*.lnk`，或走 `shell:AppsFolder`。
4. **没有成本可见性。** 云端调用不知道花了多少钱；`usage` 已经拿到了，但没展示也没累计。

### 中低优先级

5. 悬浮球气泡偶尔报 `QWindowsWindow::setGeometry` 警告（布局高度与请求不符），纯观感。
6. 悬浮球拖动没有边缘吸附。
7. 工具调用行只显示摘要，点不开详情。
8. 流式只在**无工具**的档位生效（工具调用参数分片拼装故意没做）。

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

## 8. 下一步：打磨 agent（用户明确指定的方向）

建议按这个顺序，前两项是"不做就会绊倒"的：

1. **上下文裁剪 + 会话摘要**
   保留最近 N 轮原文，更早的压成摘要；总 prompt 卡在 3500 tokens 以内（本地）。
   这一步和 `design-memory.md` 的短期部分是同一件事。

2. **补齐 work 档工具**
   `fs_read` / `fs_write` / `fs_list` / `shell_exec`（高风险，需确认）。
   做完 work 档才真的能"整理文档、写代码"。

3. **成本与用量可见**
   每次调用累计 prompt/completion tokens，按模型单价估算，显示在状态栏 / `/status`。
   DeepSeek 价格（每百万 tokens，空闲/高峰两档）：flash 输入 1–2 元、输出 4–8 元；
   pro 输入 4.5–9 元、输出 13.5–27 元。缓存命中的输入更便宜。
   Search / Fetch 各 5 元每 1000 次成功请求。

4. **`app_launch` 支持开始菜单**（见 §6.3）

5. 之后可选：记忆系统落地、工具详情面板、语音（两段式唤醒 + piper TTS）

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
3. **UI 模型面板**（选 GGUF / 切云端 / 存 key）——目前只能改配置或 `/use`、`EVE_MODEL`。

---

## 11. 给下一个对话的开场白

> 读 `docs/HANDOFF.md`，我们接着打磨 agent。先做上下文裁剪。
