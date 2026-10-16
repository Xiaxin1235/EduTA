import os
import sys

def is_frozen() -> bool:
    return getattr(sys, "frozen", False)

def resource_root() -> str:
    pass
    if is_frozen():
        return sys._MEIPASS

    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def app_dir() -> str:
    pass
    if is_frozen():
        return os.path.dirname(sys.executable)
    return resource_root()

def data_dir() -> str:
    pass
    return os.path.join(resource_root(), "data", "demo")

def output_dir() -> str:
    pass
    p = os.path.join(app_dir(), "output")
    os.makedirs(p, exist_ok=True)
    return p

def charts_dir() -> str:
    pass
    configured = os.environ.get("TEACHER_EVAL_CHARTS_DIR", "").strip()
    p = configured or os.path.join(app_dir(), "output", "charts")
    os.makedirs(p, exist_ok=True)
    return p

def template_dir() -> str:
    return os.path.join(resource_root(), "evaluation_templates")

def web_dir() -> str:
    return os.path.join(resource_root(), "apps", "web")

def llm_config_path() -> str:
    pass
    return os.path.join(app_dir(), "output", "secure_config", "llm_profiles.dpapi")
