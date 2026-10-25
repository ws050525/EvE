# EVE Agent — MVP 设计草案

> 状态：讨论稿（v0.1）。还没有代码，先把边界、模型层和路由策略定下来。

## 0. 一句话目标

一个本地优先的个人 agent：**日常简单任务走本机模型，复杂任务自动升级到云端**；模型层完全可配置、可替换，任何一端换了都不改业务代码。

## 1. 现状扫描（已核实）

### 已有资产

| 项 | 内容 |
| --- | --- |
| 模型 | `D:\1\EVE\Qwen3.5-9B\Qwen\Qwen3___5-9B`，ModelScope 下载，**完整**（4 个 shard + index） |
| 权重体积 | 19.3 GB（bf16，`total_size=19306216416`） |
| 架构 | `qwen3_5`，32 层 = 8 × (3 × Gated DeltaNet 线性注意力 → 1 × Gated Attention)，多模态（27 层 vision tower），原生 262k 上下文 |
| 官方支持运行时 | Transformers / vLLM / SGLang / KTransformers（README 明示） |
| 工具调用格式 | chat_template 里是 XML 风格 `<tool_call><function=...>`，**不是** OpenAI 的 `tool_calls` JSON |

### 硬件与运行时

- CPU：i9-13980HX｜内存：63.6 GB
- GPU：RTX 4060 Laptop，**8 GB VRAM**，驱动 581.29
- 磁盘：C: 仅剩 **6.6 GB**，D: 剩 136 GB
- Python：**`D:\python\python.exe`，3.11.9**（不在 PATH 里，需显式调用）
- 关键依赖：torch 2.5.1+cu121（CUDA 可用）｜transformers **5.8.0**（含 `models/qwen3_5`，`AutoConfig` 可加载）｜accelerate 1.11｜bitsandbytes 0.47｜qwen-vl-utils 0.0.8｜openai 1.107.3｜vllm 0.10.2｜PyAutoGUI 0.9.54
- 另有：git 2.55、Ollama 客户端 0.9.0（服务未启动）、Node（未装）
- 邻近项目：`D:\1\Aico AI - 1.1`（含 `.venv-cu126`、`.venv-gemma4`，已打包）——是否复用待定

> 修正记录：v0.1 曾误判"没有 Python 环境"。原因是探测命令用了 PATH 里的 `python`，命中应用商店占位符。以 `D:\python\python.exe` 为准，环境是齐的。

### 环境阻塞（只剩一项）

**本会话的 shell 受限模式不可用**：沙箱临时授权机制在这台机器上持续失败（`windows-acl temp grant materialization failed`），受限模式下 `pwsh` 任何命令都起不来（文件读写工具正常）。绕过办法是每条命令单独申请"完全权限"，能跑通，但会反复弹确认。

→ 想让我连续干活（装包、跑测试、起服务），把这个会话切到**完全权限**最省事。这是会话设置，不是项目配置，也不影响你机器上任何东西。

## 2. 关键判断：8 GB 显存怎么放 9B

这是整个 MVP 里最"物理"的约束。9B bf16 是 19 GB，8 GB 卡装不下，必须量化或卸载到内存。

| 方案 | 现实性 | 说明 |
| --- | --- | --- |
| vLLM / SGLang | 低（现状） | 本机虽装了 vllm 0.10.2，但该版本早于 Qwen3.5，基本不认 `qwen3_5` 架构；而且 vLLM 在 Windows 上要 WSL2/Docker，C: 空间不够 |
| Ollama / LM Studio（GGUF） | 中（不确定） | 安装最轻、自带 OpenAI 兼容端点，是最省事的"提速项"。**但 `qwen3_5` 的混合线性注意力能否被 llama.cpp 支持需实测** |
| Transformers + torch | **高（已验证可加载）** | 本机版本组合齐全，`Qwen3_5ForConditionalGeneration` 原生支持。4bit(nf4) + 部分层回落内存 |
| KTransformers | 低 | 偏服务器/Linux，Windows 不划算 |

**建议**：MVP 阶段不把本地模型当性能卖点，先当"免费且私密的默认档"。架构上只认 OpenAI 兼容端点，因此 Ollama / LM Studio / vLLM 三者可以随时互换 —— 先试 Ollama，跑不通再退。

