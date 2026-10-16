import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class ToolResult:
    pass
    tool_name: str
    success: bool = True
    stdout: str = ""
    stderr: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    duration_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool_name,
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "data": self.data,
            "artifacts": self.artifacts,
            "error": self.error,
            "duration_ms": round(self.duration_ms, 2),
        }

class BaseTool(ABC):
    pass

    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {}

    def to_schema(self) -> Dict[str, Any]:
        pass
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }

    def run(self, **kwargs) -> ToolResult:
        pass
        t0 = time.perf_counter()
        try:
            res = self.execute(**kwargs)
            res.duration_ms = (time.perf_counter() - t0) * 1000.0
            return res
        except Exception as e:
            return ToolResult(
                tool_name=self.name,
                success=False,
                error=str(e),
                duration_ms=(time.perf_counter() - t0) * 1000.0,
            )

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        pass
        pass
