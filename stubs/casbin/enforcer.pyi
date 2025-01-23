from typing import Any, List, Optional, Union
from casbin.model import Model

class Enforcer:
    """The main interface for authorization enforcement and policy management."""
    
    def __init__(
        self,
        model: Union[str, Model],
        adapter: Optional[Any] = None,
        enable_log: bool = False
    ) -> None: ...

    def enforce(self, *rvals: Any) -> bool:
        """Enforces the policy on a request."""
        ...

    def add_policy(self, *params: str) -> bool:
        """Add a policy rule to the enforcer."""
        ...

    def remove_policy(self, *params: str) -> bool:
        """Remove a policy rule from the enforcer."""
        ...

    def add_grouping_policy(self, *params: str) -> bool:
        """Add a role inheritance rule."""
        ...

    def get_implicit_permissions_for_user(self, user: str) -> List[List[str]]:
        """Get implicit permissions for a user."""
        ...

    def get_implicit_roles_for_user(self, user: str) -> List[str]:
        """Get implicit roles for a user."""
        ...

    def load_model(self) -> None:
        """Reload the model from the model path."""
        ...

    def load_policy(self) -> None:
        """Reload the policy from the adapter."""
        ...

    def save_policy(self) -> bool:
        """Save the current policy to the adapter."""
        ...

    def enable_enforce(self, enabled: bool) -> None:
        """Enable or disable the enforcement of policies."""
        ...

    def enable_auto_save(self, auto_save: bool) -> None:
        """Enable or disable auto-save of policies."""
        ...

    def enable_auto_build_role_links(self, auto_build_role_links: bool) -> None:
        """Enable or disable auto-build of role links."""
        ... 