**备选**：如果 9B 确实跑不顺，本地档先换成 4B（Qwen3.5-4B / Qwen3-4B-Instruct 的 GGUF），9B 留作"本地强档"。这件事应该由配置决定，而不是写死。

**上下文现实**：262k 是宣传值。8 GB 卡上实际合理区间是 8k–32k，prompt 长了先掉的是速度。

## 3. 分层架构（MVP：单进程，无微服务）

```
┌──────────────────────────────────────────────┐
│  Interface   CLI / TUI                       │
├──────────────────────────────────────────────┤
│  AgentLoop   会话、工具循环、步数/预算控制      │
│      │                                       │
│      ├── RoutingPolicy  选哪个模型            │
│      ├── ToolRegistry   执行工具              │
│      └── Memory         历史、摘要、持久化     │
├──────────────────────────────────────────────┤
│  ModelRouter → Provider[]   （唯一抽象边界）   │
└──────────────────────────────────────────────┘
```

原则：**只有 Provider 层知道"某个厂商"的存在**。AgentLoop 只跟一个 `Provider` 接口和一份 `ModelSpec` 打交道，所以加一个新模型 = 加一段配置。

## 4. 模型层：怎么做到"接口不写死"

### 4.1 唯一契约

```python
class Provider(Protocol):
    name: str
    def chat(self, req: ChatRequest) -> ChatResponse: ...
    def stream(self, req: ChatRequest) -> Iterator[ChatDelta]: ...

# ChatRequest:  messages, tools, temperature, max_tokens, json_schema,
#               images, stop, timeout, extra
# ChatResponse: text, tool_calls, usage, model, provider, raw, finish_reason
```

上层永远不 import 任何 SDK，只发 `ChatRequest`、收 `ChatResponse`。换云端厂商 = 改配置。

### 4.2 用一个协议吃掉 90% 的差异

vLLM、Ollama、LM Studio、llama-server、DeepSeek、OpenAI、Moonshot、OpenRouter 全部提供 **OpenAI 兼容** 的 `/v1/chat/completions`。

所以 MVP 只需要三个 adapter：

- **`local_hf`（进程内）**：直接用 transformers 加载本地权重。没有 HTTP 跳转、可直接用多模态输入，但只有本进程能用。**这是当前 9B 的主路径**。
- **`openai_compatible`（HTTP）**：`base_url` + `api_key` + `model` 三件套，覆盖 vLLM / Ollama / LM Studio / DeepSeek / OpenAI / OpenRouter。将来若要本地提速（GGUF），把 `local_hf` 换成这条即可，上层无感。
- **`native_*`（预留）**：Anthropic 等协议差异较大的，等确实需要再加。

### 4.3 能力声明，而不是 if/else 分支

```yaml
providers:
  local:
    type: openai_compatible
    base_url: http://127.0.0.1:11434/v1
    api_key: "${LOCAL_API_KEY:-none}"
    model: qwen3.5-9b
    capabilities:
      tool_calling: true
      json_mode: false
      vision: true
      streaming: true
      context_window: 32768
    tags: [local, cheap, private]

  cloud:
    type: openai_compatible
    base_url: "${CLOUD_BASE_URL}"
    api_key: "${CLOUD_API_KEY}"
    model: "${CLOUD_MODEL}"
    capabilities:
      tool_calling: true
      json_mode: true
      vision: true
      context_window: 131072
    tags: [cloud, strong]

routing:
  default: local
  escalate_to: cloud
```

`capabilities` 是**路由的输入**，也是**适配的依据**：模型不支持 vision，就永远不把图片请求路由过去；不支持 json_mode，就由 adapter 用提示词兜底。

### 4.4 配置来源与密钥

优先级：命令行参数 > 环境变量 > `.env`（不入版本库）> `config.yaml` 默认值。
密钥**只**从环境变量/`.env` 读，配置文件里只写 `${VAR}` 引用。

### 4.5 工具调用归一化（最容易埋坑的地方）

- 云端：标准 `tool_calls` 数组
- 本地 Qwen3.5：XML 标签 `<tool_call><function=name><parameter=k>v</parameter></function></tool_call>`

