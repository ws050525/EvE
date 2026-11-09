# EVE 记忆报告（截至 v0.6.8）

> 给下一个对话用：读完这份就能接手，不用翻聊天记录。
> 这份是**对话总结**，不是交接清单；`docs/HANDOFF.md` 是上一个作者留下的旧笔记，不要在里面追加。

---

## 1. 这是什么

一个本地优先的个人 agent：**本地模型陪聊 + 操作电脑，云端模型干重活**。

- 仓库：https://github.com/ws050525/EvE （分支 `main`）
- 当前版本：`0.6.8`（`eve_core/version.py`），最新标签 `v0.6.8`
- 工作目录：`D:\1\EVE`

## 2. 环境（容易踩的坑）

| 项 | 值 |
| --- | --- |
| Python | **`D:\python\python.exe`**（3.11.9，不在 PATH，必须写全路径） |
| GUI | PySide6 6.9.2 |
| 本地模型 | Qwen3-VL-4B Q4_K_M，llama.cpp 引擎（`D:/1/Aico AI - 1.1/llama.cpp/llama-server.exe`，别的项目，不能改） |
| 云端 | DeepSeek `deepseek-flash`（默认）/ `deepseek-v4-pro`，key 在 `.data/keys.json`（DPAPI） |
| 语音 | sherpa-onnx 2.0.1 + onnxruntime；模型在 `models/voice/`（约 1.1GB，已 gitignore） |
| GPU | RTX 4060 Laptop 8GB |

**已知坏掉的东西**（换方案时别再踩）：
- `openai-whisper` 导入即炸：numba 要求 numpy ≤1.26，实际 2.4.6。
- `webrtcvad` 缺 `pkg_resources`。
- `edge-tts`（微软晓晓）403：免费端点要令牌。
- **ChatTTS 装不进来**：依赖 numba，同上。要试只能独立 venv + 子进程。
- GitHub 直连经常超时；下模型用镜像 `https://ghproxy.net/https://github.com/...`。

## 3. 架构一张图

```
eve_core/   核心（不含 UI、不含 agent 循环）
  config.py     config.toml → AppConfig / ModelConfig / ModeConfig / VoiceSettings
  provider.py   OpenAI 兼容 chat() / stream()，流式带 usage
  runtime.py    RuntimeManager：model_id → Provider；LocalEngine 拉起 llama-server
  pricing.py    单价 → 花费（含缓存折扣）
  usage.py      会话账本（按模型汇总，切档不清零）
  voice.py      语音输入：Recorder / EnergyVad / SherpaRecognizer / KwsDetector / 唤醒词匹配
  tts.py        语音播报：PiperTts（VITS）+ clean_for_speech + 音色效果 + 逐句切分
  secrets.py    DPAPI KeyStore
  events.py     事件总线
eve_agent/  agent 层
  loop.py       Agent：人格注入、流式、工具调用、上下文预算、用量记账
  context.py    token 估算、按轮裁剪、滚动摘要、兜底截断
  persona.py    人格状态机（抗议分级、颜文字节流）
  tools/        registry / builtin(app_launch,app_list,screen_capture) / desktop / workspace /
                shell / web / office
eve_ui/     界面
  app.py 入口与开发期开关；main_window 主窗；chat_view 气泡；mobius 莫比乌斯；orb 悬浮球
  bridge.py agent 跑在 QThread；voice.py 语音线程；tts.py 播报线程；theme.py 配色
eve_cli.py  eve_gui.py  eve_tts.py（播调试听工具）
tests/      8 个文件 / 136 项，全部不需要模型
```

## 4. 三个档位

| id | 显示名 | 模型 | 工具 | 行为 |
| --- | --- | --- | --- | --- |
| `chat` | 聊天 | 本地 GGUF | 无 | 纯聊天，不收成悬浮球 |
| `desktop` | 桌面 | 本地 GGUF | 11 个桌面/联网工具 | 收成悬浮球，干完回界面 |
| `work` | 工作 | DeepSeek Flash | 12 个（文件/文档/shell/桌面混合） | 收成悬浮球，干完回界面 |

内部 id 保持英文，显示名走 `[modes.*] label`。切档会重置上下文（prompt + 工具集变了）。

## 5. 这几十轮做了什么（按版本）

- **v0.5.7 之前**：骨架、桌面自动化、模型抽象、UI、悬浮球、联网工具。
- **v0.6.0**：上下文预算（按轮裁剪 + 滚动摘要 + 兜底截断）。
- **v0.6.0**：work 档工具落地（fs_read/write/list、shell_exec，锁在 `work_root`）。
- **v0.6.1~0.6.7 的语音线**：
  - 麦克风采集 + 能量断句 + sherpa-onnx 离线识别（热态 0.05–0.1 s/句）
  - 常听唤醒（KWS，拼音 token `y ī f ú`）：**必须按 100ms 分块喂**，整段喂检测不到
  - 语音切档（「切换到工作」直接切，不走模型）
  - 播报：melo 44.1kHz + 「AI 音色」处理（轻压缩/提亮/短混响）+ 逐句流式（首声 3.6s → 1.5s）+ 语速 1.12
  - 播报不再「说半句」：改按分句切（只在标点下刀）+ 首尾 8ms 淡入淡出
- **0.5.8~0.5.9**：`app_launch` 走开始菜单（Get-StartApps，本机 335 条），支持中文名、`file=` 直接开文件、`key_type(save=)`。
- **0.5.10**：界面内配 API Key、模型菜单带单价、`ctx` 胶囊调预算与步数。
- **0.6.7**：`office_write`（直接生成 pptx/docx/xlsx）、最高权限开关、工作档补齐桌面工具。

