# EVE 记忆报告（截至 v0.14.1）

> 给下一个对话用：读完这份就能接手，不用翻聊天记录。
> 这份是**对话总结**，不是交接清单；上一版留下的 `docs/HANDOFF.md` 已删（内容并进这份）。

---

## 1. 这是什么

一个本地优先的个人 agent：**本地模型陪聊 + 操作电脑，云端模型干重活**。

- 仓库：https://github.com/ws050525/EvE （分支 `main`）
- 当前版本：`0.14.1`（`eve_core/version.py`），最新标签 `v0.14.1`
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
tests/      16 个文件 / 241 项，全部不需要模型
```

## 4. 三个档位

| id | 显示名 | 模型 | 工具 | 行为 |
| --- | --- | --- | --- | --- |
| `chat` | 聊天 | DeepSeek Flash（云端） | 无 | 纯聊天，不收成悬浮球，步数 12 |
| `desktop` | 桌面 | DeepSeek Flash（云端，支持图） | 12 个桌面/联网/计划工具 | 收成悬浮球，步数 60 |
| `work` | 工作 | DeepSeek Flash（云端，支持图） | 29 个（计划/多文件/浏览器/视觉/共享） | 收成悬浮球，步数 100 |

内部 id 保持英文，显示名走 `[modes.*] label`。切档会重置上下文（prompt + 工具集变了）。
**步数上限按档位走**（`[modes.*] max_steps`），`[app] max_steps` 只是兜底。

## 5. 这几十轮做了什么（按版本，最新在上）

- **0.14.1**：宏不许抢复合任务，参数要校验；截图窗口兜底。见 23.6。
- **0.14.0**：语义动作记忆（做过一遍的操作直接回放），见第 23 节。
- **0.13.0**：`ui_find` 三层定位（OCR → 色块 → 九宫格），截图前临时置顶避开遮挡，见第 21 节。
- **0.12.1**：截图锁定"正在操作的窗口"，修掉窗口漂移，见 20.8。
- **0.12.0**：视觉改走云端（DeepSeek Flash 本来就收图）、九宫格粗定位、步数 60/100、鬼打墙熔断，见第 20 节。
- **0.11.0**：UI-TARS 真正跑通（`--image-max-tokens` 是关键）、修截图缩放导致坐标偏小的老 bug，见第 19 节。
- **0.10.3**：UI-TARS 改用魔搭源下载，加 `tools/model_progress.py` 看进度。
- **0.10.2**：接 UI-TARS-1.5-7B，加模型完整性检查（半截文件不会被误用）。
- **0.10.1**：视觉支持 GUI 专用模型（UI-TARS / Holo）的动作格式与 0–1000 归一化坐标，见第 18 节。
- **0.10.0**：聊天也走云端，本地只留视觉，见第 17 节。
- **0.9.1**：视觉默认只截活动窗口 + 限 image tokens，单次 15s → 6–9s，见 16.6。
- **0.9.0**：跨任务共享笔记、浏览器多标签页、本地视觉当"眼睛"，见第 16 节。
- **0.8.0**：浏览器真交互（Edge CDP，零下载）、多任务并行（最多 3 个）、断点列表，见第 15 节。
- **0.7.1**：断点续跑 + 多文件资料（`checkpoint.py` / `project_init` / `project_status`），见第 14 节。
- **0.7.0**：agent 能力节点——计划工具 `todo_write`、长文分块写、联网查证方法论、完全权限开关、桌面档换云端、随时停止，见第 13 节。
- **0.6.10**：声音不再发硬（后处理改"柔和优先"，语速 1.12→1.05，句间 70ms 换气），见 12.6。
- **0.6.9**：语音两处大修（播报改一路音频流 + 流式切句；识别换 SenseVoice + 双门限 VAD），见第 12 节。
- **0.6.7**：`office_write`（直接生成 pptx/docx/xlsx）、最高权限开关、工作档补齐桌面工具。
- **0.6.1~0.6.6 的语音线**：
  - 麦克风采集 + 能量断句 + sherpa-onnx 离线识别（热态 0.05–0.1 s/句）
  - 常听唤醒（KWS，拼音 token `y ī f ú`）：必须按 100ms 分块喂，整段喂检测不到
  - 语音切档（「切换到工作」直接切，不走模型）
  - 播报：melo 44.1kHz + AI 音色处理 + 逐句流式（首声 3.6s → 1.5s）+ 语速 1.12
- **0.6.0**：上下文预算（按轮裁剪 + 滚动摘要 + 兜底截断）；work 档工具落地（fs_read/write/list、shell_exec，锁在 `work_root`）。
- **0.5.8~0.5.10**：`app_launch` 走开始菜单（支持中文名、`file=` 直接开文件、`key_type(save=)`）；界面内配 API Key、模型菜单带单价、`ctx` 胶囊调预算与步数。
- **v0.5.7 之前**：骨架、桌面自动化、模型抽象、UI、悬浮球、联网工具。


## 23. 语义动作记忆（v0.14.0）

### 23.1 为什么不是"记坐标"

用户最初的想法：第一次自动化时后台记下坐标，下次直接复用。他自己也看出问题了——
**那就是硬编码，命令稍微变一下就不成立**。

改成记「意图」：

```json
{"do": "launch", "app": "网易云音乐"}
{"do": "click", "target": "播放全部"}
{"do": "type",  "target": "搜索框", "text": "{1}"}
```

- `click` 走 `ui_find(target)` 重新定位（OCR 10–15px），窗口挪了、分辨率变了都还能用；
  只有"界面上再也找不到这几个字"才失效。
- 没有文字的控件（网易云搜索框）记 `click_point 0.24,0.03`——窗口内相对位置，
  比绝对坐标稳，窗口移动/缩放都还对，只有布局改了才失效。
- `{1}` 是参数槽：`patterns` 用正则从任务里抽（`《(.+?)》` 抽歌名）。
  **抽不到就不回放**，免得照着旧参数乱做（第一版就犯过：让它搜《七里香》，
  它照着上次的记忆搜了"晴天"）。
- 任何一步失败（报错 / 用户拒绝确认）就整体放弃，落到正常 agent 流程。

### 23.2 实测对比

同一个任务：「用网易云搜歌，歌名《X》」

| | 走模型 | 走记忆 |
| --- | --- | --- |
| 步数 | 20–30 步（有一次撞 30 步上限） | **7 步**（全是工具调用） |
| 模型调用 | 每步一次 | **0 次**（只有最后验证一次） |
| token | 6500–11300 | ~600（就那句验证） |
| 耗时 | 2–4 分钟 | **9.6–13.9 秒** |
| 花费 | ¥0.0026–0.0059 | ~¥0.0002 |

### 23.3 踩过的坑（都在代码注释里记了）

1. **参数被固化**：回放时直接在 `step.text` 上做替换，`record_hit()` 又把内存里的
   steps 存回磁盘 → 下次换歌名还是输旧的。改成用局部变量替换，不动原对象。
2. **拿错窗口**：同一个进程有主窗 + 一堆小浮层（SystemHint 540×738），
   按"第一个匹配"找会拿到浮层，后面截图和点击全落在它身上。改成**按面积取最大**。
3. **前台切换失败就不认窗口**：`_already_running` 原来只在 `_force_foreground` 成功时返回，
   改为"尽力置前，但窗口照用"。
4. **弹窗挡路**：网易云会弹"每日推荐"卡片，挡住搜索框，输入全进不去。
   宏里加一步 `esc` 先关弹窗。

### 23.4 仍然不稳的地方

回放假设"应用处于预期的状态"。实测同一套宏：
- 干净状态下 7 步全成、搜索结果正确；
- 有弹窗/焦点被抢时，步骤"都成功"但结果是错的（输入没进搜索框）。

所以 `_try_macro` 里带了一步 `screen_read` 验证，但**验证只报告不阻断**——
判断"验证结果是否算失败"还需要语义判断，这是下一步要做的（比如连续两次验证不符就作废这条记忆）。

### 23.6 记忆抢任务（v0.14.1，用户逼出来的）

用户要求"再跑一遍，你自己看图确认"。跑的时候暴露了一个**危险 bug**：

复合任务「先在网易云搜歌，然后打开抖音，最后写文件」被那条「网易云搜歌」记忆**抢走了**——
它把关键词当成命令，抽出参数时把后面的整段指令当歌名，直接输进搜索框。
结果搜索框里出现的是 `; 最后把歌名写进 D:\EVE\.tmp\plan-test.txt，先列出计划再动...`。

三个修复：
1. **复合任务不抢**：文本里出现「然后 / 接着 / 最后 / 首先 / 第一 / 并且 / 同时 / 三件事」等，
   `match()` 直接返回 None，交给模型拆。
2. **参数要像参数**：抽出来超过 24 字、或含 `/ \ : ； , 。` 的，一律丢弃。
3. **窗口兜底**：启动瞬间抓到的是闪屏 → `click_point` 拿不到窗口。改成
   目标缺失时按程序名重新找（取面积最大），`_wait_new_window` 也改成取最大新窗口。

验证：同一条复合任务现在会老老实实交给模型，拆出 4 步
（确认歌名 → 网易云搜索播放 → 抖音搜索 → 写文件）。

**教训**：记忆命中率高不等于对——它抢到了不该抢的任务，比没命中更糟。
以后加任何"捷径"都要先问一句：什么情况下它**不该**走这条路。

### 23.5 关于上下文压缩

用户问"你能不能自动压缩对话上下文，能的话就不常开新对话"。
EVE 自己的 agent 有上下文压缩（`_enforce_context`：按轮裁剪 + 滚动摘要 + 兜底截断）。
这个 DSH 会话本身也会自动压缩（本次会话中途就插入过 checkpoint 摘要）。
但压缩是有损的，所以**关键决策仍然写进这份 MEMORY**，不依赖对话记忆。


## 25. 清理（v0.15.1，2026-11-22）

视觉走了云端之后，本地那 9GB 权重就是纯占地方。清掉之后：

| 项 | 之前 | 之后 |
| --- | --- | --- |
| `models/` | 9,951 MB | **924 MB**（只剩语音模型） |
| `.tmp/` | 443 MB | **0.2 MB** |
| `__pycache__` | 到处都是 | 清空 |

删掉的：`UI-TARS-1.5-7B-GGUF`（5.8GB）、`Qwen3-VL-4B-Instruct-GGUF`（3.2GB）、
`.tmp` 下 219 张旧截图、三个浏览器临时 profile、下载器留下的 `._____temp` 和 `.lock`。

配置同步做了简化：`[vision] model = ""`（留空=不用本地视觉），
`[models.*]` 只留一条 `qwen3vl-4b` 作为"要用本地时下这个"的说明，其余条目删除。
README 里那段"下载 Qwen3-VL-8B"的长说明也换成了三行可选步骤。

**没删的**：`models/voice/`（924MB，TTS/ASR 在用）、`docs/assets/`（README 配图）、
`logs/`（0.8MB，排查问题用）、`.data/`（偏好和记忆，是你自己的数据）。


## 26. 音色换成 CosyVoice2（v0.16.0）

### 26.1 为什么换

原来的 sherpa-onnx VITS（melo/theresa/eula/keqing）"能用"，但语气平、断句机械。
CosyVoice2-0.5B 是自回归 TTS，长句自然得多，而且后面能零样本复刻音色。

### 26.2 两个坑（都踩了）

1. **PyPI 的 `cosyvoice` 包不是推理库**。`pip install cosyvoice` 装完只有
   `api / server / version` 三个模块，`cosyvoice.cli` 根本不存在。
   真正的代码在官方仓库 `FunAudioLLM/CosyVoice`，clone 到 `.vendor/CosyVoice`，
   import 前加 `sys.path`。
2. **多线程下载器写坏文件**。第一版是"每个线程读文件末尾位置再 append"，
   四个线程会读到同一个位置、写重复数据。改成：预分配文件 + 每个线程领块号、
   按偏移量 `seek` 写 + `.parts.json` 记进度 + 下完校验 sha256。

### 26.3 实现

- `eve_core/tts_cosy.py`：`CosyVoiceTts`，接口和 `PiperTts` 一致
  （`available` / `synthesize`），所以播放、流式、打断这些上层逻辑一行没改。
- `eve_ui/tts.py::_pick_backend`：`tts_backend = "cosyvoice"` 时先试它，
  不可用就**自动退回 VITS**，不会哑掉。
- `tools/tts_preview.py --backend cosyvoice --play`：不用开 GUI 就能试听。

### 26.4 参数对齐（官方 API 和直觉不一样）

```python
CosyVoice2(model_dir, load_jit=False, load_trt=False, fp16=False)   # 没有 use_flow_cache
model.inference_sft(text, "中文女", stream=False, speed=1.0)        # speed 是模型自带的
```

一开始我按记忆写了 `use_flow_cache=False`，还打算自己重采样做变速——都是错的，
读一眼源码才发现 `inference_sft` 本来就收 `speed`。

### 26.5 实测结果：能出声，但偏慢（2026-11-22）

| 项 | VITS（现用） | CosyVoice2 |
| --- | --- | --- |
| 语速 | ~5 字/秒 | **1.3–2 字/秒**（"你好，我是伊芙。" 要 6.4 秒） |
| 合成耗时 | 几百毫秒 | 6–53 秒（RTF 0.98–1.4，fp32 在 4060 上） |
| 显存 | 0 | ~3GB |
| 首次加载 | 2 秒 | 25–30 秒 |

6.4 秒的"你好，我是伊芙"不是静音垫出来的（有声区间 0.1–18.2s 是连续的），
是模型真的念得慢。换参考音频（用 VITS 生成一段再喂回去）时长一模一样，
说明语速跟参考无关，是模型 + 缺文本前端的问题（日志里 `no frontend is avaliable`）。

**所以默认改回 vits**，CosyVoice 保留为可选后端。要继续调的话有三条路：
1. 装 `ttsfrd` / `WeTextProcessing` 文本前端（Windows 上 pynini 编译很麻烦）；
2. `fp16=True` 把 RTF 压到 0.7 左右；
3. 换 CosyVoice-300M（v1，自带 中文女 等预置音色，模型也小）。

**结论**：链路是通的（能合成、能播、能退回），但还没到"换掉 VITS"的程度。

### 26.6 换 300M（v1）之后

v2 念得慢是模型特性，不是配置问题，所以又下了 `iic/CosyVoice-300M-SFT`（2.2GB，
跳过 JIT/TRT 的 zip 才没下满 5.5GB）。它自带 `spk2info.pt`，有「中文女 / 中文男」等预置音色。

同一句「今天天气不错，我们一起出去走走吧。」（17 字）：

| | 音频时长 | 合成耗时 | 语速 |
| --- | --- | --- | --- |
| VITS（现用） | 2.8s | 2.7s | 6.2 字/秒 |
| CosyVoice-300M 中文女 | 2.0s | 3.5s | **8.5 字/秒** |
| CosyVoice-300M 中文男 | 2.3s | 3.2s | 7.4 字/秒 |

语速终于正常了（v2 是 1.3 字/秒）。但**合成比实时慢 1.7 倍**（RTF 1.76），
`fp16=True` 也没改善（1.93）——瓶颈在自回归逐步生成 + CPU 上的 ONNX tokenizer，不是精度。

所以：能听、能用，但每句要等 3 秒左右才出声，和现在的 VITS 差不多。
默认仍是 VITS，改用 `tts_backend = "cosyvoice"` 即可切换。


### 26.7 复刻「龙安亲」音色（v0.17.0）

用户在百炼的音色列表里挑中 `longanqin_v3`（亲和活泼女，20~25 岁）。
那是**云端音色 ID**，本地模型没有；但不用接 API——走零样本复刻：

1. 他放了一段 19.4 秒的 mp3 到 `.tmp/vision/龙安亲.mp3`
2. `tools/prepare_ref.py` 转成 22050 单声道、砍静音
3. **本地 ASR（SenseVoice）转写**样本 → 得到参考文本，不用手工敲
4. 截 8 秒（`models/voices/longanqin-8s.wav`）+ 对应文本，配进 `cosy_ref_wav/text`
5. `cosy_mode = "zero_shot"`，`tts_backend = "cosyvoice"`

参考长度对速度影响很大（同一句「早上好，我是伊芙…」）：

| 参考长度 | 合成耗时 | 音频 | 语速 |
| --- | --- | --- | --- |
| 5s | 6.6s | 3.2s | 7.5 字/秒 |
| 8s | 9.9s | 3.6s | 6.7 字/秒 |
| 11s | 13.5s | 4.0s | 6.1 字/秒 |

选 8s：音色还原够用，速度还能接受。

**仍然慢**：RTF 约 1.1–1.9，一句 50 字的回复要等 15 秒才出声。
下一步如果还要压延迟，只能上 `stream=True`（边生成边播，首声能压到 2–3 秒），
或者换更小的模型。


### 26.8 最终音色：中文样本 + 0.9 倍速（v0.17.2）

用户对比后选了**中文样本**（`models/voices/ref-cn.wav`，来自 中文.ogg）：
"确实会舒服许多"。日文样本走 cross_lingual 也能用，但转写质量差，音色略飘。

语速从 1.0 调到 **0.9**（"只比现在慢一点点"）：

| speed | 语速 |
| --- | --- |
| 1.0 | 5.6 字/秒 |
| 0.95 | 5.4 字/秒 |
| **0.9** | **5.1 字/秒** |
| 0.85 | 4.8 字/秒 |

0.9 是"听得出来但不拖沓"的位置。

**踩坑**：`tools/tts_preview.py` 在同一个进程里同时导入了 sherpa-onnx（VITS）和
CosyVoice（torch + onnxruntime），偶发 **0xC0000005 访问违例**直接崩。
改成 VITS 在真正用到时才 import，之后稳定。


### 26.9 播报失败：后端接口不齐（v0.17.3）

用户上了 GUI 一测："播放失败"。原因很直接：

`eve_ui/tts.py` 的 `TtsWorker._new_session()` 先找 `backend.new_session()`，
没有就退回 `backend.speak()`。而 `CosyVoiceTts` 当时**只实现了 `synthesize()`**，
两个都没有 → `AttributeError: 'CosyVoiceTts' object has no attribute 'speak'`
→ 界面上就是一句"播报失败"。

补上 `new_session()`（返回 `PlaybackSession`，和 `PiperTts` 用同一套播放管线）
和 `speak()` 之后就正常了。实测 `speak('你好，我是伊芙，测试一下播报。')` 返回 True。

**教训**：给同一个接口加新实现时，要么继承同一个基类，要么加一条
"接口必须齐"的测试。这次加了 `tts_backends_share_the_same_surface`：
断言 PiperTts 和 CosyVoiceTts 都有 `available / synthesize / speak / new_session`。

另外语速按用户反馈改回 **1.0**（0.9 听着偏慢）。


### 26.10 播报念参考文本 / 断断续续（v0.17.4）

用户上机后发现两个问题：**每句话前面都会念一遍参考文本**、**语音断断续续**。

**念参考文本**：日志里那句 warning 是钥匙——
`synthesis text 预热。 too short than prompt text 第一次接触新游戏的时候…`

CosyVoice 的零样本模式把参考文本当成"前文"，目标文本太短（比参考短很多）时，
它会**接着念参考音频**。触发点有两个：
1. `preload()` 里合成了一句假的"预热"（两个字）——比 44 字的参考短太多；
2. 参考音频 10 秒 / 44 字，正常回复的短句也容易触发。

修法：
- 预热只加载模型，**不再合成假文本**；
- 参考从 10 秒缩到 **5 秒**（`ref-cn-5s.wav`，对应文本"第一次接触新游戏的时候，提升等级，成功强化装备的时。"），
  短 prompt 既不容易被"续写"，也更快（同一句 7.2s vs 10.9s）。
- 用本地 ASR 把合成结果转写回来核对："好的。""你好，我是伊芙。"等短句都不再出现参考文本。

**断断续续**：这是 RTF > 1 的必然结果——一句 4 秒的话要合成 7 秒，
播放器只能播完一句、等下一句。改了 `PlaybackSession`：引擎有 `stream_synthesize()`
就边生成边喂音频块（CosyVoice 支持 `stream=True`），首块延迟从 7.4s 降到 6.5s，
句间空隙也小了些。**没有根治**——根治要么换更快模型，要么上更好的 GPU。


### 26.13 颜文字被念出来（v0.18.1）

用户贴的日志里有一行：`INFO synthesis text 在。说吧，要干啥(・ω・)。`

问题不在模型，在**没清洗文本**。`PiperTts.synthesize()` 里一直有
`clean_for_speech(text)`（去 markdown、颜文字、圆括号），我写 `CosyVoiceTts` 时
直接用 `text.strip()`，于是"(・ω・)"被当成正文念，听感就是"括号 点 欧米伽 点 括号"。

修法：`synthesize()` 和 `stream_synthesize()` 都先过 `clean_for_speech()`。
实测 `'在。说吧，要干啥(・ω・)。'` → 清洗成 `'在。说吧，要干啥'`，
合成 2.6 秒音频，ASR 回读不再有奇怪符号。

**教训**：新后端不能只对齐"核心方法名"，还要对齐**预处理**。
后端接口里应该有个共用的 `preprocess(text)`，而不是各写各的。

### 26.14 日志里那些"看着像错"的行

- `no frontend is avaliable`：CosyVoice 自己的文本正则前端（ttsfrd）没装，
  数字/日期不会规整成读法。我们的 `clean_for_speech` 管 markdown 和符号，
  但"2026-11-22"这种还是会被逐字念。要彻底解决得装 ttsfrd（Windows 上麻烦）。
- `Failed to create CUDAExecutionProvider ... cuDNN 9 / CUDA 13`：
  onnxruntime-gpu 和本机 CUDA 版本不匹配，**自动退回 CPU**。
  只影响 speech tokenizer 那一步的速度，不影响正确性。
- 第一条 `yield speech len 1.76 rtf 1.41`、第二条 `rtf 0.33`：
  第一条含 prompt 处理，后面就快了。


### 26.15 说话不连贯：句间静音被加到了每一小块之间（v0.18.2）

用户反馈"音色对了，但说话不连贯，感觉每段太短"。

原因是我上一版加的流式播放：`PlaybackSession._play()` 里原本有一句

```python
elif played:
    stream.write(_silence(rate, SENTENCE_GAP_MS))   # 70ms
