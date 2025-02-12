from fastapi import HTTPException
from starlette import status


class PermissionDeniedError(HTTPException):
    """Raised when a user attempts an action they don't have permission for."""

    def __init__(self, detail: str = "Permission denied"):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


class SystemManagedFieldViolation(Exception):
    """Raised when attempting to modify system-managed fields"""

    def __init__(self, field_name: str):
        self.field_name = field_name
        super().__init__(f"Field '{field_name}' is managed by the system")


class SystemManagedFieldRequired(Exception):
    """Raised when attempting to modify system-managed fields"""

    def __init__(self, field_name: str):
        self.field_name = field_name
        super().__init__(f"Field '{field_name}' is required but not provided")
