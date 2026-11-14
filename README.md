# EVE

本地优先的个人 agent。本地模型陪聊，云端模型干重活：写文章、做 PPT、查资料、改文件、操作电脑。

## 设计

- **手动切档，不做自动路由**。每个档位 = 一段 system prompt + 一组工具 + 一个默认模型。
- **模型是一等公民**。`[models.*]` 声明有什么，`[modes.*]` 只声明用哪个；换本地模型、换云端厂商都只是改配置。
- **本地模型的引擎在执行时按需拉起**，动态端口，退出即释放。
- 详细设计见 [docs/](docs/)：`design-mvp.md`（整体）、`design-ui.md`（界面）、`design-mascot.md`（形象）、`design-persona.md`（人格）。

| 档位 | 默认模型 | 工具 | 定位 |
| --- | --- | --- | --- |
| 聊天（`chat`） | 本地 GGUF | 无 | 闲聊、陪伴 |
| 桌面（`desktop`） | DeepSeek Flash（云端） | 12 个桌面 / 联网 / 计划 | 开软件、点按输入、查东西 |
| 工作（`work`） | DeepSeek Flash（云端） | 29 个（计划/多文件/浏览器/视觉/共享） | 写文章、做 PPT、查资料、改代码、操作网页和软件 |

> 桌面档也走云端：本地 4B 做工具调用参数经常写错，简单操作也会反复试错。
> 聊天档仍用本地模型——陪聊不需要那么强的算力，也不花钱。

## 多任务并行

顶栏的 **＋**（或 `Ctrl+T`）开一个新任务；任务超过一个时，顶栏下面会出现任务条：

```
● 1. 做一套 AI 报告   ✓ 2. 查一下显卡价格   ○ 3. 新任务
```

- 每个任务有自己的 **Agent 线程、对话上下文、计划清单、花费账本**，互不干扰。
- 切任务只换视图，后台任务照跑；跑完的 chip 变 ✓，状态栏会提一句。
- 同时最多 3 个在跑（`MAX_RUNNING`），再多就是烧钱和抢上下文。
- 右键任务 chip 可以"停止它 / 关闭任务"。

## 跨任务共享

并行任务之间传结论用一块"共享黑板"：

| 工具 | 作用 |
| --- | --- |
| `share_write(title, content)` | 把一条结论写进去（≤2000 字，最多 20 条） |
| `share_read(title)` | 读一条或全部；不给标题就全读 |
| `share_forget(title)` | 删掉过时的 |

- 存在 `.data/shared.json`，**重启还在**（任务条是内存里的，黑板不是）。
- 每条记来源，读的时候知道是谁说的。
- 每个任务每步都会在 system prompt 里看到黑板摘要，但不污染各自的对话历史。

## 视觉：让云端模型"看"屏幕

云端模型（DeepSeek）不收图，但桌面操作必须看。所以让**本地 Qwen3-VL 当眼睛**：

| 工具 | 作用 |
| --- | --- |
| `screen_read(question)` | 截屏 → 本地视觉模型描述界面（"搜索框在顶部中央"） |
| `screen_locate(target)` | 截屏 → 直接给**可点击的屏幕坐标**（已换算成真实坐标） |
| `image_read(path, question)` | 对已有图片提问 |

第一次调用会把本地引擎拉起来（约 30 秒、4–5GB 显存），之后每张图 12–20 秒。
不用就不占显存。`config.toml` 的 `[vision] model` 可以指定用哪个视觉模型。

## 浏览器真交互

读静态页面用 `http_get`（快、便宜）；需要登录、点按钮、翻页、填表时，agent 会调浏览器工具：

| 工具 | 干什么 |
| --- | --- |
| `browser_open(url, show=false)` | 打开页面；`show=true` 弹出真窗口，方便人工登录/过验证码 |
| `browser_read()` | 读当前页面文字 |
| `browser_click("下一页")` | 按可见文字或 CSS 选择器点击；点不到会回一份"页面上能点的东西"清单 |
| `browser_type("搜索", "关键词", submit=true)` | 填表并可回车提交 |
| `browser_wait(selector)` | 单页应用加载慢时等元素出现 |
| `browser_screenshot()` | 存 png，给用户看或当证据 |
| `browser_back()` / `browser_close()` | 后退 / 关掉浏览器 |
| `browser_tabs` / `new_tab` / `switch_tab` / `close_tab` | 多标签页：查多个来源时一页一个，来回切不丢 |

