# -*- coding: utf-8 -*-
"""ToolRegistry · Agent 模块化工具注册中心与分发管理器。

管理系统中所有可用工具的注册、查找、Schema 导出与执行调度。
"""

from typing import Any, Dict, List, Optional
from packages.agent.tools.base import BaseTool, ToolResult
from packages.agent.tools.python_runner import PythonInterpreterTool


class ToolRegistry:
    """工具注册与分发管理器。"""

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册工具实例。"""
        if not tool.name:
            raise ValueError("Tool name cannot be empty")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[BaseTool]:
        """按名称查找工具。"""
        return self._tools.get(name)

    def list_tools(self) -> List[BaseTool]:
        """返回所有已注册工具列表。"""
        return list(self._tools.values())

    def get_schemas(self) -> List[Dict[str, Any]]:
        """导出供大模型 Function Calling 使用的标准 JSON Schema 列表。"""
        return [tool.to_schema() for tool in self._tools.values()]

    def call(self, name: str, **kwargs) -> ToolResult:
        """根据名称调用工具并返回 ToolResult。"""
        tool = self.get(name)
        if not tool:
            return ToolResult(
                tool_name=name,
                success=False,
                error=f"工具 '{name}' 未在系统注册中找到。当前可用工具: {list(self._tools.keys())}",
            )
        return tool.run(**kwargs)


# 默认单例注册中心并预装核心工具
default_registry = ToolRegistry()
default_registry.register(PythonInterpreterTool())
from packages.agent.tools.regional_capability import RegionalCapabilityTool
default_registry.register(RegionalCapabilityTool())


def get_default_registry() -> ToolRegistry:
    return default_registry
