# EVE

本地优先的个人 agent。本地模型负责陪伴与简单的桌面操作，云端模型负责真正干活的事。

## 设计

- **手动切档，不做自动路由**。每个档位 = 一段 system prompt + 一组工具 + 一个默认模型。
- **模型是一等公民**。`[models.*]` 声明有什么，`[modes.*]` 只声明用哪个；换本地模型、换云端厂商都只是改配置。
- **本地模型的引擎在执行时按需拉起**，动态端口，退出即释放。
- 详细设计见 [docs/design-mvp.md](docs/design-mvp.md) 与 [docs/design-v2-ui-voice.md](docs/design-v2-ui-voice.md)。

| 档位 | 默认模型 | 定位 |
| --- | --- | --- |
| `chat` | 本地 GGUF | 闲聊、陪伴 |
| `desktop` | 本地 GGUF + 视觉 | 开软件、搜索、点击 |
| `work` | 云端 API | 整理文档、写代码、爬数据 |

## 环境

- Python 3.11：`D:\python\python.exe`（不在 PATH 里，需显式调用）
- 无第三方依赖，只用标准库（`tomllib`、`urllib`、`ctypes`）

## 快速开始

```powershell
# 直接对话（本地引擎会自动拉起，约 3–6 秒）
& 'D:\python\python.exe' -m eve

# 指定档位 / 临时指定模型
& 'D:\python\python.exe' -m eve --mode desktop
& 'D:\python\python.exe' -m eve --model qwen3vl-4b-small

# 非交互跑一句
& 'D:\python\python.exe' -m eve --once "你好，介绍一下你自己"
```

## REPL 命令

| 命令 | 说明 |
| --- | --- |
| `/models` | 列出已配置的模型，`*` 标出当前使用 |
| `/use <id>` | 给当前档位换模型（保留上下文，会重启本地引擎） |
| `/scan` | 扫描 `model_dirs` 下可用的 GGUF，含量化等级与 mmproj 配对 |
| `/mode <name>` | 切换档位（会重置上下文） |
| `/status` | 运行时状态：引擎、端口、pid、加载耗时、日志路径 |
| `/stop` | 停止本地引擎，释放显存 |
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

[models.deepseek]
kind = "cloud_api"           # 云端 API
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
api_key_env = "EVE_CLOUD_API_KEY"

[modes.chat]
model = "qwen3vl-4b"         # 档位只绑模型 id，不绑类型
```

## 状态

- [x] v0.1 骨架：配置、provider 抽象、多轮对话、切档、日志
- [x] v0.2 desktop 档：窗口枚举/激活、键鼠注入、中文输入、动作日志
- [x] v0.3 本地档打磨：重复启动复用窗口、窗口关闭、工具级自测
- [x] v0.4 模型抽象：ModelCatalog + RuntimeManager，本地/云端统一切换，引擎懒启动
- [ ] v0.5 UI（PySide6）：模型面板 + 聊天 + 托盘
- [ ] v0.6 语音：两段式唤醒 + ASR + piper TTS
- [ ] v0.7 打包：PyInstaller spec + 首次运行向导

## desktop 档实测

| 场景 | 结果 |
| --- | --- |
| 「打开记事本」 | `app_launch` → 返回真实窗口标题与 hwnd，一步到位 |
| 「打开记事本并输入 hello from eve」 | 3 步（启动 → 激活 → 输入），约 2 秒 |
| 「输入：你好，我是 EVE」 | 中文走剪贴板 + Ctrl+V，正常 |
| 重复「打开记事本」 | 检测到进程已在运行，直接激活原窗口，不再开新实例 |
| 「关闭记事本」 | `window_close` 发送 WM_CLOSE，窗口消失；有未保存内容会提示 |

## v0.4 实测（模型切换）

| 场景 | 结果 |
| --- | --- |
| 直接对话 | 自动拉起引擎，加载约 6 秒，44 tok/s |
| `/use qwen3vl-4b-small` | 停旧引擎、起新引擎，新端口 53734，加载 3.4 秒 |
| `/status` | 显示 pid、端口、加载耗时、引擎日志路径 |
| `/use deepseek` | 未配 key 时明确报错，指出用 `/key` 或设环境变量 |