Provider 层负责把两者都转成同一个 `ToolCall{name, args}`。**解析失败本身就是一次升级信号**（见 5.3），不要静默丢弃。

## 5. 路由策略：从确定到模糊，三层

> **v0.3 已放弃本节方案**，改为手动切档（见 §11）。保留是因为将来若要"本地建议、用户确认"的半自动形态，这里是素材。MVP 不实现自动路由。

### 5.1 硬规则（确定性，先跑，覆盖 80% 场景）

按优先级：
1. 用户显式指定（`--model cloud` / 会话内 `/model`）
2. 模态与能力：请求含图片、上下文超本地窗口、工具数超阈值、要求 json schema 而本地不支持
3. 任务标签：会话被标记为"写代码/长链推理"等重任务
4. 粘性：一旦本轮升级过，本轮后续步骤不来回跳（避免抖动）

### 5.2 轻量打分（可选，MVP 可后置）

- 启发式：输入长度、是否含代码块、是否要求多步/多文件、是否含"设计/权衡/对比/为什么"类词
- 或本地小模型输出一个 0–1 复杂度分数（成本几乎为零，但增加一次往返延迟）

### 5.3 结果驱动升级（最可靠，MVP 必做）

本地产出**校验失败**时升级，而不是靠猜：

| 信号 | 动作 |
| --- | --- |
| tool_call 解析失败 / 参数缺必填 | 先重试 1 次，再升级 |
| 要求 JSON 但解析失败或 schema 不符 | 升级 |
| 空回复或撞 max_tokens | 升级 |
| 工具连续报错 2 次仍无法自愈 | 升级 |
| 自检（self-critique）判定不达标 | 升级（可选） |

### 5.4 保护栏

- 每次会话/每轮的 token 与金额预算，超限即停
- 每步超时
- 云端失败/限流 → 回落本地并告知用户
- **强制本地模式**开关（隐私场景）：宁可失败也不外发
- 每一步都记录 `provider / model / 路由原因 / 耗时 / tokens` —— 没有这份日志，阈值永远靠猜

> 反模式：不要一上来就做"用 LLM 当路由器"。延迟翻倍、不可解释、容易抖。先用规则 + 结果驱动，攒够日志再调。

## 6. Agent 循环与工具

```
loop:
  resp = router.chat(messages, tools)
  if not resp.tool_calls: return resp.text
  for call in resp.tool_calls: messages += execute(call)
  until steps > MAX_STEPS or budget exhausted
```

MVP 工具（4–6 个足够）：

| 工具 | 说明 | 风险 |
| --- | --- | --- |
| `fs.read` | 读文件（限 workspace） | 低 |
| `fs.write` | 写文件（限 workspace） | 中 |
| `fs.list` / `fs.search` | 列目录、内容检索 | 低 |
| `shell.exec` | 执行命令 | 高 → 需确认 + 超时 + 白名单 |
| `http.get` | 取网页/API | 中 → 域名白名单 |

安全底线：路径限制在工作区、写操作可 dry-run、`shell` 默认每次确认、单会话步数上限、可选只读模式。

## 7. MVP 范围（建议 3 个迭代）

**I1 骨架（能对话、能换模型）**
配置加载 → `openai_compatible` adapter → 多轮对话 → `/model` 切换 → 结构化日志。不接工具。

**I2 变成 agent**
工具注册表 + 4 个工具 → 工具循环 → 路由硬规则 → 结果驱动升级 → 预算与确认。

**I3 本地档落地 + 体验**
选型实测（Ollama/4B/vLLM 三选一）→ 流式输出 → 会话持久化（jsonl/sqlite）→ 简单 TUI。

**明确不做**：多 agent 协作、RAG/向量库、自建 Web UI、插件市场、微调、语音、MCP 生态对接。

## 8. 风险清单

