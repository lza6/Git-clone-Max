"""Rev17 真实调用验收：MCP stdio 三跳 + preview_id（走既有 _Session 轨道）。

独立脚本（不复用 tests 的 unittest 断言形态），按 four-track SOP 输出
可判定的 JSON 到 evidence/realcalls-rev17.json。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_g84_mcp_resources import _Session  # noqa: E402  （既有 stdio 会话轨道）


def main() -> int:
    out: dict = {"track": "realcalls-rev17", "checks": []}

    def check(name: str, ok: bool, detail: str = "") -> None:
        out["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""))

    td = pathlib.Path(tempfile.mkdtemp(prefix="gcm_rc_"))
    s = _Session(td, allow_mutate=True)
    import threading
    def _guard():
        import time
        time.sleep(60)
        try:
            s.proc.kill()
        except Exception:
            pass
    threading.Thread(target=_guard, daemon=True).start()
    try:
        r = s.call("initialize", {"protocolVersion": "2024-11-05",
                                  "capabilities": {},
                                  "clientInfo": {"name": "audit", "version": "0"}})
        info = r["result"]["serverInfo"]
        check("initialize handshake", info["name"] == "gcm", f"v{info['version']}")
        # notifications/initialized 是通知（无响应）：直接写行，不走 call()
        s._id += 1
        s.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + chr(10))
        s.proc.stdin.flush()

        tools = s.call("tools/list")["result"]["tools"]
        names = [t["name"] for t in tools]
        check("tools >= 7 (含 job_status/job_cancel)",
              {"clone_batch", "list_repos", "search_repos", "export_report",
               "status", "job_status", "job_cancel"} <= set(names), str(names))

        res = s.call("resources/list")["result"]["resources"]
        uris = {x["uri"] for x in res}
        check("resources 4 个（含 gcm://history）",
              {"gcm://status", "gcm://repos", "gcm://report/markdown",
               "gcm://history"} <= uris, str(sorted(uris)))

        h = s.call("resources/read", {"uri": "gcm://history"})["result"]["contents"][0]
        data = json.loads(h["text"])
        check("gcm://history 合法 JSON 且空库 count=0",
              data.get("count") == len(data.get("history", [])) == 0, str(data)[:80])

        pr = s.call("prompts/list")["result"]["prompts"]
        check("prompts 2 个", {p["name"] for p in pr} >= {"clone-batch", "diagnose-failure"},
              str([p["name"] for p in pr]))

        cb = s.call("tools/call", {"name": "clone_batch", "arguments": {
            "urls": ["https://github.com/octocat/Hello-World"], "dry_run": True}})
        pid = cb["result"].get("preview_id", "")
        check("clone_batch(dry_run) 带 preview_id（32 hex）",
              len(pid) == 32 and all(c in "0123456789abcdef" for c in pid), pid[:8] + "…")
        plan = cb["result"].get("plan", {})
        check("plan.counts 齐全", plan.get("counts", {}).get("clone") == 1, str(plan.get("counts")))

        # 未知资源错误出口：server 返回 JSON-RPC error（-32602），通道不崩
        resp = s.call("resources/read", {"uri": "gcm://no-such"})
        check("unknown resource errors",
              resp.get("error", {}).get("code") == -32602,
              str(resp.get("error"))[:80])
    finally:
        s.proc.terminate()

    ok = all(c["ok"] for c in out["checks"])
    out["verdict"] = "PASS" if ok else "FAIL"
    dest = ROOT / "evidence" / "realcalls-rev17.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nVERDICT: {out['verdict']}  -> {dest}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