```

本来是给"句与句之间留一口气"用的。但流式播放把**同一句话**切成 1.5 秒一块塞进队列，
于是每 1.5 秒就插 70ms 静音——听感就是"一顿一顿、每段都太短"。

两步修：
1. 队列项带上 `sentence_head` 标记，只有一句的**第一块**前面才插换气静音；
2. 更关键：**CosyVoice 默认不用流式播**。音色特征缓存后 RTF 0.83（比播放快），
   整句合成完再播反而连续、无缝。流式只快 1 秒首声，却带来块间缝隙，不划算。

实现上用一个开关：`CosyVoiceTts.stream_playback = False`，
`PlaybackSession` 只在引擎显式声明 `stream_playback=True` 时才走流式。

**教训**：流式不是免费的——把一句话切碎，播放器就得处理"块边界"。
只有合成明显慢于播放（RTF > 1.3）时，流式才值得。


### 26.16 改成"文本出完再播"（v0.18.3）

用户反馈：不管怎么调，边生成边播就是卡。他的原话是"先把流式文本输出完毕，再进行播报"。

改成默认 `tts_live = false`：
- `stream_begin()` **不建会话**，只清空缓冲；
- `stream_text()` 一路累积；
- `stream_end()` 才把整段文本交给 `speak()`，由 `PlaybackSession` 切成 ~34 字的句子逐句合成。

为什么这样就不卡了：整句缓冲（6.3s 音频）比下一句的合成时间（3.6s）长，
生产者始终跑在播放前面，队列不空 → 没有缝。
实测 64 字的一段：合成 11.2s / 音频 12.7s，**RTF 0.88**。

代价是首声变慢：要等整段文本生成完（几秒）+ 第一句合成（3–6 秒）才开口。
这是用户主动选的——连贯比快更重要。

想改回去：`tts_live = true`；想调句间停顿：`PlaybackSession.SENTENCE_GAP_MS`（现 70ms）。


### 26.17 "有声音的断点"：参考音频被从半个字上截断（v0.19.0）

用户描述得很准：**每次播报第一个字之前一定有个"断点"，而且这个断点是有声音的**，
之后每隔一段又有一次。听起来"像文本里本来就有那个断点"。

分析合成出来的波形（"早上好，我是伊芙。"）：

```
前 400ms RMS(每20ms): 0.179 0.112 0.132 0.110 0.080 0.050 0.020 0.007 0.002 0.000 ...
首样本值 -0.1526
```

正常语音不会这样——一开头就是一记 0.18 的冲击，然后 150ms 内衰减到 0，之后才是真正的话。
这就是那个"有声音的断点"：**模型每句开头都吐了一小段杂音**。

根因不在模型，在**参考音频**。之前为了把 10 秒压到 5 秒，我用 `wave[:5*rate]`
硬截，结果截在"成功强化装备的**时**"——半个字上。零样本复刻会模仿参考音频的起音方式，
参考本身收得不干净，模型就学到了"开头先来一下杂音"。

修法三件套：
1. 参考重新切在**完整句子**上（`ref-cn-clean.wav`，5.4s，
   文本"第一次接触新游戏的时候，提升等级，成功强化装备的时候。"）；
2. `tools/prepare_ref.py` 的截断逻辑改成**优先切在停顿处**（找最后一个 >=120ms 的静音）；
3. 播放前给每个音频块加 **5ms 淡入淡出**（`_fade_edges`），消掉残留的爆音。

修完后同一句话：`起音 RMS 0.000 0.000 0.000`，ASR 回读"早上好，我是伊芙"，杂音没了。

**教训**：做声音复刻，参考音频的**首尾必须干净**。宁短勿断——
切在停顿处比多要 0.5 秒重要得多。


### 26.18 数字被念成英文（v0.19.1）

用户反馈"播报数字会说英文"。原因还是那个缺失的文本前端——
CosyVoice 没装 ttsfrd，`123` 这种阿拉伯数字进模型后会被当成英文词读。

修法：新模块 `eve_core/numbers.py`，在 `clean_for_speech()` 里把数字转成中文读法。

| 输入 | 输出 |
| --- | --- |
| `2026年` | 二零二六年（年份逐位） |
| `2026-11-22` | 二零二六年十一月二十二日 |
| `15:30` / `9:05` | 十五点三十分 / 九点零五分 |
| `3.14` | 三点一四 |
| `50%` | 百分之五十 |
| `¥128` | 一百二十八元 |
| `100万` | 一百万 |
| `13800138000` | 一三八零零一三八零零零（≥7 位逐位念） |
| `第3名` | 第三名 |
| `1-2个` | 一到二个 |

顺带把**文件名**也处理了：`report_2026.xlsx` 会念成"文件"，
不然就是一串英文加数字。

坑：`int_to_zh` 第一版对 `100500` 输出"十零万五百"、`100000001` 输出"一零亿零一"——
按 4 位分块、块间补零的逻辑写错了。重写后正确。
测试在 `tests/test_numbers.py`（7 项）。


### 26.19 ttsfrd 在 Windows 上装不了（v0.19.2）

用户让装 ttsfrd。结论：**这条路在 Windows 上走不通**。

- PyPI 上的 `ttsfrd` 0.1.0 是个**空壳**：解开只有 `__init__.py`，没有 `TtsFrontendEngine`；
- 官方在 ModelScope 只提供 `ttsfrd-0.4.2-cp38/cp310-**linux_x86_64**.whl`，
  没有 Windows 轮子；
- 资源包 `resource.zip` 还有 323MB。

所以放弃 ttsfrd，继续用自己的规则（`eve_core/numbers.py` + `clean_for_speech`）。
已经覆盖：数字、年份、日期、时间、小数、百分比、金额、序数、区间、文件名、网址。

### 26.20 网址播报（v0.19.2）

以前网址会念成"h t t p s 冒号 斜杠 斜杠 w w w 点 百度 点 com"。

现在 `clean_for_speech()` 里加了：

1. **协议黑名单**：`https/http/ftp/ftps/ws/wss/sftp/ssh/smb/file/mailto/tel/magnet/irc/data/rtsp/rtmp/git/svn`
   后面的 `://` 整段不念；