1. **9B 落不了地**：qwen3_5 的 GGUF 支持需实测；不行就要么上 WSL2+vLLM（C: 空间不够），要么本地档降级到 4B。
2. **C: 只剩 6.6 GB**：pip/npm/WSL 任何一步都可能失败。所有缓存目录（`PIP_CACHE_DIR`、`HF_HOME`、`OLLAMA_MODELS`）必须先指到 D:。
3. **工具调用格式差异**：本地 XML 与云端 JSON 归一化是主要 bug 源，要单测。
4. **隐私预期落差**：本地优先的卖点是私密，路由必须提供"绝不外发"的硬开关。
5. **可观测性缺失**：不做日志，路由就退化成玄学。
6. **速度预期**：8 GB 卡上 9B 首 token 可能要数秒，交互体验需要先让人有心理准备（或者本地档就用 4B）。

## 9. 待定（需要拍板）

1. **语言栈**：Python（openai/transformers/vllm 生态最顺）还是 TypeScript（启动快、终端 UI 好）？
2. **本地模型策略**：坚持 9B 并先验证 runtime，还是先用 4B GGUF 保底、9B 作强档？
3. **云端选哪家**：是否全部走 OpenAI 兼容端点（DeepSeek / OpenAI / Kimi / OpenRouter）？
4. **交互形态**：纯 CLI、TUI，还是本地 Web？
5. **agent 的主业**：通用助手、编码 agent、还是桌面自动化？这直接决定工具集长什么样。

---

## 10. 决策记录（v0.2，已拍板）

| 项 | 决策 |
| --- | --- |
| 技术栈 | **Python** |
| 本地模型 | **先验证 9B 能否跑**，不行再降 4B；配置可换 |
| 云端 | **先不接，占位**（provider 写好但 `enabled: false`） |
| 主业 | **通用助手 + 桌面/系统自动化** |

### 10.1 由此产生的方案修订

**① 模型层：云端占位，但抽象先立住**

本期只有一个真实 provider（本地）。但 `ModelRouter`、`ChatRequest/ChatResponse`、`capabilities` 全部照做，云端 provider 以 `enabled: false` 存在，路由升级路径遇到它时抛明确错误（"云端未配置"），而不是静默降级。这样加云端时只删一行 `enabled`。

**② 9B 验证走两条路，先保底再提速**

| 路径 | 目的 | 预期 |
| --- | --- | --- |
| A. Transformers + bitsandbytes 4bit | **保底**，官方支持，必成 | 显存约 6 GB + 部分 offload，5–15 tok/s |
| B. Ollama / llama.cpp GGUF | **提速**，自带 OpenAI 兼容端点更省事 | 需先确认 `qwen3_5` 架构被支持 |

顺序：先跑 A 拿到"能跑、多少 tok/s、多少显存"的实测数据，再决定要不要折腾 B。A 若达标，B 就只是优化项。

**③ 桌面自动化把"视觉"从可选变成硬需求**

这条决策其实解释了为什么会下 9B-VL：桌面自动化必须能看屏幕。

工具集相应扩为两组：

```
通用组   fs.read / fs.write / fs.list / fs.search / http.get
系统组   shell.exec / process.list / window.focus
桌面组   screen.capture / mouse.move|click / keyboard.type|hotkey
```

**关键约束：截图一律只送本地 VL 模型，永不上云**（除非用户当场显式同意）。这条要写进路由的硬规则，不是"看情况"。

**④ 桌面自动化的权限模型要单独设计**

`pyautogui` 这类库注入的是当前交互会话的输入，意味着 agent **不能跑在受限服务账户里**，否则看不到真实桌面。代价是风险等级整体上移：

- 高风险工具（`shell.exec`、`mouse.click`、`keyboard.type`）默认每次确认
- 桌面操作要能"暂停 / 中止"（Esc 逃生键）
- 记录每一步的截图 + 动作，可回放、可追责
- 强烈建议：桌面组默认关闭，用配置显式打开

**⑤ 安装规划（C: 只剩 6.6 GB，必须先定目录）**

| 内容 | 落到 |
| --- | --- |
| Python 解释器 | `D:\tools\python312` |
| pip 缓存 | `D:\cache\pip` |
| HF 模型缓存 / 临时权重 | `D:\cache\hf`（现有 19 GB 权重直接原地引用，别复制） |
| Ollama 模型（若用） | `D:\cache\ollama` |

**⑥ 版本风险：`transformers` 可能要装 main 分支**

