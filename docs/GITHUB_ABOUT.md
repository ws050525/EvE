# GitHub 仓库配置（复制粘贴用）

仓库页右上角 **About → ⚙ (Edit repository details)**，把下面三段分别贴进去。

## Description（简介，一句话）

```
本地优先的个人 AI agent：能聊天，也能真的动手——开软件、点按钮、搜网页、写文章、做 PPT。
视觉用云端多模态模型直读截图 + OCR/色块/九宫格三层定位；支持断点续跑、多任务共享上下文。
```

> GitHub 的简介上限 350 字符，上面这段约 90 字，放得下。
> 想让搜索更容易命中，可以只用第一句，把后面换成 `Topics`。

## Website（可选）

留空，或者填你自己的说明页。

## Topics（标签，逐个添加）

```
ai-agent
computer-use
desktop-automation
gui-automation
ocr
python
pyside6
deepseek
llm
windows
local-first
```

## 当前版本

`v0.13.0`（见 `eve_core/version.py`）。标签与版本号一一对应，
`git tag --list 'v*' | Sort-Object` 可以核对。
