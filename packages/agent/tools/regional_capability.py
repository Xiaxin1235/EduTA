"""One registered composite tool, backed by the common fixed region SQL tools."""
from packages.agent.tools.base import BaseTool, ToolResult


class RegionalCapabilityTool(BaseTool):
    name = 'regional_capability'
    description = '执行已校验的省级/全国、位置、开放评估、多地区和多年类型化任务单。'
    parameters = {'type': 'object', 'properties': {'routed': {'type': 'object'}}, 'required': ['routed']}

    def execute(self, routed, connection=None, verify_execution=False):
        from packages.semantic_query.public_tasks import _execute
        return ToolResult(tool_name=self.name, data=_execute(routed, connection=connection, verify_execution=verify_execution))