2. **www → 三w**：`w{3,}` 一律替换。中文里没人念"达不溜达不溜达不溜"，
   说"三w"最自然；`w` 或 `ww` 保持原样（"没有哪个英文单词是三个 w 连着的"）；
3. **域名数字逐位**：`163.com` → 一六三.com，不当成"一百六十三"；
4. **路径/查询串不念**：`/search?q=天气` 对听者是噪音，直接丢。

例：`打开 https://www.baidu.com 就行` → `打开 三w点baidu点com 就行`。

（v0.20.1 起点也念出来：一开始保留英文句点，模型会直接吞掉，
听起来像"三w baidu com"三段互不相干；换成"点"就自然了。）

### 26.21 唤醒监听：没有麦克风时别把线程弄死（v0.19.2）

用户截图：`唤醒监听出错：PortAudioError ... 'There is no driver installed on your system.' [MME error 6]`

原来 `WakeWorker.run()` 的 try/except 包在 while 外面，一次异常线程就退出了，
而且提示是原样抛出的英文错误。

改：把 try 挪到 while 里面，识别"没有录音设备"这一类（`PortAudioError` / `PaErrorCode` /
`MME error` / `no driver installed` / `Invalid number of channels`），
提示一次中文说明后 **每 3 秒重试**，插上设备自动恢复；其它异常仍然直接报。'


