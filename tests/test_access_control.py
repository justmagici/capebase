import pytest

from cape.auth.access_control import AccessControl


@pytest.fixture
def ac():
    """Create a fresh AccessControl instance for each test"""
    return AccessControl()


def test_basic_policy_enforcement(ac):
    # Add basic policies
    ac.add_policy(role="admin", resource="posts", action="read")
    ac.add_policy(role=None, resource="comments", action="write")

    ac.add_role_inheritance("user1", "admin")

    # Test basic enforcement
    assert ac.enforce("admin", "posts", "read")
    assert ac.enforce("admin", "comments", "write")

    assert ac.enforce("user1", "posts", "read")
    assert ac.enforce("user1", "comments", "write")

    assert ac.enforce("user2", "comments", "write")
    assert not ac.enforce("user2", "posts", "read")


def test_context_based_policies_with_list(ac):
    # Add policies with different contexts
    ac.add_policy(
        role="editor", resource="articles", action="edit", context=["department"]
    )
    ac.add_policy(
        role="reviewer", resource="articles", action="read", context=["department"]
    )

    # Test with matching contexts
    assert ac.enforce(
        role="editor",
        resource="articles",
        action="edit",
        subject_context={"department": "editorial"},
        resource_context={"department": "editorial"},
    )
    assert ac.enforce(
        role="reviewer",
        resource="articles",
        action="read",
        subject_context={"department": "review"},
        resource_context={"department": "review"},
    )

    # Test with non-matching contexts
    assert not ac.enforce(
        "editor",
        "articles",
        "edit",
        {"department": "marketing"},
        {"department": "editorial"},
    )
    assert not ac.enforce(
        "reviewer",
        "articles",
        "read",
        {"department": "wrong"},
        {"department": "review"},
    )


def test_context_based_policies(ac):
    # Add policies with different contexts
    ac.add_policy(
        role="editor",
        resource="articles",
        action="edit",
        context={"department": "editorial"},
    )
    ac.add_policy(
        role="reviewer",
        resource="articles",
        action="read",
        context={"department": "review"},
    )

    # Test with matching contexts
    assert ac.enforce("editor", "articles", "edit", {}, {"department": "editorial"})
    assert ac.enforce("reviewer", "articles", "read", {}, {"department": "review"})

    # Test with non-matching contexts
    assert not ac.enforce("editor", "articles", "edit", {}, {"department": "marketing"})
    assert not ac.enforce("reviewer", "articles", "read", {}, {"department": "wrong"})


def test_role_inheritance(ac):
    # Set up role hierarchy
    ac.add_role_inheritance("admin", "editor")
    ac.add_role_inheritance("editor", "viewer")

    # Add base policy for viewer
    ac.add_policy(role="viewer", resource="documents", action="read")
    ac.add_policy(role="editor", resource="documents", action="edit")
    ac.add_policy(role="admin", resource="documents", action="delete")

    # Test role inheritance
    assert ac.enforce("admin", "documents", "read")  # inherited from viewer
    assert ac.enforce("admin", "documents", "edit")  # inherited from editor
    assert ac.enforce("admin", "documents", "delete")  # direct permission

    assert ac.enforce("editor", "documents", "read")  # inherited from viewer
    assert ac.enforce("editor", "documents", "edit")  # direct permission
    assert not ac.enforce("editor", "documents", "delete")  # no permission

    assert ac.enforce("viewer", "documents", "read")  # direct permission
    assert not ac.enforce("viewer", "documents", "edit")  # no permission


def test_policy_removal(ac):
    # Add and then remove policies
    ac.add_policy(role="user", resource="resource", action="action")
    assert ac.enforce("user", "resource", "action")

    ac.remove_policy(role="user", resource="resource", action="action")
    # Check that the policy is removed
    assert not ac.enforcer.get_policy()


def test_multiple_policies_same_user(ac):
    # Add multiple policies for the same user
    ac.add_policy(role="user", resource="docs", action="read")
    ac.add_policy(
        role="user", resource="docs", action="write", context={"project": "alpha"}
    )
    ac.add_policy(
        role="user", resource="docs", action="delete", context={"level": "admin"}
    )

    # Test different permissions
    assert ac.enforce("user", "docs", "read")
    assert ac.enforce("user", "docs", "write", {}, {"project": "alpha"})
    assert not ac.enforce("user", "docs", "write", {}, {"project": "beta"})
    assert ac.enforce("user", "docs", "delete", {}, {"level": "admin"})
    assert not ac.enforce("user", "docs", "delete", {}, {"level": "user"})


def test_invalid_inputs(ac):
    # Test with invalid inputs
    with pytest.raises(Exception):
        ac.enforce(None, "resource", "action")

    with pytest.raises(Exception):
        ac.add_policy(None, "resource", "action", "true")

    # Test with empty strings
    assert ac.enforce("", "resource", "action") is False

    # Test with non-existent policies
    assert ac.enforce("nonexistent", "resource", "action") is False


def test_context_variations(ac):
    # Test different context formats
    ac.add_policy(role="user", resource="resource", action="action")
    ac.add_policy(
        role="user", resource="resource2", action="action", context={"key": "value"}
    )
    ac.add_policy(
        role="user", resource="resource3", action="action", context=["context"]
    )

    # Test with different context scenarios
    assert ac.enforce("user", "resource", "action")
    assert ac.enforce("user", "resource2", "action", {}, {"key": "value"})
    assert ac.enforce(
        "user", "resource3", "action", {"context": "value"}, {"context": "value"}
    )