**不下载任何浏览器**：直接驱动本机已装的 Edge（走 CDP）。
默认无头；`config.toml` 的 `[browser] headless = false` 可以改成有头。

## 能干什么（复杂任务）

一次任务不是一问一答，而是"计划 → 查证 → 分块写 → 生成文件 → 自检"。`work` 档按这套流程跑：

| 环节 | 怎么做的 |
| --- | --- |
| 列计划 | `todo_write` 维护 3–8 步清单，每步更新状态，同时只允许一个进行中 |
| 查证 | 不确定的事实先 `web_search` 再 `http_get` 读正文，至少两个独立来源对得上才写进交付物 |
| 写文章 | `office_write(kind="docx")` 建文件，再 `append=true` 分块续写；一次几千字不会被输出上限截断 |
| 做 PPT | 一页一个论点，`bullets` 支持二级缩进，`notes` 写讲稿 |
| 自检 | 生成后用 `fs_list` 或 `shell_exec` 确认文件真的存在、大小合理 |
| 交付 | 说清楚文件路径 + 内容概要 + 还有什么要用户拍板 |

实测：让它做"8 页 AI 编程助手市场格局 PPT"，它会自己搜两个调查、交叉验证排名、
把口径不同的市场规模并列而不是硬凑一个数，最后把来源链接放进最后一页。

## 完全权限

顶栏那颗 `权限：询问 / 权限：完全` 胶囊，点一下切换：

- **询问**（默认）：中高风险操作（写文件、跑命令、生成文档）先弹确认。
- **完全**：全部放行，长任务不再被打断。胶囊会变成警示色，随时能看见自己在哪种状态。

选择存在 `.data/prefs.json`，下次启动仍然是这个状态；`config.toml` 里的 `auto_confirm`
只是默认值。命令行用 `--yes` 等价于完全权限。

任务跑到一半想叫停：发送按钮在忙的时候会变成 **停止**，或者按 `Esc`。

## 断点续跑与断点列表

任务被停止、或者撞到步数上限时，进度会存成一份断点（`.data/tasks/<id>.json`）：
原始要求、计划清单、已经动过的文件、对话历史。输入框旁边会出现 **继续** 按钮（`Ctrl+R`），
点一下就从断点接着做，不用把需求重讲一遍。命令行：`eve_cli.py --resume`。

**多个任务的断点各存各的**，互不覆盖：右键那颗「继续」按钮会弹出断点列表，
显示每个断点的请求摘要、进度（几/几步）和保存时间，挑一个继续。
任务正常做完会自动清掉自己那份断点，所以列表里都是真有活没干完的。

续跑时模型拿到的是一段"上次进度"（做到第几步、还差哪些、上次说到哪），
它会先看一眼现状再动手，不会把已经写完的部分重写一遍。

## 多文件资料

"做一套资料：主报告 + 附录 + 摘要 PPT"这种任务，先 `project_init` 定清单
（生成 `manifest.json` 和 `index.md`），再按清单逐篇写，最后 `project_status` 自检：

- 哪些文件还没写；
- 哪些内容过少（< 50 字，基本等于占位）；
- markdown 里的相对链接是否指得通（`[附录](02-附录.md)` 指向的文件在不在）；
- 目录里有没有清单外的多余产物。

自检不过就不交付——这样不会出现"引用了不存在的附录"或者"三篇之间说法对不上"。

## 目录结构

```
eve_core/     核心：配置、provider、运行时、模型扫描、DPAPI 密钥、事件总线
eve_agent/    agent 层：主循环、人格状态机、工具注册表与内置工具
eve_ui/       界面层：theme / mobius（莫比乌斯）/ bridge（Qt 桥）/ main_window / app
eve_cli.py    命令行入口
eve_gui.py    图形界面入口
config.toml   模型与档位配置
prompts/      各档位的 system prompt
tests/        纯逻辑单元测试（不需要模型）
docs/         设计文档与视觉稿
models/       本地模型文件（不入版本库）
logs/         会话日志与引擎日志（不入版本库）
```

## 环境

