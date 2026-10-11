from __future__ import annotations

from casino.config.settings import Settings
from casino.errors import PermissionDeniedError


class PermissionService:
    def __init__(self, settings: Settings) -> None:
        self._developer_id = settings.developer_user_id
        self._admin_roles = settings.admin_role_id_set()

    def is_admin(self, user_id: int, role_ids: list[int]) -> bool:
        if self._developer_id is not None and user_id == self._developer_id:
            return True
        return any(role_id in self._admin_roles for role_id in role_ids)

    def require_admin(self, user_id: int, role_ids: list[int]) -> None:
        if not self.is_admin(user_id, role_ids):
            raise PermissionDeniedError("This command is restricted to the developer.")
