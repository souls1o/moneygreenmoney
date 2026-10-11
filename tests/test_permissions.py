from types import SimpleNamespace

import pytest

from casino.errors import PermissionDeniedError
from casino.services.permission_service import PermissionService


def test_developer_user_id_is_admin() -> None:
    settings = SimpleNamespace(developer_user_id=1200, admin_role_id_set=lambda: frozenset({9}))
    permissions = PermissionService(settings)  # type: ignore[arg-type]
    assert permissions.is_admin(1200, [])
    assert not permissions.is_admin(1, [])
    assert permissions.is_admin(1, [9])


def test_require_admin_rejects_everyone_else() -> None:
    settings = SimpleNamespace(developer_user_id=1200, admin_role_id_set=lambda: frozenset())
    permissions = PermissionService(settings)  # type: ignore[arg-type]
    with pytest.raises(PermissionDeniedError):
        permissions.require_admin(1, [])
