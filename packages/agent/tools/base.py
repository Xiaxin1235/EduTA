# -*- coding: utf-8 -*-
"""Agent 工具基础抽象规范与数据结构。

遵循类似 OpenAI / Claude 的 Function / Tool Calling 规范：
- 每种工具具有唯一的名称 (name)、功能说明 (description)、参数格式规范 (parameters，符合 JSON Schema)
- 具备统一的 execute(**kwargs) 执行接口与标准化的 ToolResult 返回载荷
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolResult:
    """工具单次调用的标准化执行结果。"""
    tool_name: str
    success: bool = True
    stdout: str = ""
    stderr: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)  # 图表、文件等生成物
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
    """所有 Agent 工具的抽象基类。"""

    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {}

    def to_schema(self) -> Dict[str, Any]:
        """输出标准 OpenAI / Claude 兼容的 Function Calling JSON Schema。"""
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
        """运行工具并自动计算耗时与异常兜底。"""
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
        """执行工具核心逻辑，子类必须实现。"""
        pass
