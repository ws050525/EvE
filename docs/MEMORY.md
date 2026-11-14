# EVE 记忆报告（截至 v0.8.0）

> 给下一个对话用：读完这份就能接手，不用翻聊天记录。
> 这份是**对话总结**，不是交接清单；上一版留下的 `docs/HANDOFF.md` 已删（内容并进这份）。

---

## 1. 这是什么

一个本地优先的个人 agent：**本地模型陪聊 + 操作电脑，云端模型干重活**。

- 仓库：https://github.com/ws050525/EvE （分支 `main`）
- 当前版本：`0.8.0`（`eve_core/version.py`），最新标签 `v0.8.0`
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
  checkpoint.py 断点：把"停在哪"存下来，下次接着做
  persona.py    人格状态机（抗议分级、颜文字节流）
  tools/        registry / planning(todo_write) / project(多文件) / browser(Edge CDP) /
                builtin(app_launch,app_list,screen_capture)
                / desktop / workspace / shell / web / office
eve_ui/     界面
  app.py 入口与开发期开关；main_window 主窗；chat_view 气泡；mobius 莫比乌斯；orb 悬浮球
  bridge.py agent 跑在 QThread；voice.py 语音线程；tts.py 播报线程；theme.py 配色
eve_cli.py  eve_gui.py  eve_tts.py（播调试听工具）  tools/（模型下载、播报预览）
tests/      12 个文件 / 191 项，全部不需要模型
```

## 4. 三个档位

| id | 显示名 | 模型 | 工具 | 行为 |
| --- | --- | --- | --- | --- |
| `chat` | 聊天 | 本地 GGUF | 无 | 纯聊天，不收成悬浮球，步数 12 |
| `desktop` | 桌面 | DeepSeek Flash（云端） | 12 个桌面/联网/计划工具 | 收成悬浮球，步数 20 |
| `work` | 工作 | DeepSeek Flash | 24 个（含计划、多文件、浏览器） | 收成悬浮球，步数 32 |

内部 id 保持英文，显示名走 `[modes.*] label`。切档会重置上下文（prompt + 工具集变了）。
**步数上限按档位走**（`[modes.*] max_steps`），`[app] max_steps` 只是兜底。

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
- **0.6.9**：语音两处大修（播报改成"一路流 + 流式切句"，识别换 SenseVoice + 双门限 VAD），
  见第 12 节；顺手清掉 537MB 没用的音色和 `.tmp/`。
- **0.6.10**：声音不再发硬——后处理从"提亮优先"改成"柔和优先"，语速 1.12→1.05，
  句间留 70ms 换气，见 12.6 节。
- **0.8.0**：浏览器真交互（Edge CDP，零下载）、多任务并行（最多 3 个）、断点列表，见第 15 节。
- **0.7.1**：断点续跑 + 多文件资料（`checkpoint.py` / `project_init` / `project_status`），见第 14 节。
- **0.7.0**：agent 能力节点——计划工具 `todo_write`、长文分块写（office_write append）、
  联网查证方法论、完全权限一键开关、桌面档换云端、随时停止，见第 13 节。

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
- 语音端到端需要真人：喊「伊芙」听应答 → 说长指令 → 看播报断不断（代码侧已验证，见第 12 节）。

**待办**：
1. ~~`models/voice/` 里没用的音色~~（已删，见 12.4）。
2. 唤醒叫不醒：`[voice] kws_threshold` 调低（0.22 → 0.15），或往 `WAKE_VARIANTS` 加误识别变体。
3. 打包（PyInstaller one-folder）还没开始，模型要外置。
4. `docs/design-v2-ui-voice.md` 的 §5.2 与 §8.1 对唤醒方案的说法矛盾，以 §8.1（两段式）为准。

## 8. 语音阈值在哪改（用户明确问过）

**优先改 `config.toml` 的 `[voice]`，不用动代码：**

| 键 | 作用 | 默认 |
| --- | --- | --- |
| `kws_threshold` | 唤醒灵敏度，调低更容易叫醒（0.10 很敏感，0.35 很迟钝） | 0.22 |
| `vad_energy_floor` | 低于这个 RMS 不算说话；环境吵就调高 | 0.010 |
| `vad_noise_gain` | 开口门限 = max(floor, 环境噪音 × 倍数) | 2.5 |
| `vad_hold_ratio` | **新**：开口后门限降到高门限的这个比例，防"换气声把句子切断" | 0.7 |
| `vad_min_speech_ms` | 连续说多久才算真在说话（挡咳嗽/敲键盘） | 240 |
| `silence_ms` | 说完静音多久算一句结束 | 1000 |
| `vad_engine` | **新**：`energy`（默认）/ `silero`（模型断句，嘈杂环境更稳） | energy |
| `silero_vad_model` | **新**：silero 模型路径，`tools\fetch_voice_models.py --vad` 下载 | models/voice/silero_vad.onnx |
| `asr_model` / `asr_fallbacks` | **新**：识别模型（默认 SenseVoice），第一个存在的目录生效 | 见 config.toml |
| `tts_speed` | 语速，太快显得急、发硬 | 1.05 |
| `tts_fx_warmth` | **新**：低频厚度 | 0.16 |
| `tts_fx_clarity` | **新**：中高频存在感，大了发燥 | 0.06 |
| `tts_fx_deharsh` | **新**：4.5kHz 以上柔化，大了发闷 | 0.22 |
| `tts_fx_space` | **新**：空间感 | 0.06 |
| `tts_fx_compression` | **新**：压缩，大了压扁发硬 | 0.30 |

**要动代码时**（`eve_core/voice.py`）：

| 位置 | 内容 |
| --- | --- |
| `VoiceSettings`（约 150 行） | 上面这些默认值 |
| `EnergyVad`（约 238 行） | 断句状态机：双门限（开口/续说）、`update()` 决定何时收尾 |
| `EnergyVad.CALIBRATE_BLOCKS` | 开头用几个块估环境噪音（8 块 ≈ 256ms，取中位数） |
| `EnergyVad.NOISE_CEILING` | 噪声估计上限 0.05，防止"一开口就被当成环境噪音" |
| `WAKE_VARIANTS`（约 32 行） | 唤醒词误识别变体，**ASR 会把「伊芙」听成「衣服」**，所以收了它 |
| `_WAKE_TOKENS`（约 196 行） | 唤醒词 → 拼音 token，换唤醒词要在这里加一行 |
| `KwsDetector.__init__` | `threshold` / `score` 参数（`eve_ui/voice.py` 里从配置传进来） |
| `detect_asr_family()` | 按目录特征认识别模型家族（sense_voice / paraformer / whisper / transducer） |
| `_make_silero()` | silero VAD 构造，失败一律回退能量断句 |

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

测试共 12 个文件 / 191 项：persona / context / workspace / cost / apps / office / voice /
agent_loop / planning / checkpoint / project（语音 50 项；计划 11 项；断点 7 项；多文件 8 项）。

下载语音模型（SenseVoice + silero VAD）：

```powershell
& 'D:\python\python.exe' tools\fetch_voice_models.py --list   # 先看有没有
& 'D:\python\python.exe' tools\fetch_voice_models.py          # 下载（≈156MB 压缩包）
```

## 11. 给下一个对话的开场建议

> 读 `docs/MEMORY.md`。语音（第 12 节）和 agent 能力（第 13 节）两个节点都已完成，
> 测试 191/191，代码在本地和 GitHub 上都是最新。
> 剩下的主要是**真人验收**：喊「伊芙」听播报、开完全权限跑一个真实复杂任务、
> 看长任务中途按停止会不会干净退出。
> 然后是打包（PyInstaller one-folder，模型外置）。

## 12. 语音两处大修（v0.6.9，已验证并提交）

用户反馈两个 bug：① 阈值调完识别还是不理想，问有没有新模型；② 播报开头和结尾的句子念不全，
播报跟不上文字生成。

### 12.1 播报（`eve_core/tts.py` + `eve_ui/tts.py` + `eve_ui/main_window.py`）

根因三个，都是**"每句一次 `sd.play`"**这套写法带来的：

1. **`clean_for_speech` 把回复砍到 220 字**（`MAX_SPEECH_CHARS`）→ 长回复后半段根本没送进合成。
   现在改 3000 字，只防病态输入；正常回复一律念完。
2. **每句开关一次音频设备**：设备启动那几十毫秒会吞数据（开头没了），
   `sd.wait()` 返回就换下一句（尾音被掐）。块越短，头尾缺失越明显。
   → 新增 `PlaybackSession`：整段只开一路 `OutputStream`（`latency="high"`），
   开头垫 120ms 静音、结尾补 180ms 静音并且等缓冲区真的播完才关流。
   合成和播放分两条线程，下一句在上一句还没播完时就合成好。
3. **等整段生成完才开始念** → 声音永远落后于文字。
   → 新增 `SentenceStreamer`：流式文本攒成"完整且够长"的句子就吐出来（碎句不吐，
   一直往后攒），`main_window._feed_speech()` 在 `reply_chunk` 里喂进去，
   `_end_speech()` 在回复结束时收尾。

线程模型：`TtsWorker` 持有当前 `PlaybackSession`，用 `_session_mark` 只让"当前这次"的
结束信号生效（否则取消旧播报时会把唤醒提前恢复，自己念自己）。

### 12.2 识别（`eve_core/voice.py` + `config.toml` + `tools/fetch_voice_models.py`）

- **换模型**：默认 `SenseVoice`（`sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17`，
  ≈228MB，int8，自带标点+数字规整）。比原来的 `paraformer-zh-small-2024-03` 中文准得多。
  下载：`& 'D:\python\python.exe' tools\fetch_voice_models.py`（走 ghproxy 镜像，失败退回直连）。
- **没下载也能用**：`asr_fallbacks` 里第一个存在的目录顶上，`SherpaRecognizer.note` 会说明。
- **自动认家族**：`detect_asr_family()` 按目录名+文件特征，换模型只改配置。
- **断句改双门限**：开口看高门限，开口后看低门限（`vad_hold_ratio`），
  说话中途音量掉一点不会被当成"说完了"（这是句子被拦腰切断的主因）。
- **噪声估计改中位数 + 上限 0.05**：以前用最大值，开头一声响动就把门限顶死，
  后面整段都判不出说话（表现为"没听到说话"）。
- **录音前后各补 150ms 静音**再交给识别，头尾不容易丢字。
- **可选 silero VAD**：`vad_engine = "silero"` + 下载 `silero_vad.onnx`。默认还是 energy。

### 12.3 验证结果（2026-11-12）

- `tests\test_voice.py` **48/48 通过**；8 个测试文件合计 **147/147**。
- 真机播报：`tts.speak()` 走新链路，`played=True`，流只开一路、正常 stop/close；
  逐句推送（模拟流式）时首次出声 ≈1.9s（含首句合成 0.9s）、整段播完正常。
- `TtsBridge` 层（Qt 队列信号）实测：`started` 1 次、`finished(True)` 1 次，没有重复/丢失。
- 新加的**预热**（`TtsBridge.preload()`，开窗 1.5s 后加载 VITS + 合一句"预热"）：
  第一次播报的首声从 ~3.3s 降到 ~0.7s。
- ASR 回退链路实测：SenseVoice 未就位时自动用 paraformer，三份示例音频识别正常（≈0.9s/句）。
- **SenseVoice 已下载并实测**（2026-11-12，229MB，加载 0.85s）：
  - 中文：和 paraformer 内容基本一致，但**带标点和阿拉伯数字**（「早上9点至下午5点。」）。
  - 英/日/粤：paraformer 完全是乱码，SenseVoice 全对（它就是多语言模型）。
  - 速度：5.6s 音频 0.19s（paraformer 0.06s），RTF 0.03，够用。
  - `config.toml` 指向它，`SherpaRecognizer.note` 为空（没走回退）。
- `sherpa-onnx 2.0.1` 的 `from_sense_voice` 签名确认有 `language` / `use_itn`；
  silero VAD 能正常构造。

### 12.4 清理（同时做了）

- 删掉 `.tmp/`（151 个临时脚本和截图，11MB）、所有 `__pycache__`。
- 删掉用不上的音色：`vits-zh-aishell3`（328MB，8kHz）、`vits-zh-hf-fanchen-C`（131MB，3.8 字/秒）、
  `vits-piper-zh_CN-huayan-medium`（77MB，没 lexicon，代码根本加载不了）。合计省 537MB。
- `.gitignore` 里 `/tools/` 改成 `/tools/*` + `!/tools/*.py`，否则 `tools\fetch_voice_models.py` 被忽略。
- `docs/HANDOFF.md` 保持删除状态（旧笔记，内容已被本文件覆盖）。
- 留下的：melo（当前音色）、eula（代码默认值）、theresa、keqing（备选）、
  SenseVoice（ASR 主用）、paraformer（ASR 回退）、KWS、silero。

### 12.5 下一步

1. **真人验收**：喊「伊芙」→ 说长指令 → 听播报开头结尾、看是否跟得上文字。
2. 真人说话再试，听不准就把 `vad_engine` 换成 `silero`（模型已下载）。
3. `docs/design-v2-ui-voice.md` 的 §5.2/§8.1 矛盾仍未处理。
4. 改动已提交（`v0.6.9`）并打了标签，已 push 到 GitHub。

### 12.6 声音发硬（v0.6.10）

用户反馈"声音太硬、语气不自然"。根因不在模型，在**后处理链**：上一版是"清楚优先"，
给 3.2kHz 以上加 30% 存在感、压缩 0.6、再把峰值顶到 0.85 —— 听着发燥、发刺、没呼吸。

改法（`eve_core/tts.py` 的 `apply_voice_fx`）：

| 环节 | 旧 | 新 |
| --- | --- | --- |
| 低频厚度 | 220Hz ×0.12 | 200Hz ×0.16 |
| 中高频存在感 | 3.2kHz 以上 +30% | 2.5–4.5kHz 只 +6% |
| 4.5kHz 以上 | 提亮 | **压 22%**（新增 `deharsh`） |
| 压缩 | 0.6，门限 0.12，比例 2.8:1 | 0.30，门限 0.18，比例 1.6:1 |
| 收尾 | 硬削波 | tanh 软限幅 |
| 语速 | 1.12 | 1.05 |
| 句间 | 紧贴（只有合成空档） | 固定 70ms 换气（`PlaybackSession.SENTENCE_GAP_MS`） |

客观指标（同一句话，`tools/tts_preview.py` 可复现）：谱心 **3008Hz → 2415Hz**，
4.5kHz 以上能量 **29.0% → 20.4%**，音量不变（RMS -15.6dB / -15.1dB），峰均比不降（不压扁）。

五个旋钮都在 `config.toml [voice]`：`tts_fx_warmth / clarity / deharsh / space / compression`，
`eve_tts.py` 也有同名命令行参数（`--deharsh --compression ...`）。想更柔就把 `deharsh` 往上、
`clarity`/`compression` 往下；`tools/tts_preview.py` 会把"当前 / 更柔 / 干声 / 各音色"渲染成
wav 放到 `.tmp/preview/` 供试听。

## 13. agent 能力节点（v0.7.0）

目标：让它能接"市面上的 agent 都能干"的活——写文章、做 PPT、不确定的数据自己查。

### 13.1 让长任务跑得起来（几处硬限制）

| 问题 | 原来 | 现在 |
| --- | --- | --- |
| 输出上限 | work 2048 tokens，长文必被截断 | work 8192 / desktop 4096 |
| 步数 | 全局 12 步 | 按档位：chat 12 / desktop 20 / work 32 |
| 写长文 | 一次 `office_write` 塞几千字 → 参数非法 JSON | `append=true` 分块续写（docx/pptx/xlsx 都支持） |
| PPT 排版 | 只有一级要点、固定 18pt | `bullets` 支持 `{"text","level"}` 二级缩进、`notes` 讲稿、按条数自适应字号 |
| shell 里 python | 机器上 Python 不在 PATH，`python` 直接 exit 9009 | `shell_exec` 把解释器目录**挪到** PATH 最前（不是"没有就加"，系统里已有一个排在 Store 占位符后面） |

### 13.2 计划工具 `todo_write`（eve_agent/tools/planning.py）

多步任务的骨架：列 3–8 步，每步 `pending / in_progress / completed`，
**同一时刻只允许一个 in_progress**（多出来的自动降回 pending 并告知）。
每次调用都回完整清单——即使上下文被摘要压过，也能立刻对回目标。
`work` / `desktop` 两档都挂了它；纯聊天档没有。

### 13.3 提示词（prompts/work.md 重写）

从"能用工具"升级成"会做项目"：判断任务类型 → 列计划 → 查证（至少两个独立来源，
对不上就明说，末尾列来源）→ 分块写正文 → 生成文件 → 自检（`fs_list` 确认存在）→ 交付。
明确写了"简单一问一答别用 todo_write"，避免为了用工具而用工具。

### 13.4 完全权限

顶栏新增 `权限：询问 / 权限：完全` 胶囊（`theme.PERM_CHIP_ON_QSS` 打开时变警示色）。
点一下切换，状态存 `.data/prefs.json`（`save_preference`），下次启动还在；
`config.toml` 的 `auto_confirm` 只是默认值。命令行 `--yes` 等价。

### 13.5 随时停止

- `Agent.cancel()` 置标志位，主循环每步开头检查，工具循环里也检查 → 当前这步结束后退出。
- `AgentBridge.cancel()` **直接调用 worker**（不排队）：worker 正阻塞在模型请求里，排队的信号没用。
- UI：忙的时候发送按钮变 **停止**，`Esc` 同效。
- 局限：已经发出的 HTTP 请求掐不掉，所以是"这一步跑完就停"，不是立刻。

### 13.6 云端模型

`desktop` 档从本地 4B 换成 `deepseek-flash`：4B 做工具调用参数错误率太高（实测反复试错）。
`chat` 仍用本地——陪聊不需要那么强，也不花钱。

### 13.7 端到端实测（2026-11-14，真实 API）

1. **写文章**：`写 800 字《本地模型和云端模型怎么分工》，3 个小标题，生成 docx`
   → 3 步计划 + 2 次 `office_write`（append）+ 自检，11 步、977 个汉字、¥0.005。
2. **查资料做 PPT**：`8 页《2026 年 AI 编程助手市场格局》，至少两个独立来源，最后一页列来源`
   → JetBrains + Stack Overflow 交叉验证排名，两个口径的市场规模并列而不是硬凑一个，
   标注了媒体转述未确认的数字和付费墙限制，8 页 + 讲稿，¥0.0096。

两个都远超"简单操作"：会自己判断数据可信度、会承认读不到原文。

### 13.8 还没做的（下一个节点的候选）

- 任务中断后的"继续"：现在停了就停了，没有断点续跑。
- 多文件项目级写作（一个目录里多篇互相引用）。
- 浏览器真交互（点按/登录）仍然只有桌面工具，没有无头浏览器。
- 长任务的过程产物（中间稿）没有在界面上单独展示。
- 打包（PyInstaller one-folder，模型外置）一直没动。

## 14. 断点续跑与多文件资料（v0.7.1）

### 14.1 断点续跑（`eve_agent/checkpoint.py`）

**问题**：任务动辄二三十步，用户按停止或撞上步数上限，前面查的资料、写好的文件全白费——
模型不知道上次干到哪，只能重头再来。

**做法**：三种"没干完"的出口都存一份快照到 `.data/tasks/latest.json`：
`cancelled`（用户停止）/ `steps_limit`（撞上限）/ `error`（异常）。快照里有：
原始请求、计划清单、动过的文件、截断后的对话历史（单条 ≤4000 字符，最多 40 条）。

**续跑**：`Agent.resume()` 把历史、计划捞回来，生成一段 brief（上次做到第几步、
还差哪些、上次说到哪），然后照常 `send(brief)`。提示词里明确写了
"以（续跑）开头 → 从没完成的那一步继续，不要重写已完成的部分"。

**入口**：
- 界面：输入框旁边出现「继续」按钮（`Ctrl+R`），只在真有断点时显示。
- 命令行：`eve_cli.py --resume`；`--steps N` 可以临时压低步数上限（测断点用）。

**清除**：任务正常做完自动删断点，所以「继续」不会给出一个已经完成的任务。

### 14.2 多文件资料（`eve_agent/tools/project.py`）

**问题**：让模型写"主报告 + 附录 + 摘要 PPT"，它能写，但常见三件事：
引用写成 `附录.md` 而实际叫 `02-附录.md`；引用了不存在的文件；目录里多出没人认领的产物。

**做法**：
- `project_init(name, title, files=[{path, purpose}])` → 生成 `manifest.json` + `index.md`。
- `project_status(name)` → 检查缺文件 / 字数过少（<50 字）/ markdown 相对链接断链 /
  清单外的孤儿文件，最后给"齐了"或"还没齐"的结论。
- 提示词要求：多文件先建清单 → 逐篇写（互引用用相对链接）→ 写完必须跑一次自检 → 交付 index.md。

单文件任务不该用这套工具，`office_write` 一次写完就行（提示词里也写了）。

### 14.3 实测（2026-11-14，真实 API）

- **断点**：`--steps 4` 跑一个 6 页 PPT 任务 → 第 4 步停下，断点里 4 步计划、13 条消息、
  reason=steps_limit；`--resume` 接着跑完 → 6 页 PPT、3 个来源、断点自动清除。¥0.011 + ¥0.008。
- **多文件**：`docs/ai-adoption` 三件套 → 主报告 1667 字、附录 7 张表、
  1 页 PPT 带讲稿，三份互引（主报告 ↔ 附录 / 摘要），`project_status` 报"齐了"。
  ¥0.0074。

### 14.4 还没做的

- 断点只有"最近一份"，多任务并行时会互相覆盖。
- 续跑不会自动挑回原来的档位/模型到界面显示（agent 内部会切回，UI 顶栏要手动刷新）。
- 多文件只做 markdown 链接检查，Office 之间只核对存在性。
- 打包（PyInstaller）按用户要求继续往后放。

## 15. 浏览器真交互 / 多任务并行 / 断点列表（v0.8.0）

### 15.1 浏览器（`eve_agent/tools/browser.py`）

**为什么不用 Playwright**：要额外拉 100MB+ 的 Chromium，而这台机器已经装了 Edge。
直接 `msedge.exe --remote-debugging-port` 起浏览器，用 WebSocket 发 CDP 命令，
**零下载**，不需要驱动。`http_get` 仍然负责读静态正文，浏览器只负责"必须动手"的场景。

实现要点：
- 点击走 JS 注入（选择器优先，找不到再按可见文字匹配，都不中就回一份"页面上能点的东西"清单）；
- 填写用原生 `value` setter + `input/change` 事件（React 那套监听才认）；
- 回车提交走 CDP `Input.dispatchKeyEvent`（纯 JS 派的 KeyboardEvent 不触发默认提交）；
- `_eval` 对"导航中上下文被销毁"这类错误自动重试——不重试的话每次跳转后的第一次读取都会炸。

8 个工具：`browser_open / read / click / type / wait / screenshot / back / close`。
`show=true` 用有头模式，方便人工登录和过验证码。

### 15.2 多任务并行（`eve_ui/tasks.py` + `main_window`）

每个任务 = 独立的 `AgentBridge`（自己的 QThread + Agent）+ 独立的 `ChatView`（放在
`QStackedWidget` 里，切换只换页，滚动位置保留）+ 自己的计划/花费/忙闲状态。

- 顶栏 **＋** 或 `Ctrl+T` 开新任务；超过 1 个就显示任务条（`●` 跑着 / `✓` 完成 / `✕` 出错）。
- 同时最多 `MAX_RUNNING = 3` 个在跑。
- 事件处理器都按 `tid` 分流：聊天内容写进自己的 ChatView，**状态栏/悬浮球/播报只对当前任务生效**。
- 工具确认带 `tid`：后台任务要授权时弹卡片，答复送回那个任务的 worker（不是当前这个）。

### 15.3 断点列表

`tasks/<id>.json` 一任务一份，`latest.json` 只当"最近一份"的指针。
`checkpoints.list_all()` 给界面列出来：右键「继续」按钮弹出断点列表（摘要 + 进度 + 时间）。
任务成功收尾只删自己那份，**不动别人的**（这条有专门用例守着）。

id 用 `task-<时间>-<4位随机>`：同一秒起几个任务不会撞车（第一版就撞了，测试抓到的）。

### 15.4 实测（2026-11-14）

- **浏览器**：agent 自己打开 example.com → 点 Learn more → 读 IANA 页面 → 退回 → 截图 → 关闭，
  5 步、¥0.0031。截图确认页面对。
- **并行**：两个任务同时跑（work 档 × 2），各自拿到自己的回答，状态都变 done。
- **断点列表**：存 2 份断点 → 列表显示 2 条 → 删一条剩 1 条 → 清空后「继续」隐藏。
- 测试 12 个文件 / 191 项全绿（新增 browser 10 项、checkpoint 多槽位 2 项）。

### 15.5 还没做的

- 多任务之间不能共享上下文（要共享得手动把结论复制过去）。
- 任务条只显示在内存里，重启后只有断点列表能捞回来。
- 浏览器只支持单个标签页（多标签要再包一层）。
- 打包（PyInstaller）按用户要求继续往后放。