`config.json` 里写的是 `transformers_version: 4.57.0.dev0`，且 `qwen3_5` 是混合线性注意力新架构。如果 pip 上的稳定版不认这个 `model_type`，就需要 `pip install git+https://github.com/huggingface/transformers`。这属于"可能踩坑"项，验证时第一件事就是确认。

### 10.2 下一步（三步，按顺序）

- **P0 环境**：本会话切到完全权限 → 装 Python 到 D: → 设好缓存目录。（现在 shell 不可用，这一步做不了）
- **P1 冒烟测试**：加载 9B（4bit）→ 生成一句话 → 记录 tok/s 与峰值显存 → 顺便试一次多模态（喂一张截图）。
- **P2 骨架**：`config.yaml` 加载 + `openai_compatible` / `local_transformers` 两个 provider + 多轮对话 + 日志。仍然不接工具。

P1 的数据出来之前，不建议动 P2 的代码 —— 因为"本地到底跑不跑得动、多快"会直接决定默认档是 9B 还是 4B。

---

## 11. v0.3 调整：本地换 GGUF，路由改手动（覆盖 §10 的部分决策）

### 11.1 分工是产品定义，不是技术妥协

| 档 | 模型 | 干什么 | 不干什么 |
| --- | --- | --- | --- |
| `chat` | 本地 GGUF | 闲聊、陪伴、情绪价值、轻量问答 | 不调工具 |
| `desktop` | 本地 GGUF + 视觉 | 开软件、搜索、点击、填表 | 不碰文件批量操作 |
| `work` | 云端 API | 整理文档、写代码、爬数据、长链推理 | — |

一句话：**本地负责"陪"，云端负责"干"**。

### 11.2 为什么手动切档是对的

- **阈值本质上不可调**。复杂度不是一个能可靠打分的标量；错判的代价是双向的——该干活的用了笨模型，闲聊被送上云（多花钱、还外发了隐私）。
- **可解释**：用户永远知道此刻花的是哪份算力、数据出不出本机。
- **可测试**：每档都是"单一模型 + 固定 prompt + 固定工具集"，行为确定，容易调、容易量延迟。
- **实现量差一个数量级**：自动路由要打分器 + 升级策略 + 回滚 + 预算；手动切档只要一张映射表。
- 唯一代价是用户要自己切 —— 一条命令 + 一次提示就补上了。

### 11.3 实现：一张表，不是一套算法

```yaml
modes:
  chat:
    provider: local
    system: prompts/chat.md        # 陪伴人格
    tools: []
  desktop:
    provider: local
    system: prompts/desktop.md     # 动作翻译
    tools: [app.launch, key.type, mouse.click, screen.capture, window.focus]
    require_confirm: true
  work:
    provider: cloud
    system: prompts/work.md
    tools: [fs.read, fs.write, fs.list, shell.exec, http.get]

default_mode: chat
```

- `/mode work` 切档，`--mode` 启动参数，配置可自定义档位
- 不做自动路由，但保留**温和提示**：chat 档收到"帮我改这个文件"这类明确超出能力的请求时，回一句"这需要 work 档，要切吗？`/mode work`"——这是提示，不是决策
- 会话内记住上次选择

### 11.4 本地档已实测可用（数据来自本机）

| 项 | 实测 |
| --- | --- |
| 模型 | Gemma-4-E4B-it QAT q4_0 + mmproj（已在 `D:\1\Aico AI - 1.1\models\gemma4`） |
| 运行时 | llama.cpp **b9553**，`llama-server.exe` 自带 OpenAI 兼容端点 |
| 生成速度 | **36.9 tok/s**（47 tokens / 1.27 s） |
| 加载 | 3.75 s，`-ngl 99` 全层上卡 |
| 结论 | 聊天体验足够，比 4bit 9B 那条路好太多，且几乎不挤占 8 GB 显存 |

> 这个组合意味着：本地档**零下载、零安装**就能起，只要把 `llama-server` 的路径和模型路径写进配置。

### 11.5 那个 19 GB 的 Qwen3.5-9B 怎么处置