## 6. 关键决策（别推翻）

1. **手动切档，不做自动路由。** 档位 = prompt + 工具集 + 默认模型。
2. **模型是一等公民。** `[models.*]` 声明，`[modes.*]` 只绑 id；换模型改配置。
3. **上下文按轮裁。** tool 消息必须和它的 `assistant.tool_calls` 同轮进出，否则请求体非法。
4. **边界写在代码里。** `fs_*`/`shell_exec` 锁 `work_root`；shell 另有底线黑名单（关机/格式化/删盘）。
5. **确认走「子线程阻塞 + 主线程弹窗」**，结果必须直接调 `resolve_confirm()`，不能走信号（会死锁）。
6. **中文输入一律剪贴板 + Ctrl+V**，`pyautogui.write` 会被输入法吞键。
7. **价格与用量**：单价在 `[models.*]`，账本跟着 Agent，切档不清零。
8. **`config.toml` 程序不重写**（有注释和顺序），界面里的选择存 `.data/voice.json` 覆盖。

## 7. 已知问题 / 下一步

**坏过的、已验证修好的**：长参数被 max_tokens 截断（桌面档 512→2048）、tool 确认死锁、悬浮球收太快、
干完停在球上不回来、唤醒没应答、播报说半句、切到工作档没有球（刚修：改成"有工具的档位都收球"）。

**还没验的**：
- 跨工具工作流「查资料 → 打开软件把资料写进去」：工具已配齐（`office_write`/`app_launch(file=)`/`window_focus`/`key_type`），没跑过端到端。
- 语音端到端需要真人：喊「伊芙」听应答 → 说长指令 → 看播报断不断。

**待办**：
1. `models/voice/` 1.1GB，`fanchen-C`（3.8 字/秒）和 `aishell3`（8kHz）建议删。
2. 唤醒叫不醒：`[voice] kws_threshold` 调低（0.22 → 0.15），或往 `WAKE_VARIANTS` 加误识别变体。
3. 打包（PyInstaller one-folder）还没开始，模型要外置。
4. `docs/design-v2-ui-voice.md` 的 §5.2 与 §8.1 对唤醒方案的说法矛盾，以 §8.1（两段式）为准。

## 8. 语音阈值在哪改（用户明确问过）

**优先改 `config.toml` 的 `[voice]`，不用动代码：**

| 键 | 作用 | 默认 |
| --- | --- | --- |
| `kws_threshold` | 唤醒灵敏度，调低更容易叫醒（0.10 很敏感，0.35 很迟钝） | 0.22 |
| `vad_energy_floor` | 低于这个 RMS 不算说话；环境吵就调高 | 0.010 |
| `vad_noise_gain` | 门限 = max(floor, 环境噪音 × 倍数) | 2.5 |
| `vad_min_speech_ms` | 连续说多久才算真在说话（挡咳嗽/敲键盘） | 240 |
| `silence_ms` | 说完静音多久算一句结束 | 900 |

**要动代码时**（`eve_core/voice.py`）：

| 位置 | 内容 |
| --- | --- |
| `VoiceSettings`（约 142 行） | 上面这些默认值 |
| `EnergyVad`（约 209 行） | 断句状态机；`update()` 决定何时收尾，`threshold()` 算门限 |
| `EnergyVad.CALIBRATE_BLOCKS` | 开头用几个块估环境噪音（8 块 ≈ 256ms） |
| `WAKE_VARIANTS`（约 32 行） | 唤醒词误识别变体，**ASR 会把「伊芙」听成「衣服」**，所以收了它 |
| `_WAKE_TOKENS`（约 184 行） | 唤醒词 → 拼音 token，换唤醒词要在这里加一行 |
| `KwsDetector.__init__` | `threshold` / `score` 参数（`eve_ui/voice.py` 里从配置传进来） |

调法建议：先只动 `kws_threshold`（0.22 → 0.15），不行再加变体；`vad_*` 只影响"说完什么时候停"，不影响能不能叫醒。

## 9. 开发约定（血泪）

- **PowerShell 里不要用 `python -c "..."` 传中文/引号**，会被吞引号。写临时 `.py` 再跑。
- 写文件用 UTF-8 无 BOM（Python/TOML 都会因 BOM 炸）。
- TOML 表名 `[engines.llama.cpp]` 会被解析成嵌套表，用 `[engines.llamacpp]`。
- git 提交信息用多个 `-m`，每段短句，避开引号和 `+`。
- Qt：半透明窗口隐藏子控件后要手动 `update()`；`QScrollArea` viewport 要用对象名选择器写 QSS。
- 工具开发：输入文字走剪贴板；`app_launch` 窗口标题异步变化（记事本会变），优先用 hwnd。
- 验证脚本别用「定时器采样」那种写法，容易错序；用 `app.processEvents()` 循环推进时间线。

## 10. 怎么跑

```powershell
& 'D:\python\python.exe' eve_gui.py          # 界面
& 'D:\python\python.exe' eve_cli.py          # 命令行
& 'D:\python\python.exe' eve_tts.py --list   # 看有哪些音色
& 'D:\python\python.exe' tests\test_voice.py # 单个测试
```

测试共 8 个文件 / 136 项：persona / context / workspace / cost / apps / office / voice / agent_loop。

## 11. 给下一个对话的开场建议

> 读 `docs/MEMORY.md`，接着做：① 跨工具工作流端到端（查资料 → 写文件 → 打开软件写进去）；
> ② 语音真人验收；③ 清理 models/voice 里没用的音色；④ 打包。
