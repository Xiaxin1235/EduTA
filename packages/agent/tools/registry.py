from typing import Any, Dict, List, Optional
from packages.agent.tools.base import BaseTool, ToolResult
from packages.agent.tools.python_runner import PythonInterpreterTool

class ToolRegistry:
    pass

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        pass
        if not tool.name:
            raise ValueError("Tool name cannot be empty")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[BaseTool]:
        pass
        return self._tools.get(name)

    def list_tools(self) -> List[BaseTool]:
        pass
        return list(self._tools.values())

    def get_schemas(self) -> List[Dict[str, Any]]:
        pass
        return [tool.to_schema() for tool in self._tools.values()]

    def call(self, name: str, **kwargs) -> ToolResult:
        pass
        tool = self.get(name)
        if not tool:
            return ToolResult(
                tool_name=name,
                success=False,
                error=f"工具 '{name}' 未在系统注册中找到。当前可用工具: {list(self._tools.keys())}",
            )
        return tool.run(**kwargs)

default_registry = ToolRegistry()
default_registry.register(PythonInterpreterTool())

def get_default_registry() -> ToolRegistry:
    return default_registry
