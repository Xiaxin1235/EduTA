"""M11 · 权限与授权逻辑（桩实现，真实用户表由部署方提供）。

角色矩阵：
  admin       系统管理员 —— 全校数据 + 导出 + 红线明细
  manage      教务管理员 —— 全校数据（不含红线明细）
  教研员       按教研组范围（dept）—— 本组数据
  teacher_self 教师本人   —— 仅本人数据（评估报告，不含红线明细）

红线数据（complaint/discipline）最严格：默认仅 admin 可看明细。
"""

from enum import Enum


class Role(str, Enum):
    ADMIN = "admin"
    MANAGE = "manage"
    DEPT_LEADER = "教研员"   # 教研组长
    TEACHER_SELF = "teacher_self"


# 各角色的数据范围能力（简化 P 策略）
_VIEW_ALLOWED = {
    Role.ADMIN: True,
    Role.MANAGE: True,
    Role.DEPT_LEADER: "dept_only",
    Role.TEACHER_SELF: "self_only",
}

_REDLINE_ALLOWED = {Role.ADMIN}  # 红线明细仅 admin


def _scope_get(user_scope, key):
    """兼容两种 scope：dict（结构化）与 str（直接值——self_only 时是 teacher_id，
    教研员时是 dept_id）。"""
    if isinstance(user_scope, dict):
        return user_scope.get(key)
    return user_scope  # 字符串：key 语义由调用方决定


def can_view(user_role, target_teacher_id, user_scope=None):
    """判断某角色能否查看指定教师的数据。

    user_scope 两种形态：
      dict  —— {dept_id: "语文组"} / {teacher_id: "T001"}
      str   —— "T001"（teacher_self 的本人 ID）或 "语文组"（教研员的组）
    返回 bool。
    """
    role = Role(user_role) if not isinstance(user_role, Role) else user_role
    rule = _VIEW_ALLOWED.get(role)
    if rule is None:
        return False
    if rule is True:
        return True
    if rule == "dept_only":
        # 教研员：仅本组（原型按 dept_id 前缀/相等匹配；真实部署走用户表 dept 字段）
        target_dept = _scope_get(user_scope, "dept_id")
        return target_dept is not None and target_dept in str(target_teacher_id)
    if rule == "self_only":
        # 教师本人：只能看自己的 teacher_id
        me = _scope_get(user_scope, "teacher_id")
        return me is not None and me == target_teacher_id
    return False


def can_view_redline(user_role):
    """红线明细访问：仅 admin。"""
    role = Role(user_role) if not isinstance(user_role, Role) else user_role
    return role in _REDLINE_ALLOWED


def can_export(user_role, target_teacher_id, user_scope=None, redline=False):
    """导出控制：admin/manage 可导出；涉及红线（或红线标记报告）需二次授权。

    原型：把"二次授权"建模为一项能力位——未授权时 admin 可导，manage 导出含红线
    报告需额外 authorized=True；教师本人不可导出他人数据。
    """
    if redline:
        # 含红线明细的导出：仅 admin（且需二次授权位，原型简化成 allow）
        return Role(user_role) == Role.ADMIN
    return can_view(user_role, target_teacher_id, user_scope)


def audit_entry(user, action, result_id=None, model_calls=None):
    """审计日志条目构造（写入接入方；此处返回结构化 dict 便于持久化）。"""
    return {
        "user_id": user.get("user_id"),
        "role": user.get("role"),
        "action": action,           # view / evaluate / export / model_call
        "target_scope": user.get("scope"),
        "result_id": result_id,
        "model_calls": model_calls,
    }


if __name__ == "__main__":
    # 冒烟（目标 ID 带 dept 前缀演示教研员范围匹配）
    print("teacher_self 看自己:", can_view("teacher_self", "T001", {"teacher_id": "T001"}))
    print("teacher_self 看别人:", can_view("teacher_self", "T002", {"teacher_id": "T001"}))
    print("admin 看任何人:", can_view("admin", "T999", None))
    print("教研员 看本组(语文组-T003):", can_view("教研员", "语文组-T003", {"dept_id": "语文组"}))
    print("教研员 看别组(数学组-T001):", can_view("教研员", "数学组-T001", {"dept_id": "语文组"}))
    print("红线明细 admin:", can_view_redline("admin"), " 教研员:", can_view_redline("教研员"))