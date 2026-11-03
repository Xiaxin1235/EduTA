# -*- coding: utf-8 -*-
"""PythonInterpreterTool · 本地 Python 数据分析与绘图执行工具。

功能特性：
1. 类似市面上现代 Agent（Code Interpreter / Advanced Data Analysis）：
   - 支持动态执行 Python 代码，自动捕获标准输出与绘图生成物；
2. 绘图能力专精（Matplotlib / Seaborn / Pandas）：
   - 自动预配置中文字体（Microsoft YaHei, SimHei, DengXian），彻底杜绝方块乱码 (□□□)；
   - 注入契合前端 Claude 温暖极简美学的统一配色与画布样式；
   - 强制使用无头模式 ('Agg')，绝不弹出阻塞窗口；
3. 输出标准化：
   - 自动抓取当前所有活动 Figure，输出高分辨率 PNG；
   - 双通道交付：本地文件 (output/charts/<id>.png) + 内联 Base64 Data URI（保证内网穿透/离线秒开）；
   - 详细记录执行耗时、标准输出与工具调用轨迹。
"""

import base64
import contextlib
import io
import os
import re
import sys
import time
import traceback
import uuid
from typing import Any, Dict, Optional

from packages.agent.tools.base import BaseTool, ToolResult
from packages.core.frozen_paths import charts_dir


def _charts_dir() -> str:
    """持久化图表存放目录：<app_dir>/output/charts/"""
    return charts_dir()


_PYTHON_SITE_PACKAGES_RE = re.compile(
    r"(?i)(?:^|[\\/])python(?P<major>\d)(?:[._-]?(?P<minor>\d{1,2}))?"
    r"[\\/](?:lib[\\/])?site-packages(?:[\\/]|$)"
)


def _site_packages_runtime_tag(path: str) -> Optional[str]:
    """Return an explicit Python runtime tag embedded in a site-packages path."""
    match = _PYTHON_SITE_PACKAGES_RE.search(str(path or ""))
    if not match or not match.group("minor"):
        return None
    return f"Python{match.group('major')}{match.group('minor')}"


@contextlib.contextmanager
def _compatible_scientific_import_path():
    """Temporarily exclude site-packages built for another Python runtime.

    Some Windows launchers append both Python 3.10 and 3.12 user package
    directories.  NumPy may then resolve a package and binary extension from
    different runtimes.  Keep frozen bundle paths and unversioned paths, and
    expose only non-sensitive runtime tags for verification evidence.
    """
    original = list(sys.path)
    current_tag = f"Python{sys.version_info.major}{sys.version_info.minor}"
    filtered = []
    excluded_tags = []
    for entry in original:
        tag = _site_packages_runtime_tag(entry)
        if tag and tag.casefold() != current_tag.casefold():
            excluded_tags.append(tag)
            continue
        filtered.append(entry)
    sys.path[:] = filtered
    try:
        yield sorted(set(excluded_tags))
    finally:
        sys.path[:] = original


def _is_meaningful_figure(fig) -> bool:
    """检查 Figure 是否包含有效的绘图数据（防止捕获空画布或仅有刻度线的幽灵 axes）。"""
    if not getattr(fig, "axes", None):
        return False
    for ax in fig.axes:
        if len(getattr(ax, "lines", [])) > 0:
            return True
        if len(getattr(ax, "patches", [])) > 0:
            return True
        if len(getattr(ax, "collections", [])) > 0:
            return True
        if len(getattr(ax, "images", [])) > 0:
            return True
        if len(getattr(ax, "tables", [])) > 0:
            return True
    return False


