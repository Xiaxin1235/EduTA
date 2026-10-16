from enum import Enum

class Role(str, Enum):
    ADMIN = "admin"
    MANAGE = "manage"
    DEPT_LEADER = "教研员"
    TEACHER_SELF = "teacher_self"

_VIEW_ALLOWED = {
    Role.ADMIN: True,
    Role.MANAGE: True,
    Role.DEPT_LEADER: "dept_only",
    Role.TEACHER_SELF: "self_only",
}

_REDLINE_ALLOWED = {Role.ADMIN}

def _scope_get(user_scope, key):
    pass
    if isinstance(user_scope, dict):
        return user_scope.get(key)
    return user_scope

def can_view(user_role, target_teacher_id, user_scope=None):
    pass
    role = Role(user_role) if not isinstance(user_role, Role) else user_role
    rule = _VIEW_ALLOWED.get(role)
    if rule is None:
        return False
    if rule is True:
        return True
    if rule == "dept_only":

        target_dept = _scope_get(user_scope, "dept_id")
        return target_dept is not None and target_dept in str(target_teacher_id)
    if rule == "self_only":

        me = _scope_get(user_scope, "teacher_id")
        return me is not None and me == target_teacher_id
    return False

def can_view_redline(user_role):
    pass
    role = Role(user_role) if not isinstance(user_role, Role) else user_role
    return role in _REDLINE_ALLOWED

def can_export(user_role, target_teacher_id, user_scope=None, redline=False):
    pass
    if redline:

        return Role(user_role) == Role.ADMIN
    return can_view(user_role, target_teacher_id, user_scope)

def audit_entry(user, action, result_id=None, model_calls=None):
    pass
    return {
        "user_id": user.get("user_id"),
        "role": user.get("role"),
        "action": action,
        "target_scope": user.get("scope"),
        "result_id": result_id,
        "model_calls": model_calls,
    }

if __name__ == "__main__":

    print("teacher_self 看自己:", can_view("teacher_self", "T001", {"teacher_id": "T001"}))
    print("teacher_self 看别人:", can_view("teacher_self", "T002", {"teacher_id": "T001"}))
    print("admin 看任何人:", can_view("admin", "T999", None))
    print("教研员 看本组(语文组-T003):", can_view("教研员", "语文组-T003", {"dept_id": "语文组"}))
    print("教研员 看别组(数学组-T001):", can_view("教研员", "数学组-T001", {"dept_id": "语文组"}))
    print("红线明细 admin:", can_view_redline("admin"), " 教研员:", can_view_redline("教研员"))