- Python 3.11：`D:\python\python.exe`（不在 PATH 里，需显式调用）
- 无第三方依赖，只用标准库（`tomllib`、`urllib`、`ctypes`）

## 快速开始

```powershell
# 图形界面（推荐）
& 'D:\python\python.exe' eve_gui.py

# 命令行
& 'D:\python\python.exe' eve_cli.py

# 指定档位 / 临时指定模型
& 'D:\python\python.exe' eve_cli.py --mode desktop
& 'D:\python\python.exe' eve_cli.py --model qwen3vl-4b-small

# 非交互跑一句
& 'D:\python\python.exe' eve_cli.py --once "你好，介绍一下你自己"
```

界面长这样：空态居中一枚莫比乌斯，开口说话后它缩进顶栏，把空间让给对话。

![空态](docs/assets/gui-empty.png)
![对话](docs/assets/gui-chat.png)

> v0.4.2 起入口拆成 `eve_cli.py` / `eve_gui.py`，原来的 `python -m eve` 已不再可用。

## REPL 命令

| 命令 | 说明 |
| --- | --- |
| `/models` | 列出已配置的模型，`*` 标出当前使用 |
| `/use <id>` | 给当前档位换模型（保留上下文，会重启本地引擎） |
| `/scan` | 扫描 `model_dirs` 下可用的 GGUF，含量化等级与 mmproj 配对 |
| `/mode <name>` | 切换档位（会重置上下文） |
| `/status` | 运行时状态：引擎、端口、pid、加载耗时、日志路径 |
| `/persona` | 人格状态：抗议等级、连续命令数、颜文字节流 |
| `/stop` | 停止本地引擎，释放显存 |
| `/cost` | 本次会话的 token 用量与估算花费（按模型拆账） |
| `/context [n\|auto]` | 查看或设置上下文预算 |
| `/steps [n]` | 查看或设置单条消息的步数上限 |
| `/key <id>` | 为云端模型保存 API Key（DPAPI 加密，不落明文） |
| `/reset` `/tools` `/where` `/help` `/exit` | 常规 |

## 配置要点

```toml
[engines.llamacpp]
bin = "tools/llama.cpp/llama-server.exe"

[models.qwen3vl-4b]
kind = "local_gguf"          # 本地 GGUF
path = "models/....gguf"
mmproj = "models/....gguf"   # 视觉投影，可省
ctx = 8192
ngl = 99

[models.deepseek-flash]
kind = "cloud_api"           # 云端 API
base_url = "https://api.deepseek.com/v1"
model = "deepseek-flash"
api_key_ref = "deepseek"     # Key 存 KeyStore 的这个 id 下，/key deepseek 或界面里配
ctx = 131072
price_in = 2.0               # 元 / 百万 tokens（未命中缓存）
price_out = 8.0
price_cached_in = 0.2        # 命中缓存

[modes.chat]
model = "qwen3vl-4b"         # 档位只绑模型 id，不绑类型

[app]
context_tokens = 0           # 0 = 按窗口自动
max_steps = 12
work_root = "."              # work 档文件工具的根目录
```

## 语音

三种触发方式，随时打断：

- **说「伊芙」**——常听唤醒（KWS）。听到就进录音，说完停一下自动识别。
- **按空格**——输入框空着时按空格等同「开始说」（输入框里有字就照常打空格）。
- **点「语音」按钮 / Ctrl+Space**——同上。

识别结果直接当作用户输入发出去；开头是唤醒词就剥掉。
说「切换到工作」这类话会**直接切档**，不绕模型。

- **识别**：sherpa-onnx 离线模型，默认 **SenseVoice**（中英日韩粤，自带标点），
  没下载时自动回退 paraformer-small。CPU 上约 0.19 秒 / 5.6 秒音频。
- **唤醒**：sherpa-onnx KWS，拼音建模（「伊芙」= `y ī f ú`），**必须按 100ms 分块喂**，
  整段一次性喂进去检测不到（踩过）。
- **断句**：双门限（开口高门限 / 续说低门限）+ 静音计时，可单测；
  嘈杂环境可以把 `vad_engine` 换成 `silero`（模型断句）。
