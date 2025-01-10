from typing import Any, Dict, List, Optional

from casbin import Enforcer
from casbin.model import Model

"""
RBAC (Role-Based Access Control) Model with Context Support

This module defines a CASBIN RBAC model that:
1. Supports role inheritance through the role definition 'g'
2. Includes context for additional access control parameters
3. Handles database table-level permissions
4. Provides expression-based policy evaluation

Example Usage:
    # Initialize access control
    ac = AccessControl()
    
    # Define policies with context expressions
    ac.add_policy(
        role="*",  # Wildcard '*' means policy applies to all roles
        resource="posts",
        action="read",
        owner_field="owner_id"
    )  # All users can read their own posts
    
    ac.add_policy(
        role="admin",  # Specific role policy
        resource="users",
        action="read"
    )  # Only admins can read all users
    
    ac.add_policy(
        role="*",
        resource="posts",
        action="read",
        context={"org_id": "org1"}
    )  # All users can read posts in their org
    
    # Add role inheritance
    ac.add_role_inheritance("alice", "admin")  # Alice is an admin
    
    # Check permissions with context
    is_allowed = ac.enforce(
        subject="user123",
        resource="posts",
        action="read",
        subject_context={"org_id": "org1"},
        resource_context={"owner_id": "user123", "org_id": "org1"}
    )

Key Features:
- Flexible policy expressions using Python evaluation
- Support for owner-based access control
- Context-based permission checks
- Role inheritance
- Resource-level granularity
- Wildcard role support ('*') for policies that apply to all users

Role Specification:
- Use '*' to indicate a policy applies to all roles
- Use specific role names (e.g., 'admin') for role-specific policies
"""

# RBAC model with context support
DEFAULT_MODEL = """
[request_definition]
# sub: user, obj: resource, act: action, ctx: additional context
r = sub, res, act, sub_ctx, res_ctx

[policy_definition]
p = sub, res, act, expr

[role_definition]
g = _, _ 

[policy_effect]
# Grant access if any policy allows it
e = some(where (p.eft == allow))

[matchers]
m = ((p.sub == "*") || g(r.sub, p.sub)) && r.res == p.res && r.act == p.act && eval(p.expr)
"""


def build_policy_context_expression(
    owner_field: Optional[str], context: Any, filter: Any = None
) -> str:
    """
        Converts a context value into a CASBIN evaluation expression that compares policy and request contexts

        Examples:
            If it is none, it means that the policy is not attributed based
            None -> "True"

            If it is a list, it is a list of object attributes that are compared to the subject attributes
            ['ord_id'] -> "r.res_ctx['ord_id'] == r.sub_ctx['ord_id']"
            This will allow us to create policies for owner based access control
    .
            If it is a dict, it is a dict of object attributes that are compared to the objects attributes
            {"resource_type": "public"} -> "r.res_ctx['resource_type'] == 'public'"
    """
    if not context and not owner_field:
        return "True"

    conditions: list[str] = []

    # Special treatment for owner field to simplify the policy
    if owner_field:
        conditions.append(f"r.sub == r.res_ctx['{owner_field}']")

    # Handle string context directly
    if isinstance(context, str):
        context = [context]

    if isinstance(context, list):
        conditions.extend(
            (f"r.res_ctx['{attr}'] == r.sub_ctx['{attr}']" for attr in context)
        )

    # Handle dict context with dynamic attribute comparison
    if isinstance(context, dict):
        conditions.extend(
            (f"r.res_ctx['{key}'] == '{val}'" for key, val in context.items())
        )

    if conditions:
        return " && ".join(conditions)

    raise ValueError(f"Invalid context type: {type(context)}")


class AccessControl:
    def __init__(self, model: str = DEFAULT_MODEL, adapter: Any = None):
        """
        Initialize access control with model and optional policy adapter

        Args:
            model: Path to RBAC model file
            adapter: Policy storage adapter (e.g. FileAdapter, DatabaseAdapter)
        """
        model_obj = Model()
        model_obj.load_model_from_text(model)
        self.enforcer = Enforcer(model_obj, adapter) if adapter else Enforcer(model_obj)

    # TODO: Add support to add label to policy
    def add_policy(
        self,
        resource: str,
        action: str,
        role: Optional[str],
        owner_field: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Adds a policy rule to the enforcer

        Args:
            Role: Identifier of the role (e.g. admin, user, etc), default to 'default' if no role is specified.
            resource: Database table name
            action: Database operation (e.g., read, write, delete, create)
            context: Additional context parameters for access control
        """
        role = role or "*"
        context_expr = build_policy_context_expression(owner_field, context)
        self.enforcer.add_policy(role, resource, action, context_expr)

    def remove_policy(
        self,
        resource: str,
        action: str,
        role: str = "default",
        owner_field: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Remove a policy rule from the enforcer
        """
        context_expr = build_policy_context_expression(owner_field, context)
        self.enforcer.remove_policy(role, resource, action, context_expr)

    def add_role_inheritance(self, parent: str, child: str) -> None:
        """
        Add role inheritance to the enforcer
        """
        self.enforcer.add_grouping_policy(parent, child)

    def enforce(
        self,
        subject: str,
        resource: str,
        action: str,
        subject_context: Optional[Dict[str, Any]] = None,
        resource_context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Check if subject has permission to perform action on resource
        """
        return self.enforcer.enforce(
            subject, resource, action, subject_context, resource_context
        )

    def get_accessible_resources(
        self, subject: str, action: Optional[str] = None
    ) -> List[str]:
        """
        Get list of resources accessible to the subject

        Args:
            subject: User or role identifier
            action: Optional action to filter by

        Returns:
            List[str]: List of accessible resource names
        """
        subject_permissions = self.enforcer.get_implicit_permissions_for_user(subject)
        default_permissions = self.enforcer.get_implicit_permissions_for_user("default")
        permissions = [
            list(perm)
            for perm in set(
                tuple(perm) for perm in subject_permissions + default_permissions
            )
        ]
        resources = []
        for p in permissions:
            if action is None or p[2] == action:
                resources.append(p[1])
        return list(set(resources))
