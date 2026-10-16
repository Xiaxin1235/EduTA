import argparse
import os
import sys
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from packages.core.frozen_paths import app_dir, output_dir, web_dir

UI_VERSION = "1.0.0"

def _port_free(port: int) -> bool:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()

def _pick_port(preferred: int) -> int:
    if _port_free(preferred):
        return preferred
    for p in range(preferred + 1, preferred + 30):
        if _port_free(p):
            return p
    return preferred

def _open_browser(url: str):
    import threading
    import webbrowser

    bust = url.rstrip("/") + f"/?ui={UI_VERSION}"
    threading.Timer(1.2, lambda: webbrowser.open(bust)).start()

def cmd_eval(args):
    if getattr(args, "school", None):
        from scripts.school_engine import find_schools, evaluate_school
        from packages.agent.composer import render_markdown
        import json

        keyword = str(args.school).strip()
        year = getattr(args, "year", None)
        sid = None
        if keyword.isdigit():
            sid = int(keyword)
        else:
            hits = find_schools(keyword, year=year, limit=5)
            if not hits:
                print(f"未找到学校「{keyword}」" + (f"（年份 {year}）" if year else ""))
                return 1
            if len(hits) > 1 and not year:
                names = [f"{h['school_name']}（{h['year']}，id={h['id']}）" for h in hits[:6]]
                print("匹配到多条学校记录，请加 --year 或指定学校 id：\n  " + "\n  ".join(names))
                return 1
            sid = hits[0]["id"]
        res = evaluate_school(sid)
        if "error" in res:
            print("评估失败：", res["error"])
            return 1
        print(render_markdown(res))
        if args.json_out:
            out = os.path.join(output_dir(), f"report_school_{sid}.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            print(f"\nJSON 已写出：{out}")
        return 0

    from scripts.indicator_engine import evaluate_teacher, load_yaml
    from packages.indicator_engine.calibration import attach_calibration
    from packages.agent.composer import render_markdown

    years = [y.strip() for y in args.years.split(",")]
    tpl = load_yaml("2025-2026_v1")
    if tpl is None:
        print("错误：评估模板不可用（PyYAML 未打包或模板文件缺失）")
        return 1
    tid = args.teacher or "T001"
    res = evaluate_teacher(tid, years, tpl)
    if "error" in res:
        print("评估失败：", res["error"])
        return 1
    attach_calibration(res, group_col="dept_id")

    print(render_markdown(res))
    if args.json_out:
        import json
        out = os.path.join(output_dir(), f"report_{tid}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print(f"\nJSON 已写出：{out}")
    return 0

def _get_lan_ip() -> str:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()

def _configure_session_proxy(proxy: str | None) -> None:
    pass
    if proxy is None:
        return
    value = str(proxy).strip()
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("--proxy 仅支持带主机名的 http/https 代理地址")

    os.environ["HTTP_PROXY"] = value
    os.environ["HTTPS_PROXY"] = value
    os.environ["ALL_PROXY"] = value
    existing = os.environ.get("NO_PROXY", "")
    no_proxy = [part.strip() for part in existing.split(",") if part.strip()]
    for host in ("127.0.0.1", "localhost", "::1"):
        if host not in no_proxy:
            no_proxy.append(host)
    os.environ["NO_PROXY"] = ",".join(no_proxy)

def cmd_serve(args):
    if getattr(args, "host", "127.0.0.1") != "127.0.0.1":
        print("本地凭据服务仅允许 --host 127.0.0.1；未启动。")
        return 2
    try:
        _configure_session_proxy(getattr(args, "proxy", None))
    except ValueError as exc:
        print(f"启动参数错误：{exc}")
        return 2

    try:
        import uvicorn
    except ImportError:
        print("未安装 uvicorn，无法启动 HTTP 服务。pip install uvicorn 后重试。")
        return 1

    import apps.api.main as api
    if not getattr(api, "FASTAPI_AVAILABLE", False):
        print("未安装 fastapi，无法启动 HTTP 服务。pip install fastapi 后重试。")
        return 1

    store_status = api.initialize_contract_store_status(force=True)
    if store_status.get("status") != "available":
        print("[warn] 契约状态库不可用；服务将以诊断降级模式继续启动，不会回退到其他状态库路径。")
        print(f"       error_code={store_status.get('error_code')}; path={store_status.get('preferred_path') or store_status.get('active_path') or '-'}")

    preferred = args.port
    port = _pick_port(preferred)
    if port != preferred:
        print(f"[warn] 端口 {preferred} 已被占用。")
        print(f"       本次改用 {port}。请关掉旧窗口后再刷新，否则浏览器可能仍打开旧页面。")

    host = "127.0.0.1"
    url = f"http://127.0.0.1:{port}/"
    wd = web_dir()
    print("=" * 60)
    print(f"教师评估 AI 平台 {UI_VERSION}")
    print(f"本机地址：{url}?ui={UI_VERSION}")
    print(f"Web 目录：{wd}")
    print("页签：对话台（Agent 工具调用绘图）/ 简介查询 / 模型密钥")
    print("仅本机访问；密钥由本人保存到 Windows 用户绑定加密存储。")
    print("Ctrl+C 退出。")
    print("=" * 60)
    if not args.no_browser:
        _open_browser(url)

    app = api.app
    try:
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.responses import Response

        class NoCacheIndex(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                resp = await call_next(request)
                path = request.url.path
                if path in ("/", "/index.html") or path.endswith(".html"):
                    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
                    resp.headers["Pragma"] = "no-cache"
                return resp

        app.add_middleware(NoCacheIndex)
    except Exception:
        pass

    if os.path.isdir(wd):
        try:
            from fastapi.responses import FileResponse
            index_path = os.path.join(wd, "index.html")

            @app.get("/", include_in_schema=False)
            def _index():
                return FileResponse(index_path, media_type="text/html; charset=utf-8",
                                    headers={"Cache-Control": "no-store, no-cache, must-revalidate"})

            @app.get("/index.html", include_in_schema=False)
            def _index_html():
                return FileResponse(index_path, media_type="text/html; charset=utf-8",
                                    headers={"Cache-Control": "no-store, no-cache, must-revalidate"})
        except Exception as e:
            print(f"[warn] Web 页面挂载失败（API 仍可用）：{e}")
    else:
        print(f"[warn] 未找到 Web 目录：{wd}")

    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0

def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(prog="TeacherEval", description="教师评估 AI 平台（单文件版）")
    sub = ap.add_subparsers(dest="cmd")

    p_eval = sub.add_parser("eval", help="命令行评估")
    p_eval.add_argument("--teacher", default=None, help="评估指定教师（如 T001）")
    p_eval.add_argument("--school", default=None, help="评估指定学校（如：--school 示例二小学 或 --school 1）")
    p_eval.add_argument("--year", default=None, help="指定学校年份（如 2020、2023）")
    p_eval.add_argument("--years", default="2023-2024,2024-2025", help="教师学年范围")
    p_eval.add_argument("--json-out", action="store_true")
    p_eval.set_defaults(func=cmd_eval)

    p_serve = sub.add_parser("serve", help="启动本地 API + Web")
    p_serve.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1"], help="仅本机监听")
    p_serve.add_argument("--port", type=int, default=8600)
    p_serve.add_argument("--no-browser", action="store_true")
    p_serve.add_argument(
        "--proxy",
        default=None,
        help="仅为本次服务进程设置 http/https 出站代理，不持久化用户配置",
    )
    p_serve.set_defaults(func=cmd_serve)

    args = ap.parse_args()
    if args.cmd is None:

        args.cmd = "serve"
        args.port = 8600
        args.no_browser = False
        args.proxy = None
        return cmd_serve(args)
    return args.func(args)

if __name__ == "__main__":
    sys.exit(main())
