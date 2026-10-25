# EVE

本地优先的个人 agent。本地模型负责陪伴与简单的桌面操作，云端模型负责真正干活的事。

## 设计

- **手动切档，不做自动路由**。每个档位 = 一个 provider + 一段 system prompt + 一组工具，就是一张表。
- **模型接口不写死**。所有远程模型走 OpenAI 兼容协议（`base_url` + `model` + key），换厂商改配置即可。
- 详细设计见 [docs/design-mvp.md](docs/design-mvp.md)。

| 档位 | 模型 | 定位 |
| --- | --- | --- |
| `chat` | 本地 GGUF | 闲聊、陪伴 |
| `desktop` | 本地 GGUF + 视觉 | 开软件、搜索、点击 |
| `work` | 云端 API | 整理文档、写代码、爬数据 |

## 环境

- Python 3.11：`D:\python\python.exe`（不在 PATH 里，需显式调用）
- 无第三方依赖，只用标准库（`tomllib`、`urllib`）

## 快速开始

```powershell
# 1. 起本地推理服务（读 config.toml 的 [engine] 段）
& 'D:\python\python.exe' -m eve.serve

# 2. 另开一个终端，开始对话
& 'D:\python\python.exe' -m eve

# 非交互跑一句
& 'D:\python\python.exe' -m eve --once "你好，介绍一下你自己"
```

## REPL 命令

| 命令 | 说明 |
| --- | --- |
| `/mode <name>` | 切换档位（chat / desktop / work） |
| `/reset` | 清空当前会话上下文 |
| `/tools` | 列出当前档位可用的工具 |
| `/where` | 显示当前 provider / 模型 / 端点 |
| `/help` | 帮助 |
| `/exit` | 退出 |

## 状态

- [x] v0.1 骨架：配置、provider 抽象、多轮对话、切档、日志
- [ ] v0.2 desktop 档：桌面工具 + 确认机制
- [ ] v0.3 work 档：云端接入 + 文件/命令/网络工具