### 26.22 网址变成可点击链接（v0.20.0）

要求：聊天里出现网址要能点开，判断标准是**有没有网络协议、是不是域名**。

新增 `eve_ui/links.py`：

- `find_urls(text)`：识别 `https://…`、`ftp://…`、`www.…`、裸域名 `163.com`；
- `href_for(url)`：没协议的自动补 `https://`（不然点击没反应）；
- `linkify(text)`：转义 HTML → 保住换行/连续空格 → 套 `<a>`，颜色用主题的 `ACCENT`。

`MessageRow.set_text()` 里按需切换：**有网址才用富文本**，没网址保持纯文本
（纯文本不会因为 HTML 转义把空格、符号弄变形）。

几个容易踩的点：
1. 中文标点要紧跟网址就截断（`www.abc.com，然后` 不能把逗号吃进链接）——
   字符类里排掉 `，。；：！？、（）【】「」『』《》`；
2. `report.xlsx`、`3.14`、`a.b` **不能**被当成网址：只认常见 TLD 列表里的后缀；
3. 显示文字一个字都不改（和"播报说三w"是两条互不影响的路径）。

测试：`tests/test_links.py` 9 项 + 用 QLabel 实测（有网址 → RichText + `<a href`，
无网址 → PlainText 且原文不变）。

## 5.9 模型列表与音色列表清理（v0.21.0）

模型菜单只剩两个云端模型：

| id | 说明 |
| --- | --- |
| `deepseek-flash` | 默认，三个档位都用它 |
| `deepseek-pro` | 手动切换，贵但强 |

删掉的：
- `[models.qwen3vl-4b]`（本地 GGUF 视觉）—— 权重 2026-11-22 已删（省 9GB），
  菜单里留着只会误导。`[engines.llamacpp]` 只给它用，一起删。
  代码里的 `local_gguf` 路径**保留**（`runtime.py` 还认），以后下回来加一条配置即可。
- VITS 音色 `vits-zh-hf-eula` / `theresa` / `keqing`（各 132MB，共 396MB）——
  默认后端早就是 CosyVoice 了，这三个只是菜单里的噪音。只留 `vits-melo-tts-zh_en`（182MB），
  `TtsSettings.model` 和 `VoiceSettings.tts_model` 的默认值也跟着改成 melo。

`models/` 现在 2.7GB：CosyVoice-300M 2.2GB + melo 182MB + SenseVoice 229MB + KWS 36MB + paraformer 80MB + 参考音频 1MB。

**顺带修的测试**：`test_agent_loop.py` 里两条用例原来假设 config.toml 有 `qwen3vl-4b`，
删掉后挂了。改成自己构造 `ModelConfig` / 假 cfg，不再依赖真实配置——
这样以后配置怎么变测试都稳。


### 26.23 换回 VITS + 数字按语种念（v0.22.0）

用户听完复刻音色的结论：**吐字不清、有点滑舌**，英文句子里数字也不对。
他给了三条判断，基本都对：

1. CosyVoice 的文本前端（ttsfrd）只有 Linux 轮子，Windows 上没有 → 缺前端，吐字和数字都吃亏；
2. 模型只有 300M，太小（文件 2.2GB）→ 零样本复刻的保真度上不去；
3. 参考音频只有 10 秒 → 音色信息太少。

**决定：默认换回 `vits` + melo**（`tts_model = models/voice/vits-melo-tts-zh_en`，
语速 1.05）。CosyVoice 保留为可选后端（`tts_backend = "cosyvoice"` 随时切回），
模型目录 `models/CosyVoice-300M-SFT`（2.2GB）暂时留着，要删随时说。

**顺手修的真 bug**：数字规范化不分语种。

以前 `normalize_numbers()` 一律转中文，于是全英文回复里 `123` 变成"一百二十三"，
中文 TTS 念成怪音、英文 TTS 根本读不出中文——两头不讨好。

现在按**语境**分流（看数字前后 10 个字符、且不跨标点）：

| 语境 | 输入 | 输出 |
| --- | --- | --- |
| 中文 | `123` | 一百二十三 |
| 英文 | `There are 123 files` | There are one hundred and twenty-three files |
| 英文 | `2026-11-22` | November twenty-second, twenty twenty-six |
| 英文 | `$128` / `50%` | one hundred and twenty-eight dollars / fifty percent |
| 中文 | `把 3 个文件…` | 把 三个文件… |

中英混排时各按自己那侧算：`Open 2 files，然后把 3 个删掉` →
`Open two files，然后把 三个删掉`（靠"只看最近标点之间"实现，
不然左边的英文会把右边的汉字数字带偏）。

测试：`tests/test_numbers.py` 从 9 项加到 **14 项**，新增英文数字/日期/金额/序数/
混排用例。全套 277 项全绿。


### 26.24 训练数据可以分段（v0.23.0）

用户问"训练数据能不能切成一段段短音频"。**可以，而且短片段才是标准做法**——
GPT-SoVITS / CosyVoice 微调吃的就是 3–10 秒的小片段 + 每段文字稿，
喂一整个长音频反而要自己切。

新增 `tools/build_dataset.py`，一条命令把零散录音整理成数据集：

```powershell
& 'D:\python\python.exe' tools\build_dataset.py 录音目录 --out models/dataset --min 3 --max 10
```

它做六件事：
1. 读入 wav/mp3/m4a/flac/ogg（soundfile 能读的都行）；
2. 转单声道 + 统一采样率（默认 32k = GPT-SoVITS 的要求，CosyVoice 可 `--sr 24000`）；
3. 砍首尾静音；
4. 超过 `--max` 秒的**自动切段，切在停顿处**（沿用 `prepare_ref.cut_at_pause` 的思路：
   只认 ≥250ms 的静音，绝不从词中间下刀）；
5. 低于 `--min` 秒的碎尾并回上一段；
6. 用本地 SenseVoice 转写每段，写 `list.txt`（`wav路径|说话人|语种|文本`）。

