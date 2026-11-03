"""M13 · frozen（PyInstaller）环境路径适配。

exe 运行时有两类资源：
  1. 只读内嵌资源（模板/演示数据/Web 页面）→ sys._MEIPASS（onefile 解包目录）
  2. 可写输出（评估结果 JSON、缓存）→ exe 所在目录（用户可见、可备份）
"""

import os
import sys


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def resource_root() -> str:
    """只读资源根目录：frozen 时为 _MEIPASS，源码运行时为仓库根。"""
    if is_frozen():
        return sys._MEIPASS
    # 源码模式：packages/core/frozen_paths.py → 上溯 3 层到仓库根
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def app_dir() -> str:
    """可写目录：frozen 时为 exe 所在目录；源码运行时为仓库根。"""
    if is_frozen():
        return os.path.dirname(sys.executable)
    return resource_root()


def data_dir() -> str:
    """演示数据目录（只读资源）。"""
    return os.path.join(resource_root(), "data", "demo")


def output_dir() -> str:
    """评估结果输出目录（可写）。"""
    p = os.path.join(app_dir(), "output")
    os.makedirs(p, exist_ok=True)
    return p


def charts_dir() -> str:
    """可写图表目录，支持隔离复测通过环境变量覆盖。"""
    configured = os.environ.get("TEACHER_EVAL_CHARTS_DIR", "").strip()
    p = configured or os.path.join(app_dir(), "output", "charts")
    os.makedirs(p, exist_ok=True)
    return p


def template_dir() -> str:
    return os.path.join(resource_root(), "evaluation_templates")


def web_dir() -> str:
    return os.path.join(resource_root(), "apps", "web")


def llm_config_path() -> str:
    """Encrypted container path only; never read or migrate legacy credentials."""
    return os.path.join(app_dir(), "output", "secure_config", "llm_profiles.dpapi")