- **播报**：回复会念出来（离线 VITS 中文女声）。念之前先洗文本：去 markdown、
  代码块、颜文字。整段只开一路音频流，前后垫静音，句间留 70ms 换气；
  回复是边生成边念的，不等整段说完。播报期间暂停唤醒，防止把自己的声音当成唤醒词。
  菜单里可以随时关掉；模型不在时自动退回 Windows 自带音色。
- **音色**：默认 `vits-melo-tts-zh_en`，后处理是"柔和优先"
  （4.5kHz 以上压 22%，轻压缩）——`config.toml` 里五个 `tts_fx_*` 旋钮可调，
  `tools/tts_preview.py` 能渲染多组参数对比试听。
- 全程离线，音频不出本机。`[voice] kws_enabled = false` 可关掉常听，
  `tts_enabled = false` 可关掉播报。

实测：合成语音「伊芙」「伊芙，打开记事本」都能唤醒；「今天天气不错」「打开微信」不误触。
ASR 会把「伊芙」听成「衣服」，所以唤醒词变体里收了它。

模型放在 `models/voice/`（不进版本库），首次使用按下表下载解压：

| 用途 | 模型 |
| --- | --- |
| 识别 | `sherpa-onnx-paraformer-zh-small-2024-03-09`（78 MB） |
| 唤醒 | `sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01`（33 MB） |
| 播报 | `vits-melo-tts-zh_en`（170 MB，44.1 kHz 自然播报女声） |

### 换音色

界面上**点顶栏的模型名 → 「选择音色…」**，列表里挑一个点「试听」，
满意点「使用」。选过的会记在 `.data/voice.json`，重启还是它（不动 `config.toml`）。

| 音色 | 风格 | 采样率 |
| --- | --- | --- |
| `vits-melo-tts-zh_en` | Melo，自然播报腔（默认） | 44.1 kHz |
| `vits-zh-hf-theresa` | 德丽莎，偏软 | 22.05 kHz |
| `vits-zh-hf-eula` | 优菈，清冷 | 22.05 kHz |
| `vits-zh-hf-keqing` | 刻晴，清亮 | 22.05 kHz |
| `vits-zh-hf-fanchen-C` | 播音腔，端正 | 16 kHz |
| `vits-zh-aishell3` | 174 个说话人，但只有 8 kHz，电话音 | 8 kHz |

合成之后还有一层**「AI 音色」后处理**（`tts_fx = true`）：轻压缩收窄音量起伏、
中高频提亮让咬字更清楚、一点短混响给你"贴耳"的空间感。
科幻片里那种 AI 助手质感，很大一部分来自这层处理而不是嗓门本身。
关掉就是干声：`eve_tts.py --no-fx`。

播报是**逐句流式**的：回复按句切开（每句 ≤34 字），第一句合完就出声，
后面几句在这句播完前已经合成好。整段合成再播的话，65 字要先干等 3.6 秒。

| 指标 | 改前 | 改后 |
| --- | --- | --- |
| 首声延迟（长回复） | 3.6 秒 | 约 1.5 秒 |
| 语速 | 5.9 字/秒 | 7.0 字/秒（`tts_speed = 1.12`） |

下新的（镜像可用，直连 GitHub 会超时）：

```powershell
$name = 'vits-zh-hf-theresa'
curl.exe -L -o "models\voice\$name.tar.bz2" "https://ghproxy.net/https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/$name.tar.bz2"
tar.exe -xjf "models\voice\$name.tar.bz2" -C models\voice
```

### 微调播报

参数都在 `config.toml` 的 `[voice]` 里，改完重启界面即可：

| 参数 | 作用 | 参考范围 |
| --- | --- | --- |
| `tts_speed` | 语速 | 0.5–2.0，1.0 正常 |
| `tts_volume` | 音量 | 0–1.5 |
| `tts_noise_scale` | 语气起伏 | 0.5–0.9；调大更有感情，超过 0.9 容易沙哑 |
| `tts_noise_scale_w` | 停顿随机性 | 0.6–1.0；太小像念稿，太大节奏乱 |
| `tts_speaker` | 音色编号 | 多人模型才有意义 |
| `tts_model` | 换音色 | 指向 `models/voice/` 下的 `vits-*` 目录 |

不想开界面试参数，用试听工具直接听：

