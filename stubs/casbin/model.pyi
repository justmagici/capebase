from typing import Any, Dict

class Model:
    """Represents the whole access control model."""
    
    def load_model_from_text(self, text: str) -> None:
        """Load the model from a string."""
        ...

    def add_def(self, sec: str, key: str, value: str) -> bool:
        """Add an assertion to the model."""
        ...

    def build_role_links(self, rm: Any) -> None:
        """Build the role inheritance links for the model."""
        ...

    def clear_policy(self) -> None:
        """Clear all policies in the model."""
        ...

    def get_model(self) -> Dict[str, Dict[str, Any]]:
        """Get all the sections of the model."""
        ...

    def has_section(self, sec: str) -> bool:
        """Check if the model has a section."""
        ...

    def load_model(self, path: str) -> None:
        """Load the model from a .conf file."""
        ...

    def load_model_from_file(self, path: str) -> None:
        """Load the model from a file."""
        ...

    def print_model(self) -> None:
        """Print the model to stdout."""
        ... 