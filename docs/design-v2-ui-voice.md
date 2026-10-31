# EVE v2 设计：模型可换 / UI / 语音 / 打包

> 讨论稿 v2.0。针对「UI 界面 + 本地云端模型可替换 + 语音唤醒 + 打包分发」四个新需求。

## 0. 三个结论

1. **模型层：provider 没写死，engine 写了。** 要补的是"模型源 → 运行实例"这一层管理器，不是重写 provider。
2. **UI：推荐 PySide6。** 决定性理由不是好看，而是"关掉窗口后语音还得继续听"——那要求托盘常驻，浏览器页做不到。
3. **语音：MVP 走"VAD + 本地 ASR + 唤醒词前缀匹配"**，不上专门的唤醒词模型。先跑通，再按 CPU 占用决定要不要换 KWS。

---

## 1. 现状盘点：哪一层可换，哪一层写死

| 层 | 文件 | 现状 | 判定 |
| --- | --- | --- | --- |
| 对话协议 | `eve_core/provider.py` | OpenAI 兼容，统一 `chat()` | ✅ 可换 |
| 档位 → provider | `config.toml [modes.*]` | 配置映射 | ✅ 可换 |
| 端点地址 | `config.toml:13` | `http://127.0.0.1:18080/v1` 固定端口 | ⚠️ 半写死 |
| **引擎启动** | `eve/serve.py:27 build_command()`（v0.4 已删除，逻辑并入 `eve_core/runtime.py`） | 硬编码 llama.cpp 的参数：`-ngl` / `-c` / `--mmproj` | ❌ **写死** |
| **引擎路径** | `config.toml:54 [engine].bin` | 指向 `D:/1/Aico AI - 1.1/...` 另一个项目 | ❌ **写死且跨项目** |
| **模型文件** | `config.toml:55 [engine].model` | 单一路径，换模型要改文件 | ❌ **单选** |

也就是说：**换云端厂商不用改代码，换本地模型要改配置文件，换推理引擎要改 Python 代码。** 第三件事就是这次要修的。

---

## 2. 目标架构：把"模型"提升为一等公民

```
UI / CLI
   │  选 model_id
   ▼
ModelCatalog     有哪些模型可用（扫描 GGUF + 用户填的云端）
   │
   ▼
RuntimeManager   把 model 变成可用端点（本地拉起引擎 / 云端直连）
   │
   ▼
Provider         OpenAI 兼容的 chat()——这层完全不动
```

**关键改动：档位不再绑定 `local` / `cloud`，而是绑定 `model_id`。**

这样"work 档用本地 8B""chat 档用云端"都成立——切换是数据变化，不是代码变化。这才是"随时更替"的真正含义。

```toml
[models.qwen3vl]
label = "Qwen3-VL 4B Q4_K_M"
kind = "local_gguf"          # local_gguf | cloud_api
engine = "llama.cpp"
path = "models/.../Qwen3VL-4B-Instruct-Q4_K_M.gguf"
mmproj = "models/.../mmproj-Qwen3VL-4B-Instruct-F16.gguf"
ctx = 8192
ngl = 99

[models.deepseek]
label = "DeepSeek Chat"
kind = "cloud_api"
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
api_key_ref = "dpapi:deepseek"     # 密钥走 Windows DPAPI，不进明文配置

[modes.chat]
model = "qwen3vl"
system_prompt = "prompts/chat.md"
tools = []
```

### 2.1 RuntimeManager 的职责

| 职责 | 说明 |
| --- | --- |
| `ensure(model_id)` | 返回一个可用 Provider；本地模型按需拉起引擎 |
| 单实例 | 8 GB 卡放不下两个 4B，同时只留一个本地引擎 |
| 空闲卸载 | 默认 10 分钟无请求即停引擎，回收显存 |
| 动态端口 | 从空闲端口分配，不再写死 18080 |
| 故障处理 | 加载失败/端口占用/进程崩溃 → 结构化错误，UI 直接显示 |
| 热切换 | 换模型 = 停旧引擎 + 起新引擎，UI 只换一个引用 |

### 2.2 Engine 接口（这才是需要抽象的东西）

```python
class Engine(Protocol):
    name: str
    def ensure_ready(self) -> str:   # 返回 base_url
    def health(self) -> dict:
    def stop(self) -> None:
```

三个实现：
- `LlamaCppEngine` —— 拉起 `llama-server`，参数由配置生成（不再是 Python 里写死的 `-ngl/-c`）
- `ExternalEngine` —— 已经在跑的 Ollama / LM Studio / vLLM，只探活，不管生命周期
- `(预留) VllmEngine`

### 2.3 用户选模型文件