```powershell
# 用配置里的参数念一句
& 'D:\python\python.exe' eve_tts.py

# 临时改参数试
& 'D:\python\python.exe' eve_tts.py "你好，我是伊芙" --speed 1.15 --noise 0.8

# 存成 wav 反复对比
& 'D:\python\python.exe' eve_tts.py "你好" --save sample.wav --no-play

# 看看有哪些音色
& 'D:\python\python.exe' eve_tts.py --list
```

念之前的文本清洗在 `eve_core/tts.py: clean_for_speech()`：
`MAX_SPEECH_CHARS = 220` 控播报长度，几个正则是去 markdown / 代码块 / 颜文字 / 列表符号。

`config.toml` 里 `[voice]` 段配置模型路径、唤醒词、静音时长。

## 工作模式

三件事：查资料、动文件、出成品。

| 能力 | 工具 |
| --- | --- |
| 联网查资料 | `web_search`（短关键词）→ `http_get` 读正文 |
| 读写整理本地文件 | `fs_read` / `fs_write` / `fs_list`（限制在 `work_root`） |
| **直接生成文档** | `office_write`：pptx / docx / xlsx，给标题 + 小节就出文件 |
| 跑命令 | `shell_exec`（底线黑名单拦截关机、格式化、删盘） |
| 把资料写进软件 | `app_launch(file=)` → `window_focus` → `key_type(save=true)` |

`office_write` 是专门加的：以前让它"做一份 PPT"，它会把 python-pptx 的代码贴给你，
你还得自己跑。现在它直接产出 5 页的 `.pptx`，内容是具体的（不是"待补充"）。

实测（deepseek-flash，work 档）：

```
-> office_write(kind=pptx, path=.tmp/ppt-test/周报.pptx, title=本周工作汇报, ...)
   OK 已生成 PPT：周报.pptx（5 页），路径：.tmp\ppt-test\周报.pptx（33 KB）
-> fs_list(path=.tmp/ppt-test)  OK 1 个文件
2 步，10813 tokens，¥0.017
```

### 最高权限

模型菜单里「最高权限」打开后，**读写文件、跑命令都不再弹确认**（含高风险工具）。
也可以写进配置常开：

```toml
[app]
auto_confirm = true
```

默认是关的。打开后它就真的能连续干活了，代价是你不再逐条把关——建议只在
自己的机器、明确知道它在做什么的时候开。


## 测试

```powershell
# 纯逻辑与集成测试，不需要模型、不联网（7 个文件 / 106 项）
& 'D:\python\python.exe' tests\test_persona.py
& 'D:\python\python.exe' tests\test_context.py      # 上下文裁剪与摘要
& 'D:\python\python.exe' tests\test_workspace.py    # fs_* / shell_exec 的边界
& 'D:\python\python.exe' tests\test_cost.py         # 计费与用量账本
& 'D:\python\python.exe' tests\test_apps.py         # 开始菜单索引与启动
& 'D:\python\python.exe' tests\test_voice.py        # 唤醒词与断句
& 'D:\python\python.exe' tests\test_agent_loop.py   # 真配置 + 桩模型的主循环
```

## 状态