- 进程内 4bit 加载在本机连试三次都失败：bnb 0.47 的 `Params4bit` 与 transformers 5.8 的分层加载不兼容，补丁之后又卡在 meta tensor 的 `quant_state`。**这条路不再折腾。**
- GGUF 化需要 llama.cpp 支持 `qwen3_5` 的混合线性注意力，目前未验证；而且 9B 在 8 GB 卡上即使 Q4 也只有个位数 tok/s，和"情绪价值"的目标不符。
- 建议：**原样留着不删**。将来若要"离线强模型"，更现实的是 WSL2 + vLLM，或者等官方发 GGUF。

### 11.6 桌面自动化：确定性优先，视觉兜底

三层，从上往下用：

1. **系统接口（首选）**：`Start-Process` 开程序、`Win+R` 输入、剪贴板、UIA 控件树（`pywinauto`）
2. **键鼠注入（次选）**：pyautogui（已装），快捷键组合、固定坐标点击
3. **视觉定位（兜底）**：截图 → 本地 VL 模型 → 目标框 → 点击

本地模型在 `desktop` 档的真实职责很轻：**把一句话翻译成一串动作 JSON**。

> 用户：「打开记事本，写个购物清单」
> → `[{"act":"launch","target":"notepad"},{"act":"type","text":"购物清单\n- 牛奶"}]`

安全设计（这一档风险最高）：
- 动作白名单；`require_confirm: true` 时逐条确认
- 全局 Esc 中止
- 每步记录（动作 + 截图），可回放追溯
- **`desktop` 档不授予 `shell.exec`** —— 否则"点一下"等于"执行任意命令"

### 11.7 修订后的路线

| 阶段 | 内容 |
| --- | --- |
| P0 | 起 `llama-server` + Gemma GGUF，验证 `/v1/models` 和一次 chat（约 10 分钟） |
| P1 | 骨架：三档配置 + `openai_compatible` provider + 多轮对话 + `/mode` + 结构化日志 |
| P2 | `desktop` 档：5 个桌面工具 + 确认机制 + 动作日志 |
| P3 | `work` 档：接云端 + `fs`/`shell`/`http` 工具 |

**明确不做**：自动路由、9B 本地部署、多 agent、RAG、Web UI。

---

### 11.8 本地模型定版（v0.4）

> 勘误：§11.4 的 Gemma 只是"能不能跑"的探路验证，不是最终选型；§11.5 里"9B 原样留着"的决定已按用户要求改为**删除**，回收 18.4 GB。

选型：**Qwen3-VL-4B-Instruct**，ModelScope 仓库 `Qwen/Qwen3-VL-4B-Instruct-GGUF`（apache-2.0）

| 需要的文件 | 体积 |
| --- | --- |
| `Qwen3VL-4B-Instruct-Q4_K_M.gguf` | 2.33 GB |
| `mmproj-Qwen3VL-4B-Instruct-F16.gguf` | 0.80 GB |

选它的理由：

- **agent 能力对口**：Qwen3-VL 官方主打的正是工具调用与 GUI grounding，"打开软件、点按钮、填表"这类正好是它的强项，比同尺寸纯文本模型更贴 `desktop` 档。
- **速度**：4B 在 Q4_K_M 下，8 GB 卡上预计 40–60 tok/s（同体量 Gemma-4-E4B 实测 36.9 tok/s 可当下限参考），首 token 亚秒级。
- **显存留得余地**：模型 3.1 GB，剩 4 GB+ 给 KV cache，8k–16k 上下文不吃力。
- **一个模型覆盖两档**：自带 vision（mmproj），截图兜底不用再挂第二个模型。

为什么不是别的：

- **8B 版**：Q4 约 5 GB + mmproj 1.3 GB，KV 只剩 1.5 GB，速度掉到 ~25 tok/s，与"响应速度优先"冲突。
- **Qwen3.5-4B**：ModelScope 上 `Qwen/Qwen3.5-4B-GGUF` 是 404（官方未发 GGUF），社区转制版对 `qwen3_5` 架构的 llama.cpp 兼容性也未验证，不适合做地基。

运行时：Aico 项目不动，但**从它复制**一份 llama.cpp 二进制到 `D:\1\EVE\tools\llama.cpp` 并不修改 Aico；也可以另下官方 release。

已知风险：Qwen3-VL 走 llama.cpp 的 mtmd 多模态路径，b9553 大概率支持，但**下载后第一件事是加载验证**，别默认它能跑。