- 扫描可配置的若干目录，收集 `*.gguf`
- **主模型 vs mmproj**：文件名含 `mmproj` 归为视觉投影，其余为主模型
- 自动配对：同目录同前缀的 mmproj 自动关联；没有就按纯文本模式跑
- 列表展示：文件名、体积、量化等级（从 `Q4_K_M` 这类后缀解析）

---

## 3. UI 框架选型

| 维度 | PySide6 | nicegui | pywebview + HTML | Flet |
| --- | --- | --- | --- | --- |
| 托盘常驻 | ✅ 原生 | ❌ 需另搭 pystray | ✅ | 部分 |
| 全局热键 | ✅ | ❌ | ⚠️ | ❌ |
| 系统通知/模态确认 | ✅ | 弱 | ⚠️ | 一般 |
| 聊天 UI 开发速度 | 中 | **快** | 慢（要写前端） | 快 |
| 打包体积 | +150–250 MB | +30 MB | +10 MB | +40 MB |
| 打包成熟度 | 高 | 中 | 高 | 中 |

**推荐 PySide6**，三个理由：

1. 语音要求"窗口关了还在听"——必须托盘常驻，nicegui 是浏览器页，关了标签就断。
2. 全局热键（喊不醒时按键说话）在 Qt 里是原生能力，Web UI 基本做不到。
3. 工具执行确认要模态弹窗 + 系统通知，原生控件最省事。

代价是体积和开发速度。如果你更看重"快速做出好看的界面"，nicegui 明显更顺手，但要接受"必须开着浏览器"。折中方案是 **pywebview + HTML**：只加 10 MB，用系统 Edge WebView2 渲染，托盘/热键靠 Python 侧，界面用前端写。

---

## 4. UI 功能清单（MVP）

**主界面三块**：对话区 / 模型面板 / 状态栏。

| 区域 | 内容 |
| --- | --- |
| 对话区 | Markdown 渲染、流式输出、停止生成、档位切换、工具调用过程可折叠 |
| 模型面板·本地 | 扫描列表、选 GGUF、自动配对 mmproj、启动/停止、加载进度、显存与 tok/s |
| 模型面板·云端 | 厂商预设 + 自定义 base_url、API Key（加密存储）、"测试连接"按钮 |
| 全局 | 当前激活模型高亮，点"应用"即热切换 |
| 弹窗 | desktop 档高风险动作确认 |
| 托盘 | 状态指示、静音、退出 |

**MVP 不做**：主题换肤、多会话管理、插件系统、图片输入（模型支持，但优先级低于语音）。

---

## 5. 语音

### 5.1 链路

```
麦克风 → VAD 断句 → ASR 识别 → 唤醒词判定 → 指令 → Agent →（可选 TTS）
```

### 5.2 三种唤醒方案

| 方案 | 唤醒延迟 | CPU | 中文 | 打包复杂度 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 常听 ASR + 前缀匹配 | 中（说完才判） | 中 | 好 | 低 | **MVP 选它** |
| sherpa-onnx KWS | 低（<300ms） | 低 | 支持 | 中 | 后续优化 |
| Porcupine | 最低 | 最低 | 要自定义/付费 | 低 | 商用再说 |

**MVP 具体做法**：whisper.cpp（CPU int8）+ VAD 切句。用户说「EVE，打开记事本」，整句识别出来，检查是否以唤醒词开头，是则执行、否则丢弃。

好处：一套 ASR 同时解决唤醒和指令，不用训练、不用调阈值。
代价：没唤醒时也在跑 ASR（CPU 约 10–20%）。若嫌费电，再上 KWS。

### 5.3 细节

- **音源不冲突**：4B LLM 占 GPU，ASR 走 CPU int8，各不打扰
- **防自激**：TTS 播报时暂停收音，播完再开
- **打断**：说「停」或按 Esc
- **隐私**：音频不出本机，唤醒词表本地
- **延迟预算**：VAD 0.3–0.5s + ASR 0.6–1.2s + 首 token 0.3–0.6s ≈ 1.5–2.3s，可接受

### 5.4 TTS（可选，影响"情绪价值"）

| 方案 | 质量 | 离线 | 体积 |
| --- | --- | --- | --- |
| piper | 中上 | ✅ | +50 MB 模型 |
| edge-tts | 好 | ❌ 需联网 | 0 |
| Windows SAPI | 差（机械） | ✅ | 0 |

情绪价值场景值得做，但排在语音输入之后。

---

## 6. 打包（auto-py-to-exe / PyInstaller）

### 6.1 关键决定