实测：16 秒的合成样例切成 3 段（4.4s / 6.7s / 5.3s），切的正是那两处停顿。
测试 `tests/test_dataset.py` 6 项（切段范围、无停顿硬切、碎尾合并、静音裁剪、
重采样比例、manifest 格式）。全套 283 项绿。

**给用户的标准**（他没说要做多少，先讲清楚）：
- 每段 3–10 秒，剪在停顿处；
- 不要背景音乐/回声/多人说话；
- 总时长 5–10 分钟起（零样本够用），1 小时以上才值得微调；
- 文字稿要准（自动转写只能当草稿）。


### 26.25 部署 GPT-SoVITS（v0.24.0）

用户决定换 GPT-SoVITS（吐字比 CosyVoice 零样本清楚），要求先部署，数据他自己整理。

**装在哪**：`.vendor/GPT-SoVITS`（gitignore），权重在 `GPT_SoVITS/pretrained_models`，
走 v2 预训练（`gsv-v2final-pretrained` + chinese-hubert + chinese-roberta）。

**踩了六个坑，都记下来避免重复**：

1. **pip 把 torch 换了**。装依赖时 pip 解析出 torch 2.15.0+cpu，覆盖了 2.5.1+cu121，
   torchaudio/torchvision 直接不兼容。教训：**装第三方项目的依赖前先看 pip 的 dry-run**，
   或者用 `--no-deps` 逐个装。恢复方法：从阿里云镜像下 cu121 的 wheel 手动 `--no-deps` 装。
2. **下游下载慢**：download.pytorch.org 卡死在 211MB，aliyun 镜像单线程 1.3MB/s，
   10 线程也才 1.5MB/s（对端限速）。2.3GB 花了 26 分钟。工具：`.tmp/dl_http.py`。
3. **transformers 拒绝 .bin**：4.5x 在 torch<2.6 上禁止 `torch.load` 的 `pytorch_model.bin`
   （安全限制）。解法：自己转成 `model.safetensors`（BART/HuBERT 各一次，
   注意共享张量要 `clone()`，否则 safetensors 拒收）。
4. **`sv.py` 用相对路径**：`os.getcwd() + GPT_SoVITS/eres2net` 和
   `GPT_SoVITS/pretrained_models/sv/...`。解法：加载和合成期间临时 `os.chdir(repo)`，
   结束再切回来。
5. **`fast_langdetect` 缓存目录**：`pretrained_models/fast_langdetect` 不存在就报错，
   手动建目录后它会自己下 125MB 的 lid 模型。
6. **G2PW 中文多音字模型**：默认不存在，调用
   `text.g2pw.onnx_api.download_and_decompress()` 从魔搭拉（608MB，含模型和词典）。

**实测（warm）**：
- 8 字：合成 3.57s / 音频 2.1s → RTF 1.70（短句有固定开销）
- 27 字：合成 3.15s / 音频 4.9s → **RTF 0.64**（比实时快）
- ASR 回读 27 字长句**一字不差**（CosyVoice 同期是"吐字糊"的评价）

**集成**：`eve_core/tts_gptsovits.py`（`available/synthesize/stream_synthesize/speak/new_session`），
`tts_backend = "gptsovits"` 切换，失败自动退回 VITS。
试听：`tools/tts_preview.py --backend gptsovits`。

**默认仍是 vits**，等用户听完 gpt 样本、给了更多数据再决定。

磁盘：`.vendor/GPT-SoVITS` 3.4GB（含 G2PW 608MB、roberta 1.2GB），`models` 529MB。
CosyVoice 模型 2.2GB 已删。


### 26.26 用游戏原声做两个版本（v0.24.1）

用户给了 `video/CN`（64 个 ogg）和 `video/JP`（69 个）——蓝色档案游戏里同一个角色的
短语音（1–15 秒，大多是 1–3 秒的技能喊话）。

`build_dataset.py` 跑完：

| 版本 | 片段数 | 总时长 | 清单 |
| --- | --- | --- | --- |
| CN | 67 | 4.6 分钟 | `models/dataset-cn/list.txt` |
| JP | 73 | 5.1 分钟 | `models/dataset-jp/list.txt` |

参考音频各取最长那句（`prepare_ref.py --max-seconds 10`，切在停顿处）：
- `models/voices/ref-cn-game.wav`（5.9s，"自从认识了老师，爱丽丝的内部就自动编写了一段程序。"）
- `models/voices/ref-jp-game.wav`（7.8s，日文，`新しいゲームに初めて向き合うとき…`）

GPT-SoVITS 零样本实测：

| 组合 | 合成 | 音频 | RTF |
| --- | --- | --- | --- |
| 中文参考 + 说中文 | 6.1s | 5.9s | 1.03 |
| 日文参考 + 说中文（跨语种） | 4.9s | 5.9s | **0.83** |
| 日文参考 + 说日文 | 4.0s | 5.8s | **0.69** |

中文回读一字不差。

**又踩了两个坑**：
1. `ref_audio_path` 传相对路径会失效——合成期间我们 `chdir` 到仓库根，
   相对路径被解析成 `repo/models/voices/...`。改成**构造时**就 `resolve()` 成绝对路径。
2. 日文 G2P 需要 `pyopenjtalk`，它有 Windows 轮子（0.4.1 装得上，首次用会下 22MB 词典）。

**结论**：5 分钟数据足够零样本；要微调（保真度再上一档）建议 30–60 分钟。
转写是 ASR 自动出的，有错字（"魔力充能100%腹肌" 之类），微调前必须人工校对。


### 26.27 第一次真正微调 GPT-SoVITS（v0.25.0）

用户给了完整数据：`video/CN/CN1`（64）+ `video/CN/CN2`（87）= 151 个 ogg，
配 `video/CN-test/CN-test1` 和 `CN-text2` 两组逐条文字稿（同名 .txt）。
两批文件**重名不同内容**，打进数据集时要加前缀（`CN1_` / `CN2_`），否则会互相覆盖。

**数据集**：140 段 / 9.8 分钟（跳过 10 个超过 12 秒的，切了文字就对不上）。
工具加了 `--text-dir`：给了逐条稿就用它，不再 ASR；此时**不切段**（一段音频=一句文本）。

**训练**（`tools/train_voice.py`，等价于 webui 里的按钮）：
1. 预处理三步（BERT 特征 / HuBERT / 语义 token）
2. SoVITS 10 epoch / batch 6 → **3.5 分钟**（1.18 it/s）
3. GPT 12 epoch / batch 6 → **约 45 秒**（4.17 it/s）

**踩了一串坑，全是 Windows + 这块显卡特有的**：

| 现象 | 原因 | 解法 |
| --- | --- | --- |
| 训练 CPU 时间一直是 0，卡死 | `DataLoader(num_workers=4)` 在 Windows 上子进程起不来 | s2/s1 的 `num_workers` 全改 0 |
| `prefetch_factor could only be specified in multiprocessing` | num_workers=0 时该参数必须 None | 两处 DataLoader 一起改 |
| `persistent_workers needs num_workers > 0` | 同上 | 改成 `(num_workers > 0)` |
| `No module named 'text'` | 预处理脚本按 `text.xxx` 导入，需 PYTHONPATH | 传 `GPT_SoVITS` 和仓库根 |
| gradio 5.x import 崩（ast TypeError） | `tools/my_utils.py` 顶层 import gradio | 包 try/except 给个 stub |
| 存权重 `FileNotFoundError` | `logs_s2_v2/`、`GPT_weights_v2/` 不会自己建 | 训练前 mkdir |
| 推理加载训练断点报 `KeyError: 'config'` | `G_*.pth` 是训练断点（model/optimizer），不是推理权重 | 新增 `tools/export_sovits.py` 用 `savee()` 导出 |
| 导出的 config 加载不了 | 必须同时支持 `a.b` 和 `a["b"]`，还要能 pickle | `eve_core.tts_gptsovits.AttrDict` |
| 英文合成 `LookupError` | nltk 的 `averaged_perceptron_tagger` 下不到（GitHub 被墙 + SSRF 拦截） | cmudict 用 PyPI 的 `cmudict` 包补上；tagger 退化成"全当名词" |

**30 秒试听结果**（同一角色，文本见 `.tmp/evaltext/`）：

| 版本 | 输入 | 音频 | 合成耗时 |
| --- | --- | --- | --- |
| 中文 | 110 字 | 23.9s | 23.0s |
| 英文 | 416 字符 | 26.3s | 22.1s |
| 中英混合 | 160 字 | 19.4s | 16.8s |

ASR 回读：中文几乎一字不差，英文完整，中英混排中英文都念对。

**结论**：10 分钟的 151 句数据足够做出可用的微调模型；再往上（30–60 分钟）音色会更稳。

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

测试共 16 个文件 / 241 项：persona / context / workspace / cost / apps / office / voice /
agent_loop / planning / checkpoint / project（语音 50 项；计划 11 项；断点 7 项；多文件 8 项）。

下载语音模型（SenseVoice + silero VAD）：

