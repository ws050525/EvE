"""REPL 入口。

刻意做得很薄：参数解析 + 打印 + 斜杠命令，逻辑都在 Agent / RuntimeManager 里。
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from eve_agent import Agent
from eve_core.catalog import scan
from eve_core.config import ConfigError, load_config
from eve_core.pricing import format_money, price_for
from eve_core.provider import ProviderError
from eve_core.runtime import RuntimeManager
from eve_core.version import __version__

BANNER = """EVE v{version} — 本地优先 agent
档位：{modes}
当前：{mode} 档 / 模型 {model}（{kind}，{state}）
输入 /help 看命令，/exit 退出。
"""

HELP = """  /models              列出已配置的模型
  /use <id>            给当前档位换模型（保留对话上下文）
  /scan                扫描磁盘上可用的 GGUF
  /mode <name>         切换档位（会重置上下文）
  /status              运行时状态（引擎、端口、加载耗时、上下文）
  /cost                本次会话的 token 用量与估算花费
  /context [n|auto]    查看或设置上下文预算（token），auto = 按模型窗口
  /steps [n]           查看或设置单条消息的步数上限
  /persona             人格状态（抗议等级、连续命令数、颜文字节流）
  /stop                停止本地引擎，释放显存
   /key <id>            为云端模型保存 API Key（DPAPI 加密）
  /reset               清空上下文
  /tools               当前档位可用工具
  /where               当前 provider / 模型 / 端点
  /exit                退出"""


class StatusLine:
    """一行状态提示，用 \\r 原地刷新；非 TTY 时自动静默。"""

    def __init__(self) -> None:
        self.enabled = sys.stdout.isatty() or os.environ.get("EVE_STATUS") == "1"
        self._width = 0

    def show(self, text: str) -> None:
        if not self.enabled:
            return
        line = f"  · {text}"
        pad = max(0, self._width - len(line))
        sys.stdout.write("\r" + line + " " * pad)
        sys.stdout.flush()
        self._width = len(line)

    def clear(self) -> None:
        if not self.enabled or self._width == 0:
            return
        sys.stdout.write("\r" + " " * self._width + "\r")
        sys.stdout.flush()
        self._width = 0


def _kind_label(kind: str) -> str:
    return {"local_gguf": "本地", "cloud_api": "云端"}.get(kind, kind)


def _resolve_mode(cfg, raw: str) -> str:
    """允许 /mode 工作 这种用显示名切档。"""
    if raw in cfg.modes:
        return raw
    for name, mode in cfg.modes.items():
        if mode.display == raw:
            return name
    return raw


def _price_label(cfg, model_id: str) -> str:
    """模型单价的一行说明：本地模型写"不计费"，云端写「入 / 出」两档。"""
    model = cfg.models.get(model_id)
    price = price_for(model)
    if price.free:
        return "本机算力，不计费"
    return (
        f"入 {price.input:g} / 出 {price.output:g} 元每百万 tokens"
        f"（缓存 {price.cached_rate:g}）"
    )


def _usage_line(agent: Agent) -> str:
    """一次回复之后的用量小结，没拿到 usage 就返回空串。"""
    record = agent.usage.last
    if record is None or not record.usage:
        return ""
    used = record.usage
    detail = f"入 {used.prompt} / 出 {used.completion}"
    if used.cached:
        detail += f" / 缓存命中 {used.cached}"
    parts = [f"本次 {used.total} tokens（{detail}）"]
    if record.cost > 0:
        parts.append(format_money(record.cost))
    session = agent.usage.totals()
    if session.cost > 0:
        parts.append(f"会话累计 {format_money(session.cost)}")
    return "  · " + " · ".join(parts)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(prog="eve", description="本地优先的个人 agent")
    parser.add_argument("--mode", help="启动档位（默认读 config.toml 的 default_mode）")
    parser.add_argument("--model", help="临时覆盖档位使用的模型 id")
    parser.add_argument("--config", help="配置文件路径")
    parser.add_argument("--once", metavar="TEXT", help="非交互执行一句话后退出")
    parser.add_argument("--yes", action="store_true", help="非交互模式下自动批准工具调用")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    mode_name = args.mode or cfg.default_mode

    try:
        mode = cfg.mode(mode_name)
        model_id = args.model or mode.model
        model = cfg.model(model_id)
        kind = model.detect_kind()
        # 模型按需加载：这里只建对象，不发请求，所以 /models /scan 都是秒回
        agent = Agent(cfg, mode_name)
        runtime = agent.runtime
        if args.model:
            agent.switch_model(args.model)
    except (ConfigError, ProviderError, OSError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2

    status_line = StatusLine()
    streamed = {"on": False}

    def on_event(ev) -> None:
        data = ev.data
        if ev.kind == "engine_starting":
            status_line.show(f"加载模型 {data.get('label') or data.get('model_id')}…")
        elif ev.kind == "engine_ready":
            status_line.show(f"就绪（{data.get('load_seconds')}s）")
        elif ev.kind == "engine_stopped":
            status_line.show("引擎已停止")
        elif ev.kind == "thinking":
            status_line.show("思考中…")
        elif ev.kind == "reply_chunk":
            if not streamed["on"]:
                streamed["on"] = True
                status_line.clear()
            sys.stdout.write(data.get("text", ""))
            sys.stdout.flush()
        elif ev.kind == "tool_start":
            status_line.show(f"调用 {data.get('name')}")
        elif ev.kind == "tool_end":
            status_line.show(f"{data.get('name')} {'完成' if data.get('ok') else '失败'}")
        elif ev.kind == "context_compacting":
            status_line.show(f"上下文 {data.get('tokens')} tokens，正在压缩…")
        elif ev.kind == "context_compacted":
            parts = []
            if data.get("dropped_turns"):
                parts.append(f"摘要 {data['dropped_turns']} 轮")
            if data.get("truncated_chars"):
                parts.append(f"截断 {data['truncated_chars']} 字符")
            status_line.show(
                f"上下文 {data.get('tokens_before')} → {data.get('tokens_after')} tokens"
                + (f"（{'，'.join(parts)}）" if parts else "")
            )
        elif ev.kind == "truncated":
            status_line.show("输出被截断（max_tokens 太小）")
        elif ev.kind == "error":
            status_line.clear()

    agent.bus.subscribe(on_event)

    def confirm(spec, tool_args) -> bool:
        if args.yes:
            return True
        preview = ", ".join(f"{k}={v}" for k, v in tool_args.items()) or "无参数"
        answer = input(f"  工具 {spec.name}({preview}) 需要确认，执行吗？[y/N] ").strip().lower()
        return answer in {"y", "yes"}

    if args.once:
        if kind == "local_gguf":
            print("（首次使用会拉起本地引擎，约 3–6 秒）", file=sys.stderr, flush=True)
        try:
            reply = agent.send(args.once, confirm=confirm)
        except (ProviderError, ConfigError) as exc:
            status_line.clear()
            print(f"调用失败：{exc}", file=sys.stderr)
            return 1
        finally:
            runtime.shutdown()
        status_line.clear()
        if not streamed["on"]:
            print(reply.strip())
        else:
            print()
        extra = _usage_line(agent)
        if extra:
            print(extra)
        return 0

    status = runtime.status()
    model = cfg.model(agent.model_id)
    print(
        BANNER.format(
            version=__version__,
            modes=" / ".join(m.display for _, m in sorted(cfg.modes.items())),
            mode=agent.mode_label,
            model=f"{agent.model_id} · {model.label}",
            kind=_kind_label(model.detect_kind()),
            state=status.state,
        )
    )
    if agent.warning:
        print(f"注意：{agent.warning}\n")

    while True:
        try:
            line = input(f"\033[1mEVE[{agent.mode_label}/{agent.model_id}]>\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            runtime.shutdown()
            return 0

        if not line:
            continue
        if line in {"/exit", "/quit"}:
            runtime.shutdown()
            return 0
        if line == "/help":
            print(HELP)
            continue
        if line == "/models":
            active = agent.model_id
            for mid, m in sorted(cfg.models.items()):
                mark = "*" if mid == active else " "
                print(f"  {mark} {mid:<18} {_kind_label(m.detect_kind()):<4} {m.label}")
                print(f"      {_price_label(cfg, mid)}")
            print("  （* 为当前使用；用 /use <id> 切换，用 /cost 看花了多少）")
            continue
        if line == "/scan":
            found = scan(cfg.model_dirs)
            if not found:
                print("  没扫描到 GGUF。检查 config.toml 的 [app].model_dirs")
                continue
            for item in found:
                mm = f"，mmproj: {item.mmproj.name}" if item.mmproj else ""
                print(f"  {item.name}\n      {item.size_gb} GB  {item.quant}{mm}\n      {item.path}")
            print(f"  共 {len(found)} 个。要使用请在 config.toml 里加一个 [models.*] 段。")
            continue
        if line == "/persona":
            snap = agent.persona.snapshot()
            print("  " + "  ".join(f"{k}={v}" for k, v in snap.items()))
            continue
        if line == "/status":
            st = runtime.status()
            print(f"  model={st.model_id or '（未加载）'} kind={_kind_label(st.kind)} state={st.state}")
            used, budget = agent.context_usage
            summary = f"，摘要 {len(agent.summary)} 字符" if agent.summary else ""
            print(f"  上下文≈{used}/{budget} tokens（{len(agent.messages) - 1} 条消息{summary}）")
            setting = agent.context_setting
            print(
                f"  预算设定={'自动' if setting == 0 else setting} · 步数上限={agent.max_steps}"
            )
            totals = agent.usage.totals()
            if totals.calls:
                print(
                    f"  本次会话 {totals.usage.total} tokens（缓存命中 {totals.usage.cached}），"
                    f"估算 {format_money(totals.cost)}"
                )
            if st.base_url:
                print(f"  base_url={st.base_url}")
            if st.pid:
                print(f"  pid={st.pid} 加载耗时={st.load_seconds}s")
            if st.log_path:
                print(f"  引擎日志={st.log_path}")
            if st.error:
                print(f"  错误={st.error}")
            continue
        if line == "/cost":
            totals = agent.usage.totals()
            if not totals.calls:
                print("  本次会话还没调用过模型")
                continue
            print(f"  调用 {totals.calls} 次，共 {totals.usage.total} tokens")
            print(f"    输入 {totals.usage.prompt}（命中缓存 {totals.usage.cached}）")
            print(f"    输出 {totals.usage.completion}")
            print(f"  估算花费 {format_money(totals.cost)}")
            for mid, total in sorted(agent.usage.per_model().items()):
                price = price_for(cfg.models.get(mid))
                money = "不计费" if price.free else format_money(total.cost)
                print(
                    f"    {mid:<18} {total.calls} 次  "
                    f"{total.usage.total} tokens  {money}"
                )
            print("  （单价读 config.toml，按高峰档上限估算，仅供心里有数）")
            continue
        if line.startswith("/context"):
            parts = line.split()
            if len(parts) == 1:
                setting = agent.context_setting
                label = "自动" if setting == 0 else f"{setting} tokens"
                print(f"  上下文预算：{label}（生效 {agent.context_limit} tokens）")
                print("  用法：/context 8000  或  /context auto")
                continue
            raw = parts[1].lower()
            try:
                value = 0 if raw in {"auto", "0"} else int(raw)
            except ValueError:
                print("  用法：/context 8000  或  /context auto")
                continue
            print("  " + agent.set_context_limit(value))
            continue
        if line.startswith("/steps"):
            parts = line.split()
            if len(parts) == 1:
                print(f"  步数上限：{agent.max_steps} 步（用法：/steps 20）")
                continue
            try:
                value = int(parts[1])
            except ValueError:
                print("  用法：/steps 20")
                continue
            print("  " + agent.set_max_steps(value))
            continue
        if line == "/stop":
            runtime.shutdown()
            print("  本地引擎已停止")
            continue
        if line == "/where":
            p = agent.provider
            print(f"  model={agent.model_id} base_url={getattr(p.cfg, 'base_url', '?')}")
            continue
        if line == "/tools":
            print(f"  可用：{', '.join(agent.active_tools) or '（无）'}")
            continue
        if line == "/reset":
            agent.reset()
            print("  上下文已清空")
            continue
        if line.startswith("/key"):
            parts = line.split()
            if len(parts) != 2:
                print("  用法：/key <model_id>")
                continue
            mid = parts[1]
            if mid not in cfg.models:
                print(f"  没有模型 {mid}")
                continue
            try:
                secret = getpass.getpass("  粘贴 API Key（不回显）：").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n  已取消")
                continue
            if not secret:
                print("  空值，未保存")
                continue
            runtime.keys.set(mid, secret)
            print(f"  已加密保存到 {runtime.keys.path}")
            continue
        if line.startswith("/use"):
            parts = line.split()
            if len(parts) != 2:
                print(f"  用法：/use <{'|'.join(sorted(cfg.models))}>")
                continue
            try:
                print("  " + agent.switch_model(parts[1]))
            except (ConfigError, ProviderError) as exc:
                print(f"  切换失败：{exc}")
            continue
        if line.startswith("/mode"):
            parts = line.split()
            if len(parts) != 2:
                joined = "|".join(f"{m.display}" for _, m in sorted(cfg.modes.items()))
                print(f"  用法：/mode <{joined}>")
                continue
            try:
                print("  " + agent.switch_mode(_resolve_mode(cfg, parts[1])))
            except (ConfigError, ProviderError) as exc:
                print(f"  切换失败：{exc}")
            if agent.warning:
                print(f"  注意：{agent.warning}")
            continue
        if line.startswith("/"):
            print(f"  未知命令 {line}，试试 /help")
            continue

        try:
            streamed["on"] = False
            reply = agent.send(line, confirm=confirm)
            status_line.clear()
            if streamed["on"]:
                print()
            else:
                print(reply.strip())
            extra = _usage_line(agent)
            if extra:
                print(extra)
        except (ProviderError, ConfigError) as exc:
            status_line.clear()
            print(f"  调用失败：{exc}")
        except KeyboardInterrupt:
            status_line.clear()
            print("\n  已中断")


if __name__ == "__main__":
    raise SystemExit(main())