- **one-folder，不是 one-file**。one-file 每次启动都要把 150 MB 解压到临时目录，冷启动 5–10 秒；one-folder 快得多，也便于替换 llama.cpp。
- auto-py-to-exe 只是 PyInstaller 的图形壳，**务必把配置存成 `.spec`**，之后用 `pyinstaller eve.spec` 复现，别每次点界面。

### 6.2 目录布局（打包后）

```
EVE\
  eve.exe
  _internal\           # PyInstaller 运行时
  tools\llama.cpp\     # llama-server.exe + dll（必须一起带）
  prompts\
  models\              # 不打包，用户自选
  config.toml          # 首次运行生成到 %APPDATA%\EVE\
```

### 6.3 体积预算

| 组件 | 体积 |
| --- | --- |
| Python + 标准库 | ~25 MB |
| PySide6 | 150–250 MB |
| llama.cpp 二进制（含 Vulkan/CUDA DLL） | 60–120 MB |
| ASR（whisper.cpp + small 模型） | 二进制 5 MB + 模型 150 MB（外置） |
| VAD | webrtcvad 1 MB / silero onnx 2 MB |

**绝对不要引入 torch**——现在整个项目是纯标准库，这是打包最大的优势，别为了语音把它丢掉。

### 6.4 已知坑

- 中文路径：`--add-data` 时用 `;` 分隔，路径含空格要引号
- 模型/引擎路径：打包后 `sys._MEIPASS` 与用户目录要区分（是否有 `sys.frozen`）
- 杀软误报：PyInstaller 常见，签名可解（可选）
- 音频设备：`sounddevice` 的 PortAudio DLL 会被 PyInstaller 漏掉，需手动 `--add-binary`

---

## 7. 阶段计划

| 版本 | 内容 | 为什么这个顺序 |
| --- | --- | --- |
| **v0.4** | 模型抽象：`models` + `RuntimeManager` + 热切换（CLI 验证） | UI 要绑稳定 API，先把它做对 |
| **v0.5** | UI 骨架：主窗 + 模型面板 + 聊天 + 托盘 | 用户能看见、能换模型 |
| **v0.6** | 语音：VAD + ASR + 唤醒词 | 依赖 UI 的状态指示 |
| **v0.7** | 打包：spec + 首次运行向导 | 最后做，避免反复返工 |

---

## 8. 决策记录

| 项 | 决定 |
| --- | --- |
| UI 框架 | **PySide6** |
| 唤醒方式 | **两段式**：先唤醒，再单独说指令 |
| TTS | **做，用 piper**（离线） |

### 8.1 两段式唤醒带来的设计

```
待机(idle)
  └─ KWS 持续监听唤醒词 ──命中──> 提示音 + UI 变"在听"(listening)
                                        │
                                   VAD 断句（5s 内无语音则回 idle）
                                        │
                                   ASR 识别指令 ──> 思考(thinking) ──> 播报(speaking) ──> idle
```

- **待机阶段只跑 KWS，不跑完整 ASR**——这是两段式相对一句说完的最大收益：功耗低一个数量级。
- 唤醒后必须给明确反馈（提示音 + 界面状态），否则用户不知道"它听到了没"。
- 5 秒内没说话自动回待机，避免麦克风长开。
- 播报时暂停收音，防止把自己的声音当成唤醒词（自激）。
- 保留**按键说话**作为兜底：唤醒词不灵时，按热键直接进 listening。

两段式的代价：需要引入 KWS 模型。方案：
- **首选** sherpa-onnx KWS（离线、支持中文关键词）
- **备选** 常听 whisper-tiny（~40 MB int8）只判首词——比 KWS 费 CPU，但零调参、不用做关键词分词

> 风险：KWS 的关键词定义依赖分词/音素，中文唤醒词（如"小伊"）需要实测命中率，别默认它能用。

### 8.2 piper 带来的体积账

piper 和 sherpa-onnx **共用 onnxruntime**，所以只多付一次 ~50 MB 的运行时成本：

| 组件 | 体积 |
| --- | --- |
| onnxruntime（KWS + TTS 共用） | ~50 MB |
| piper 语音模型（zh_CN，中等质量） | ~60 MB |
| sherpa-onnx KWS 模型 | ~10 MB |

加上 PySide6（150–250 MB）和 llama.cpp（60–120 MB），安装包总量约 **350–450 MB**。这是可接受的桌面应用体量，但要提前心里有数。

### 8.3 仍需确认

1. 唤醒词用 **"EVE"**（英文，识别引擎的英文关键词更成熟）还是 **"小伊"**（中文，更顺口但命中率要实测）？
2. 模型目录默认放**项目内 `models/`** 还是 **`%APPDATA%\EVE\models`**？
3. 打包 one-folder（推荐）还是 one-file？