```powershell
& 'D:\python\python.exe' tools\fetch_voice_models.py --list   # 先看有没有
& 'D:\python\python.exe' tools\fetch_voice_models.py          # 下载（≈156MB 压缩包）
```

## 11. 给下一个对话的开场建议

> 读 `docs/MEMORY.md`。语音（第 12 节）和 agent 能力（第 13 节）两个节点都已完成，
> 测试 241/241，代码在本地和 GitHub 上都是最新。
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

## 16. 跨任务共享 / 多标签页 / 视觉（v0.9.0）

### 16.1 共享笔记（`eve_agent/shared.py` + `tools/shared_tools.py`）

多任务之间传结论：`share_write(title, content)` / `share_read(title="")` / `share_forget(title)`。

- 落在 `.data/shared.json`，**重启也在**（任务条是内存里的，黑板不是）。
- 每条记来源（`档位·请求开头`），读的时候知道是谁说的。
- 正文 ≤2000 字、最多 20 条，写满丢最旧——它是"结论板"，不是第二个上下文。
- `Agent._outgoing()` 每步把黑板摘要拼进 system prompt（最多 6 条 × 600 字），
  所以**每个任务每步都能看到别人的进展，但不污染各自的对话历史**。

**实测**：任务 A 查完 4060 写一条笔记；任务 B（另一个进程）只 `share_read`，
就答出了 8GB GDDR6 + 带宽 + 来源，没有联网。跨进程、跨重启都成立。

### 16.2 浏览器多标签页

`browser_tabs / browser_new_tab / browser_switch_tab / browser_close_tab`，
`browser_open(..., new_tab=true)` 也能直接开新页。

实现坑：`/json/new?url` 的跳转是**异步**的，直接建会先读到 about:blank
（实测：标题是空的、接着 read 报"没有可读文本"）。改成先 `about:blank` 建页、
自己 `Page.navigate`、再等 `location.href` 变成 http 才开始用。

`page_targets()` 里只留 `type == "page"` 且不是 `chrome-extension://` 的——
Edge 自带一堆扩展后台页，不过滤的话序号全乱。

### 16.3 视觉：让云端主控"看"屏幕（`eve_core/vision.py` + `tools/vision_tools.py`）

问题：干活的档位跑云端（DeepSeek 不收图），桌面操作又必须看。
做法：**云端下命令，本地 Qwen3-VL 当眼睛**。

| 工具 | 作用 |
| --- | --- |
| `screen_read(question)` | 截屏 → 问本地视觉模型 → 返回文字描述 |
| `screen_locate(target)` | 截屏 → 问"某控件在哪" → 返回**可点击的屏幕坐标**（已是真实坐标） |
| `image_read(path, question)` | 对已有图片提问 |

坐标换算：图会先缩到 1600px 再送模型（省 token、快一倍），模型给的是**图上的像素**，
`locate()` 用缩放比例换回真实屏幕坐标——不换算的话点下去会偏。

代价：第一次调用要拉起本地引擎（约 30s、4–5GB 显存），之后每张图 12–20s。
不用就不占。

### 16.4 实测（2026-11-14）

- **打开软件**：抖音 / Steam / 网易云音乐 三个都开起来了，窗口标题确认（¥0.0026）。
- **操作软件**：网易云任务走到 24 步上限。过程是通的——`screen_read` 说"搜索框在顶部中央"，
  `image_read` 给出坐标 (340,155)-(505,190)，`mouse_click(422,172)` 点中，输入 + 回车，
  再截图确认"搜索结果第一首不是《晴天》"。**卡在 24 步和每次看图 20–40s**，没跑完。
- **共享笔记**：A 写、B 读，B 只凭笔记答对（见 16.1）。
- **多标签页**：开两页、列序号、切回、关掉，全部正确。

### 16.5 结论与下一步

视觉链路可用但**慢**（4B 视觉模型 + 2560×1600 截图）。想让它真正"会用软件"：
1. 视觉模型换更小的（如 Qwen2.5-VL-3B int4）或只截活动窗口，把单次压到 3–5s；
2. 给桌面档更长的步数（现在 20，实测要 30+）；
3. 优先用快捷键（Ctrl+F 这类）绕开"找控件"，把视觉留给验证。

### 16.6 只截活动窗口（v0.9.1）

用户问"只截活动窗口效果怎么样"，实测结论：**值得，但要配合限 image tokens**。

做法：
- `foreground_window()` 用 `GetForegroundWindow` + `DwmGetWindowAttribute(扩展边框)`
  拿当前窗口的可见矩形（不是 `GetWindowRect`，那个含不可见阴影边）。
- 整屏抓一次再**裁**（PIL `ImageGrab(all_screens=True)`），不是按窗口重抓——
  这样多显示器、负坐标都天然对。
- `Shot` 记住裁剪原点，`locate()` 换算坐标时 `x = 图坐标 / 缩放比 + 原点`。
  不换算的话点下去会偏移（和全屏时代一样的坑，只是从"缩放"变成"缩放+偏移"）。
- 拿不到窗口（桌面壳、太小、无标题）就退回全屏。

**DPI 是坑**：进程不说清的话 `GetWindowRect` 给逻辑像素、`ImageGrab` 给物理像素，
缩放屏上会裁偏。模块导入时调一次 `SetProcessDpiAwareness(2)`，GUI 和 CLI 都一致。

数字（同一台机，2560×1600，窗口占右半屏）：

| 项 | 全屏 | 只截活动窗口 |
| --- | --- | --- |
| 图片像素 | 2560×1600 → 缩到 1600×1000 | 1280×1600 → 缩到 848×1060 |
| 送模型 | 1.6MP | 0.9MP |
| 单次耗时 | 13–15s | 13–15s（**没变**） |
| after `--image-max-tokens 256` | — | **6.0–9.5s** |

**关键发现**：像素减半**不省时间**——llama.cpp 的 mtmd 给每张图一个固定 token 预算，
时间主要花在那上面。改 `[engines.llamacpp] extra_args` 加 `--image-max-tokens 256`
才真正把 15s 压到 6–9s（128 更快但坐标更糊，256 是甜点）。

精度：大目标准（"顶栏的 EVE 文字"→ (1367,18)），小控件只能到大致区域
（"发送按钮"偏几十像素）。所以**别指望它精准点小按钮**，能键盘就键盘。

另外修了两个小问题：截图文件名只到秒，同一秒截两张会互相覆盖（加随机后缀）；
`[vision] model` 留空时自动挑第一个带 mmproj 的模型。

## 17. 全云端 + 换视觉模型（v0.10.0）

用户决定：本地 Qwen 不做聊天了，聊天和 agent 都走云端，本地只留一个"眼睛"。

### 17.1 改了什么

| 项 | 之前 | 现在 |
| --- | --- | --- |
| `chat` 档 | 本地 qwen3vl-4b | **deepseek-flash**（云端） |
| `desktop` / `work` | deepseek-flash | 不变 |
| 本地模型用途 | 聊天 + 视觉 | **只有视觉** |
| `[vision] model` | 空（自动挑） | `qwen3vl-8b`（没下载会自动退回 4b） |

聊天改云端后有两件事要注意：
1. **陪聊开始花钱**了（¥2/M 入、¥8/M 出，命中缓存 10%）。一轮闲聊约 ¥0.001–0.002。
2. 本地 4B 的 GGUF（2.4GB）现在只当视觉模型用；想省磁盘可以只在需要视觉时用 8B。

### 17.2 推荐的视觉模型

**Qwen3-VL-8B-Instruct**（Q4_K_M）——和原来的 4B 同一族，llama.cpp 的 mtmd 用法完全一样，
换过去不用改代码，只是更准（认界面、读小字、找控件）。

| 文件 | 大小 | 放哪 |
| --- | --- | --- |
| `Qwen3VL-8B-Instruct-Q4_K_M.gguf` | ≈5.0 GB | `models/Qwen3-VL-8B-Instruct-GGUF/` |
| `mmproj-Qwen3VL-8B-Instruct-F16.gguf` | ≈1.7 GB | 同上 |

来源：<https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF>，国内镜像 `hf-mirror.com`。

显存账：8GB 卡上 5.0 + 1.7 ≈ 6.7GB，ctx 8192 还够；紧张就换 `Q3_K_M`（≈3.9GB）。
CPU 也可以跑（`ngl` 调小），只是慢。

### 17.3 配置怎么写好的

- `[models.qwen3vl-8b]` 已经写好（path/mmproj 指向上面两个文件）。
- `[vision] model = "qwen3vl-8b"`。
- `pick_vision_model()` 会**检查文件存在**：8B 没下载就自动用 4B；
  下好了重启程序就自动生效，不用改配置。
- 旧的 `qwen3vl-4b` 保留当后备，`qwen3vl-4b-small` 用来测 4k 上下文。

### 17.4 测试

`local_model_is_free` / `context_budget_defaults_to_auto` / `budget_never_exceeds_model_window`
这三条原来写死了"chat 用本地 8k"，chat 换云端后全挂。改成从配置算，不再写死数字。
新增"没下载的视觉模型会被跳过"两条用例。14 文件 / 214 项全绿。

