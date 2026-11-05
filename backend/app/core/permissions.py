"""The single source of truth for who may do what.

Routes declare the permission they need, and never check roles directly.
Mirrors the matrix in docs/architecture.md section 2.
"""

from enum import StrEnum


class Role(StrEnum):
    FINANCE_EXECUTIVE = "finance_executive"
    CO_FOUNDER = "co_founder"


class Permission(StrEnum):
    VIEW_WORKSPACE = "view_workspace"
    VIEW_CONFIG = "view_config"
    MANAGE_CONFIG = "manage_config"
    OPERATE_RECONCILIATION = "operate_reconciliation"  # upload, map, import, run
    MANAGE_CLAIMS = "manage_claims"
    VIEW_ALL_AUDIT = "view_all_audit"
    VIEW_OWN_AUDIT = "view_own_audit"


_READ_ACCESS = {Permission.VIEW_WORKSPACE, Permission.VIEW_CONFIG}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.FINANCE_EXECUTIVE: frozenset(
        _READ_ACCESS
        | {
            Permission.OPERATE_RECONCILIATION,
            Permission.MANAGE_CLAIMS,
            Permission.VIEW_OWN_AUDIT,
        }
    ),
    Role.CO_FOUNDER: frozenset(
        _READ_ACCESS
        | {
            Permission.MANAGE_CONFIG,
            Permission.VIEW_ALL_AUDIT,
        }
    ),
}


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]