class PythonInterpreterTool(BaseTool):
    """本地 Python 代码执行与绘图工具。"""

    name: str = "python_interpreter"
    description: str = (
        "本地 Python 解释器与作图工具。可执行包含 matplotlib、pandas、numpy 的数据分析与作图代码，"
        "自动捕获生成的图表并保存为高清图片，返回图表访问路径与 Base64 编码，用于在报告中直观呈现。"
    )
    parameters: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "要执行的完整 Python 代码。作图需使用 matplotlib.pyplot，无需额外配置字体和后端。",
            },
            "chart_title": {
                "type": "string",
                "description": "可选的图表意图或说明标签，如「学校五维能力雷达图」",
            },
        },
        "required": ["code"],
    }

    def execute(self, code: str, chart_title: Optional[str] = None, **kwargs) -> ToolResult:
        t0 = time.time()
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        artifacts = []
        plt = None
        np = None
        pd = None
        matplotlib = None
        plotting_error = None
        filtered_runtime_tags = []
        failure_stage = None
        failure_type = None
        failure_code = None
        requires_plotting = bool(chart_title) or bool(
            re.search(r"\b(?:matplotlib|pyplot|numpy|plt\.|np\.)", code)
        )

        # 1. 预设安全与便利的执行环境
        env_globals: Dict[str, Any] = {
            "__name__": "__main__",
            "__builtins__": __builtins__,
        }

        # 图表生命周期必须始终使用同一组兼容路径。Matplotlib/NumPy 会在
        # 执行绘图和 savefig 时延迟导入子模块；如果只隔离首次 import，恢复
        # 污染后的 sys.path 后仍可能混入另一 Python ABI 的二进制扩展。
        with _compatible_scientific_import_path() as filtered_runtime_tags:
            # 绘图栈与可选 pandas 分开加载，避免 pandas 的非关键故障拖垮 Matplotlib。
            try:
                import numpy as np
                import matplotlib
                matplotlib.use("Agg")  # 强制无头后台渲染
                import matplotlib.pyplot as plt
                import matplotlib.font_manager as fm

                # 配置中文字体优先级
                plt.rcParams["font.sans-serif"] = [
                    "Microsoft YaHei", "SimHei", "DengXian", "PingFang SC", "Hiragino Sans GB", "sans-serif"
                ]
                plt.rcParams["axes.unicode_minus"] = False

                # 配置美学风格：柔和网格、无杂边框、清晰字重
                plt.rcParams["figure.facecolor"] = "#ffffff"
                plt.rcParams["axes.facecolor"] = "#fdfcfb"
                plt.rcParams["axes.edgecolor"] = "#e8e6df"
                plt.rcParams["axes.linewidth"] = 0.8
                plt.rcParams["grid.color"] = "#f0eee8"
                plt.rcParams["grid.linestyle"] = "--"
                plt.rcParams["grid.alpha"] = 0.7
                plt.rcParams["text.color"] = "#201f1d"
                plt.rcParams["axes.labelcolor"] = "#201f1d"
                plt.rcParams["xtick.color"] = "#6f6c65"
                plt.rcParams["ytick.color"] = "#6f6c65"

                env_globals.update({
                    "np": np,
                    "plt": plt,
                    "matplotlib": matplotlib,
                })
                plt.close("all")  # 清空旧状态
            except Exception as exc:
                plotting_error = exc
                if requires_plotting:
                    failure_stage = "scientific_import"
                    failure_type = type(exc).__name__
                    failure_code = "scientific_plot_runtime_unavailable"
                    stderr_buf.write(
                        f"[error] 绘图运行时不可用: {type(exc).__name__}\n"
                    )
                else:
                    stderr_buf.write(
                        f"[warn] 可选绘图运行时未加载: {type(exc).__name__}\n"
                    )

            if plotting_error is None:
                try:
                    import pandas as pd
                    env_globals["pd"] = pd
                except Exception as exc:
                    stderr_buf.write(
                        f"[warn] 可选 pandas 未加载: {type(exc).__name__}\n"
                    )

            # 2. 捕获重定向 stdout/stderr 并执行代码。仍处于同一兼容路径
            # 上下文中，保证生成代码里的重复 import 与延迟 import 不漂移。
            exec_err = None
            with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                if plotting_error is not None and requires_plotting:
                    exec_err = RuntimeError("scientific_plot_runtime_unavailable")
                else:
                    try:
                        clean_code = code.strip()
                        exec(clean_code, env_globals)
                    except Exception as exc:
                        exec_err = exc
                        failure_stage = "chart_code_execution"
                        failure_type = type(exc).__name__
                        failure_code = "chart_code_execution_failed"
                        traceback.print_exc(file=stderr_buf)

            # 3. 收集并抽取 Matplotlib 生成的所有图表。同一隔离持续到
            # savefig 与 Base64 编码完成，避免导出阶段再次加载错误 ABI。
            charts_saved = 0
            try:
                fig_nums = plt.get_fignums() if plt is not None else []
                if fig_nums:
                    out_dir = _charts_dir()
                    valid_figs = []
                    for fnum in fig_nums:
                        fig = plt.figure(fnum)
                        if _is_meaningful_figure(fig):
                            valid_figs.append((fnum, fig))

                    targets = valid_figs if valid_figs else [
                        (fnum, plt.figure(fnum)) for fnum in fig_nums
                    ]

                    for fnum, fig in targets:
                        chart_id = f"chart_{uuid.uuid4().hex[:10]}"
                        filename = f"{chart_id}.png"
                        filepath = os.path.join(out_dir, filename)
                        fig.savefig(
                            filepath,
                            dpi=160,
                            bbox_inches="tight",
                            facecolor=fig.get_facecolor(),
                            edgecolor="none",
                        )

                        img_buf = io.BytesIO()
                        fig.savefig(
                            img_buf,
                            format="png",
                            dpi=160,
                            bbox_inches="tight",
                            facecolor=fig.get_facecolor(),
                            edgecolor="none",
                        )
                        img_buf.seek(0)
                        b64_str = base64.b64encode(img_buf.getvalue()).decode("ascii")
                        data_uri = f"data:image/png;base64,{b64_str}"
                        import hashlib
                        from packages.agent.tools.chart_scope import capture_figure_scene
                        rendered_scene = capture_figure_scene(fig)
                        rendered_scene["png_sha256"] = hashlib.sha256(img_buf.getvalue()).hexdigest()

                        artifacts.append({
                            "id": chart_id,
                            "title": chart_title or f"图表 #{fnum}",
                            "type": "image/png",
                            "filename": filename,
                            "file_path": filepath,
                            "url": f"/api/v1/charts/{filename}",
                            "base64": data_uri,
                            "dpi": 160,
                            "rendered_scene": rendered_scene,
                        })
                        charts_saved += 1
            except Exception as exc:
                if exec_err is None:
                    exec_err = RuntimeError("chart_artifact_export_failed")
                    failure_stage = "chart_artifact_export"
                    failure_type = type(exc).__name__
                    failure_code = "chart_artifact_export_failed"
                stderr_buf.write(
                    f"[error] 图表产物导出失败: {type(exc).__name__}\n"
                )
            finally:
                if plt is not None:
                    plt.close("all")

        out_str = stdout_buf.getvalue()
        err_str = stderr_buf.getvalue()
        duration = (time.time() - t0) * 1000.0

        if charts_saved > 0:
            if not out_str:
                out_str = f"已成功生成 {charts_saved} 张高清图表（Matplotlib 160 DPI）。"
            else:
                out_str += f"\n[Agent] 已成功生成 {charts_saved} 张图表。"

        if chart_title and exec_err is None and charts_saved == 0:
            exec_err = RuntimeError("chart_artifact_missing")
            failure_stage = "chart_artifact_validation"
            failure_type = "MissingArtifact"
            failure_code = "chart_artifact_missing"

        success = (exec_err is None)
        return ToolResult(
            tool_name=self.name,
            success=success,
            stdout=out_str.strip(),
            stderr=err_str.strip(),
            data={
                "code": code,
                "chart_title": chart_title,
                "charts_count": len(artifacts),
                "runtime": {
                    "python": f"{sys.version_info.major}.{sys.version_info.minor}",
                    "plotting_required": requires_plotting,
                    "filtered_incompatible_site_packages": filtered_runtime_tags,
                    "plotting_stack_ready": plotting_error is None,
                    "numpy_version": getattr(np, "__version__", None),
                    "matplotlib_version": getattr(matplotlib, "__version__", None),
                    "pandas_version": getattr(pd, "__version__", None),
                    "scientific_path_isolated_for_lifecycle": True,
                    "failure_stage": failure_stage,
                    "failure_type": failure_type,
                    "failure_code": failure_code,
                },
            },
            artifacts=artifacts,
            error=str(exec_err) if exec_err else None,
            duration_ms=duration,
        )