## 18. 视觉模型怎么选（v0.10.1）

用户问"这视觉能力能行吗，要换就一步到位"。结论先说：**通用 VLM 不行，得换 GUI 专用模型。**

### 18.1 为什么通用模型不行

Qwen3-VL 这类模型是"描述图片"训出来的，不是"在截图里找控件"训出来的。实测 4B：

- 大目标准（"顶栏的 EVE 文字" → (1367,18)）
- 小控件只能给到大致区域（"发送按钮"偏几十像素）——**点不中**

换成 8B 会好一些，但仍然不是质变：这类模型在 ScreenSpot（给截图找控件）上
普遍只有 70% 左右，而专门训练的 GUI 模型能到 85–90%。

### 18.2 一步到位该换什么

| 模型 | 定位 | 备注 |
| --- | --- | --- |
| **UI-TARS-1.5-7B** | 字节的 GUI agent 模型，专练"截图 → 点哪" | 7B 档精度最高；社区有 GGUF（Mungert/UI-TARS-1.5-7B-GGUF） |
| **Holo1.5-7B** | H Company 的 computer-use 模型 | 同样有 GGUF |
| Qwen3-VL-8B | 通用模型里最强的之一 | **保底选择**：同族、装好即用，不用调格式 |

要"一步到位"就是 **UI-TARS-1.5-7B**；不想折腾就 **Qwen3-VL-8B**。

### 18.3 代码已经能接 GUI 模型了

GUI 专用模型不吃"回个 JSON"那套：

- 它输出 `Action: click(start_box='<|box_start|>(x,y)<|box_end|>')`
- 坐标是 **0–1000 归一化**，不是像素

所以 `vision.py` 里加了一套格式分流：

| 配置 | 行为 |
| --- | --- |
| `[vision] format = "auto"`（默认） | 模型名/路径里有 `ui-tars` / `holo` → 用 GUI 格式，否则用 JSON |
| `"uitars"` | 强制 GUI 格式 |
| `"general"` | 强制 JSON 像素 |

换算链：归一化坐标 → `x/1000*原图宽` → 加裁剪原点 → 真实屏幕坐标。
如果某些转换版直接给像素（>1000），自动走像素分支。

### 18.4 但要认清一件事

**单步 90% 精度，20 步任务的成功率是 0.9^20 ≈ 12%。** 所以视觉再准也不能盲点，
流程上必须：

1. 能用快捷键就用（Ctrl+F、Tab 导航），视觉只兜底；
2. 每个动作后 `screen_read` 确认状态变了，再走下一步；
3. 步数给够（桌面档 20 步对"打开→搜索→播放"这种任务还是紧）。

这三件事都已经在 `prompts/desktop.md` 里写着了。

### 18.5 测试

新增 2 条：GUI 模型识别 + 归一化坐标解析（含 fail 和像素两种分支）。
14 文件 / 216 项全绿。

## 19. UI-TARS 落地实录（v0.11.0）

### 19.1 下载：魔搭有，而且不用梯子

`Mungert/UI-TARS-1.5-7B-GGUF` 在魔搭上有完整一套：

| 文件 | 大小 |
| --- | --- |
| `UI-TARS-1.5-7B-q4_k_m.gguf` | 4556 MB |
| `UI-TARS-1.5-7B-f16.mmproj` | 1291 MB |

**坑**：投影文件叫 `.mmproj` 不是 `mmproj-*.gguf`，我按 `.gguf` 过滤时以为"没有视觉投影"，
差点白扔。两个文件头都是 `GGUF`，llama.cpp 按路径读、不看后缀。

速度实测（本机，1G 宽带，无梯子）：

| 源 | 速度 |
| --- | --- |
| 魔搭 | 0.7–1.0 MB/s |
| hf-mirror + 梯子 | 1.2 MB/s |
| hf-mirror 无梯子 | 0.37 MB/s |
| 魔搭 4 路并行 | 1.07 MB/s（**并行没用，瓶颈是总带宽**） |

全量约 2 小时。用 `.tmp/dl_ui_tars_ms.py`（分块 2MB + 超时重试 + 断点续传），
`tools/model_progress.py` 看进度。

### 19.2 加载：llama.cpp 能直接吃

5.85GB，8GB 卡上起得来（6 秒），输出是正确的 `Action: click(start_box='(x,y)')`。
不需要改 chat template，特殊 token 都在词表里。

### 19.3 关键发现：`--image-max-tokens` 决定成败

一开始定位全错（点"发送按钮"落在屏幕中间），先怀疑 mmproj 不匹配，
最后发现是**我们把 image tokens 限制成了 256**——那是为了 4B 提速设的，
对通用描述够用，对"精确找一个控件"就是灾难。

| image-max-tokens | 系统托盘 | 输入框 | 发送按钮 | 单次耗时 |
| --- | --- | --- | --- | --- |
| 256 | ❌ (1462,510) | ❌ 偏上 | ❌ 偏左 | 9s |
| 1024 | ✅ (2266,1489) | ✅ (1746,1414) | ✅ (2166,1484) | 17s |
| 2048 | ✅ | ✅ | 略偏 | 18s |

**1024 是甜点**：再往上不涨精度，只涨时间（瓶颈在别处）。

### 19.4 顺带修掉的坐标 bug（一直存在）

`capture()` 原来会把截图缩到 1600px 再存盘，但 `locate()` 换算时只考虑 `_prepare()`
的缩放——于是"图上的像素"和"屏幕像素"差了 1.5–2 倍，**所有视觉定位的坐标都偏小**。
之前 4B 点"发送按钮"落在 (2180,900)，x 甚至超出了图宽（848），就是这个原因。

现在 `capture()` 存原图（全屏 2560×1600 / 活动窗口 1280×1600），缩放只在送模型前做，
缩放比交给坐标换算。加了回归测试：全屏截图的 size 必须等于系统虚拟桌面尺寸。

### 19.5 端到端验证

用记事本做闭环（不碰用户正在用的窗口）：

```
app_launch(notepad) → 截图 → locate("记事本中间白色的文字编辑区域")
→ mouse_click(1395,790) → key_type("EVE-UITARS-TEST-42")
→ ctrl+a / ctrl+c → 读剪贴板
```

结果：剪贴板里就是 `EVE-UITARS-TEST-42` ✅ —— 视觉定位 → 点击 → 输入整条链路通了。

### 19.6 现在的能力边界

- 大控件（输入框、按钮、托盘、标题栏）：准，可以真点
- 极小控件（20px 图标）：仍会偏，别指望
- 每次定位 17 秒，20 步任务光看就 5 分钟起
- 所以流程上还是：**能用快捷键就用快捷键，视觉用来定位大目标和验证结果**

## 20. 视觉改走云端（v0.12.0）

### 20.1 先认个错

从 v0.9 起我一直在做"本地视觉模型当眼睛"，前提是"DeepSeek 不收图"。
这个前提**是错的**，而且一次都没验证过：

```
GET /v1/models
deepseek-flash   DeepSeek-V4.1-Flash   ctx 1,048,576   input_modalities: ["text","image"]
deepseek-v4-pro  DeepSeek-V4-Pro       ctx 1,048,576   input_modalities: ["text"]      ← 只有 pro 不收图
```

用一张 480×180 的图问"写了什么字、方块什么颜色"，它答对"EVE-42"和"红色"。
真实截图问"最显眼的窗口标题"，答对。**所以本地那套从根上就不必要。**

代价：一张 1280×800 截图 ≈ 682 tokens、2–4 秒。比本地 17 秒快一个数量级，还更准。

### 20.2 但"能看图"不等于"能点准"

让 flash 直接给像素坐标，结果不能看：

| 目标 | 它给的 | 实际 |
| --- | --- | --- |
| 输入框 | (650,730) | 大致对 |
| 发送按钮 | (1082,**929**) | y 超出图高 800，直接废 |
| EVE 图标 | (19,282) | 偏 260px |

这不是 DeepSeek 的问题，通用 VLM 的坐标回归都这样（Qwen、UI-TARS 同样）。

**能用的替代：九宫格分类。** 给图打 3×3 网格、标 1–9，问"目标在哪一格"：

| 目标 | 结果 | 耗时 |
| --- | --- | --- |
| 输入框 | ✅ 第 8 格 | 1.2s |
| 左上角标题 | ✅ 第 1 格 | 1.3s |
| 右下角托盘 | ✅ 第 9 格 | 1.9s |

3/3、3 个输出 token。**关键参数**：`thinking: {"type":"disabled"}` + `effort: "low"`。
不关思考的话，推理模型会把 token 全花在"分析"上，答案反而空着返回。

### 20.3 试过但不行的两条路

1. **多级细化**（选中格子裁出来再切 3×3）：会飘。输入框 L2 飘到 (1278,1470)、
   标题 L2 直接跳到右半屏。外扩 0.45 和 0.9 都试过，命中率约 1/3。