- [x] v0.1 骨架：配置、provider 抽象、多轮对话、切档、日志
- [x] v0.2 desktop 档：窗口枚举/激活、键鼠注入、中文输入、动作日志
- [x] v0.3 本地档打磨：重复启动复用窗口、窗口关闭、工具级自测
- [x] v0.4 模型抽象：ModelCatalog + RuntimeManager，本地/云端统一切换，引擎懒启动
- [x] v0.4.1 修复：窗口标题异步变化导致的定位失败
- [x] v0.4.2 结构重构：按功能拆成 `eve_core` / `eve_agent` / `eve_ui`
- [x] v0.4.3 事件通道 + 人格状态机：事件总线、抗议分级、颜文字节流、单元测试
- [x] v0.5.0 主窗口：PySide6 无边框窗口、参数化莫比乌斯、事件驱动状态、agent 跑在子线程
- [x] v0.5.1 流式输出：provider 支持 SSE，逐段渲染；空态加建议入口、加大留白限制行长
- [x] v0.5.2 聊天结构：头像 + 左右气泡 + 时间戳 + 进入动效 + 顶栏状态点
- [x] v0.5.3 空态到对话的过渡：莫比乌斯飞入顶栏 + 交叉淡入
- [x] v0.5.4 agent 接线：档位切换菜单、工具确认弹窗、工具调用可视化
- [x] v0.5.5 悬浮球：玻璃球 + 光点眼 + 气泡汇报；agent 模式自动收起主窗
- [x] v0.5.6 联网工具：web_search（Bing RSS）与 http_get（正文抽取）
- [x] v0.5.7 云端接入：DeepSeek Flash/Pro + 官方 Search/Fetch 作为联网后端
- [x] v0.5.8 开始菜单：`app_launch` 走 `Get-StartApps`（本机 335 条），支持中文名；新增 `app_list`
- [x] v0.5.9 许可气泡：工具确认改为界面内卡片 / 悬浮球气泡；`app_launch(file=)` 直接开文件；`key_type(save=)`；修复长参数被 `max_tokens` 截断
- [x] v0.5.10 模型面板：顶栏点模型名切模型（带单价）、`ctx` 胶囊调预算与步数、界面内配置 API Key；`work` 档默认改 DeepSeek Flash
- [x] v0.6.0 语音输入：麦克风采集 + 静音断句 + sherpa-onnx 离线识别 + 文本级唤醒词「伊芙」（语音按钮 / Ctrl+Space）
- [x] v0.6.1 常听唤醒 + 语音切档：说「伊芙」直接进录音；空格键也能说话；三个档位显示为聊天 / 桌面 / 工作；说「切换到工作」直接切档
- [x] v0.6.2 语音播报：离线 VITS 女声（22.05 kHz），念之前洗文本；播报时暂停唤醒防自激；菜单可一键静音
- [x] v0.6.3 播报可调：音量 / 语气起伏 / 停顿随机性进配置；`eve_tts.py` 命令行试听
- [x] v0.6.4 音色可选：界面里「选择音色」试听切换，选择记到 `.data/voice.json`；内置四个女声
- [x] v0.6.5 更自然的女声 + AI 音色处理：新增 melo（44.1 kHz）并设为默认；合成后加轻压缩 / 提亮 / 短混响；输出响度归一
- [x] v0.6.6 播报提速：改成逐句流式（首声 3.6s → 1.5s），默认语速 1.12（5.9 → 7.0 字/秒）
- [x] v0.6.7 工作模式能出成品：新增 `office_write`（直接生成 pptx/docx/xlsx）；播报改成按分句切（不再说半句）+ 首尾淡入淡出；新增最高权限开关
- [ ] v0.7 打包：PyInstaller spec + 首次运行向导

## 实测数据

### desktop 档

| 场景 | 结果 |
| --- | --- |
| 「打开记事本」 | `app_launch` → 返回真实窗口标题与 hwnd，一步到位 |
| 「打开记事本并输入 hello from eve」 | 3 步（启动 → 激活 → 输入），约 2 秒 |
| 「输入：你好，我是 EVE」 | 中文走剪贴板 + Ctrl+V，正常 |
| 重复「打开记事本」 | 检测到进程已在运行，直接激活原窗口，不再开新实例 |
| 「关闭记事本」 | `window_close` 发送 WM_CLOSE，窗口消失；有未保存内容会提示 |

### 模型与运行时

| 场景 | 结果 |
| --- | --- |
| 本地模型生成 | 约 66 tok/s（Qwen3-VL-4B Q4_K_M，RTX 4060 Laptop 8G） |
| 引擎加载 | 3.4–3.8 秒 |
| `/use` 热切换 | 换端口、换 pid、各 3.4 秒，退出无残留 |
| `/scan` | 0.2 秒（不加载模型） |
| 云端 flash 单轮 | 约 1.3 秒 |
| 上下文压缩（本地 4B） | 10194 → 471 tokens，摘要 46 字，关键事实保住 |

### agent 端到端

| 场景 | 结果 |
| --- | --- |
| 「写贪吃蛇并保存」（本地 4B，desktop 档） | 4 步，自己选了记事本，最终 74 行 pygame 代码 |
| 「写贪吃蛇并保存」（DeepSeek Flash） | 8 步，**自己选了 VS Code**，118 行 tkinter 代码，41312 tokens / ¥0.045 |