2. **二分提问**（"在左半还是右半"）：更差，8 次问答误差 200–1400px。

结论：**只用一级 3×3**，精度就是 1/3 屏，靠"点完再确认"补。工具返回格心 +
四个内接点，作为候选点击位置。

### 20.4 工具改成了什么样

| 工具 | 变化 |
| --- | --- |
| `screen_read` | 默认走云端：截图直接发给主模型 |
| `screen_locate` | 九宫格，返回区域 + 5 个候选点 + "点完必须确认"的提示 |
| `image_read` | 走云端 |

`[vision] backend = "cloud"` 控制；`"local"` 保留本地那套（代码和模型都还在，用户要观察一周再删）。

### 20.5 步数

UI-TARS 文档说 GUI 任务 50–100 步，我们原来 desktop 只有 20 步——
「网易云搜歌播放」那次就是撞 24 步没跑完。

| 档位 | 原来 | 现在 |
| --- | --- | --- |
| chat | 12 | 12 |
| desktop | 20 | **60** |
| work | 32 | **100** |
| 硬上限 | 60 | **150** |

配套的**鬼打墙熔断**：同一个 `(工具, 参数)` 连做 3 次就停，返回
"同一个动作连续做了 3 次，我先停下了"。没有这个，100 步只会烧更多钱。

### 20.6 实测（真跑了一遍）

桌面档，任务："看有哪些窗口 → screen_read 看最前面是什么界面 → screen_locate 找底部输入框，别点"

- `window_list` → `screen_read`（4 秒）→ `screen_locate`（4 秒）→ 总结
- 它读出了页面上的占位文字「输入文本试试听你克隆的声音」、右上角 0/120、
  黑色「生成语音」按钮
- 还主动纠正了我的假设："输入框不在页面底部"，并提醒"这块区域 853×534 偏大，第一个候选点不一定落在框里"
- 全程 ¥0.0043

### 20.7 还没解决的

- 小控件（20px 图标）仍然点不准 → 用键盘
- 九宫格只有 1/3 屏精度 → 靠候选点 + 验证循环
- 截图**不能进对话历史**（否则 100 步会把上下文塞爆、费用爆炸）：现在工具只返回文字，天然满足；
  以后要是改成"直接塞图给主模型"，必须做旧图裁剪

### 20.8 窗口漂移（v0.12.1）

**症状**：agent 让 `window_focus` 切到网易云，下一句 `screen_read` 又截回了 EVE 自己的窗口，
模型对着一张聊天记录一本正经地分析，还给出坐标。

**根因**：`screen_read` 截的是**前台窗口**，而 agent 自己就住在 EVE 窗口里，
任何一次模型回复/界面刷新都可能把前台抢回去。

**修法**：记住"当前在操作的窗口"（`desktop.set_target`，thread-local，多任务互不干扰）：

| 动作 | 效果 |
| --- | --- |
| `window_focus` | 设为目标 |
| `app_launch` | 启动/激活后设为目标 |
| `window_close` | 关的是目标就清掉 |
| `screen_read` / `screen_locate` | 截**目标窗口**；窗口没了/最小化才退回前台，再不行退回全屏 |

**验证**：把网易云设为目标后，用 `_force_foreground` 把 EVE 抢到前台，再截——拿到的
仍然是网易云 1588×1128。实跑一句"看现在在播什么歌"，返回 SADVENTURES / 03:05，没有截错窗口。

**附带发现**：模型读小字（歌名）会抖，同一行两次读成 "TURES" / "SADVENTURES"。
所以核对身份时优先用**窗口标题**（"歌名 - 歌手"），比 OCR 稳。

## 21. ui_find：三层定位（v0.13.0）

### 21.1 为什么不是一层

| 手段 | 覆盖 | 精度 | 成本 | 死穴 |
| --- | --- | --- | --- | --- |
| 色块检测 | 饱和色实心按钮 | 像素级 | 10ms | 纯文字按钮、透明按钮、和背景同色的按钮 |
| 九宫格 | 什么都行 | 1/3 屏 | 1.2s | 太粗，小控件必失 |
| **OCR** | **有文字的控件（绝大多数）** | **10–15px** | ~0.5s | 纯图标（播放三角、齿轮、音量） |

用户问得对："色块遇到复杂背景还有用吗、UI 和背景同色只有文字不同怎么办"——
色块只覆盖第一类，答案是 **OCR**。

### 21.2 OCR 实测（Windows 自带引擎，winsdk）

同一张网易云截图（复杂壁纸 + 半透明面板）：

| 处理 | 耗时 | 找到 |
| --- | --- | --- |
| 原图 | 197ms | 播放全部、下载、最近播放 |
| 2× 放大 + 对比度 1.8 | 282ms | 推荐、播客（低对比度导航项） |

两遍**互补**，合并后 4/4。bbox 误差 10–15px。

**两个坑**：
1. `StorageFile.get_file_from_path_async` 不认相对路径，要 `resolve()`，否则 WinError -2147024809。
2. 中文小字会糊（"播客"→"间播客"），所以 `find_text` 允许按字重叠匹配（≥60% 命中）。

### 21.3 截图被遮挡（这轮最大的坑）

`screen_read` 是"抓整屏再裁窗口"——如果目标窗口被别的窗口挡着，**裁出来的就是别人的画面**。
实测：网易云在 EVE 后面，裁出来的图里全是聊天记录，OCR 读到的也是聊天文字。

修法：截图前把目标窗口 `SetWindowPos(HWND_TOPMOST)` 顶上来，抓完立刻还原
（~0.12s，窗口原本就是置顶的也能正确还原）。不引 PrintWindow——那个会遇到
DPI 缩放（1588×1128 的窗口只渲染出 1059×752 的内容），坐标还要再换算一道。

### 21.4 三层链的实现

```
ui_find(target)
  ├─ OCR 命中 1 条 → 直接返回该文字 bbox 中心
  ├─ OCR 命中多条 → 把文字列成菜单，让模型选编号（纯文本请求，10 token）
  ├─ 色块 1 个 → 直接返回；多个 → 画编号让模型选
  └─ 九宫格兜底 → 返回粗区域 + 提示
```

每层都返回"来源"（ocr / color / grid），方便事后归因。

实测（网易云窗口，复杂壁纸）：

| target | 结果 | 耗时 |
| --- | --- | --- |
| 播放全部 | OCR (390,273)-(490,293) | 1.5s |
| 下载 | OCR (579,274)-(619,293) | 1.3s |
| 推荐 | OCR (90,151)-(132,172) | 2.5s |
| 最近播放 | OCR (90,608)-(174,629) | 1.3s |

### 21.5 依赖

`winsdk`（pip 装，Windows 自带 OCR 引擎，不下载模型）。没有它时
`ocr.available()` 返回 False，ui_find 自动跳过第一层，不影响其他功能。

## 22. 下一节点候选：把步数压下来（2026-11-19 记）

视觉链路已经能完整跑通（网易云搜歌 → 抖音搜同名 → 微信发给文件传输助手，三个独立进程，
靠共享笔记串起来，合计 ¥0.045）。现在的主要成本是**步数**：一次桌面任务要 20–33 步，
每步一次模型往返 + 一次截图。

用户的想法（原话大意）：第一次自动化时后台记录坐标，下次直接复用。
他自己也意识到漏洞：**这就是硬编码，只有命令一模一样才成立**。

我的判断：方向对，但记录的粒度要换——不要记"坐标"，记"意图"：

| 方案 | 记什么 | 换分辨率/窗口大小后 | 换软件版本 |
| --- | --- | --- | --- |
| 坐标缓存 | (x, y) | ❌ 全废 | ❌ 全废 |
| **语义动作**（推荐） | 窗口标题 + 控件文字 + 是点击还是输入 | ✅ ui_find 重新定位 | ✅ 只要文字还在 |
| 计划模板 | todo 列表 + 每步该用的工具 | ✅ | ⚠️ 步骤变了要改 |

语义动作长这样：

```json
{"window": "网易云音乐", "action": "click", "target": "播放全部"}
{"window": "抖音", "action": "type", "target": "搜索框", "text": "晴天"}
```

回放时用 `ui_find(target)` 重新定位——我们有 OCR（10px）和色块，重定位成本 1–2 秒，
但省掉的是"模型想下一步做什么"这一整轮往返。命中就跳过模型，没命中再问模型。
这本质上是**给 agent 加一层"熟练度"**：做过的操作变成肌肉记忆，没做过的才动脑。

风险：界面变了还按老套路点 → 所以每次回放后仍要 `screen_read` 验一下，
连续两次对不上就把这条记忆作废（跟鬼打墙熔断一个思路）。

### 22.1 另外记一条测试原则

用户明确说过：**视觉测试不要写成固定脚本**——它测的就是随机性（每次界面状态、
模型输出都不一样），写死坐标和期望值就变成"测我自己写的常量"了。
所以回归测试只覆盖确定性部分（OCR 匹配、坐标换算、熔断），端到端一律真跑。
