import logging
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property
from typing import (
    Annotated,
    Any,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Delete, Insert, Select, Update, and_, insert, or_
from sqlalchemy.orm import Query
from sqlalchemy.sql.elements import BindParameter, ClauseElement
from sqlmodel import SQLModel
from sqlalchemy.sql.elements import TextClause

from capebase.auth.access_control import AccessControl
from capebase.exceptions import SystemManagedFieldRequired, SystemManagedFieldViolation
from capebase.models import AuthContext, AuthField

WILDCARD = "*"

logger = logging.getLogger(__name__)


class RLSConfig(BaseModel):
    """Configuration for Row-Level Security on a SQL Model"""

    model_config = ConfigDict(extra="forbid", strict=True)

    model: Type[SQLModel]
    action: str = Field(description="Single action (e.g. read, create, update, delete)")
    role: Optional[str] = Field(
        default=WILDCARD,
        description="Role of the user, or wildcard '*' to apply to all users",
    )
    owner_field: Optional[str] = Field(
        default=None, description="Field representing the owner of the model"
    )
    context_fields: Optional[Union[List[str], Dict[str, Any]]] = Field(
        default=None, description="Fields to include in context"
    )

    @cached_property
    def get_system_managed_fields(self) -> List[Tuple[str, AuthField]]:
        """Get the set of system-managed fields by inspecting model annotations for AuthField.

        Returns:
            List[Tuple[str, AuthField]]: List of tuples containing (field_name, auth_field) pairs for fields managed by the auth system
        """
        system_fields: List[Tuple[str, AuthField]] = list()
        type_hints = get_type_hints(self.model, include_extras=True)

        for field_name, field_type in type_hints.items():
            if hasattr(field_type, "__metadata__"):
                for metadata in field_type.__metadata__:
                    if isinstance(metadata, AuthField):
                        system_fields.append((field_name, metadata))
                        break

        return system_fields


def extract_tables(selectable):
    """
    Recursively extracts underlying table objects from a selectable.
    If the selectable has a `name` attribute, it's assumed to be a table or alias.
    Otherwise, if it's a join, extract from its left and right sides.
    """
    if hasattr(selectable, "name"):
        return [selectable]
    tables = []
    if hasattr(selectable, "left"):
        tables.extend(extract_tables(selectable.left))
    if hasattr(selectable, "right"):
        tables.extend(extract_tables(selectable.right))
    return tables


def get_from_auth_id_key(model: Type[SQLModel]) -> Optional[str]:
    """Get the FROM_AUTH_ID key from the type hints"""
    type_hints = get_type_hints(model, include_extras=True)
    for field_name, field_type in type_hints.items():
        if get_origin(field_type) is Annotated:
            for arg in get_args(field_type)[1:]:
                if isinstance(arg, AuthField) and arg.source == "id":
                    return field_name

    return None


def get_table_name(model: Union[Type[SQLModel], SQLModel]) -> str:
    """Get the table name of a SQLModel"""
    return str(model.__tablename__)


@dataclass
class RowLevelSecurity:
    access_control: AccessControl
    model_configs: Dict[str, List[RLSConfig]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def register_model(self, config: RLSConfig):
        """Register a model for RLS with its configuration"""
        self.model_configs[get_table_name(config.model)].append(config)

        self.access_control.add_policy(
            role=config.role,
            resource=get_table_name(config.model),
            owner_field=config.owner_field,
            action=config.action,
            context=config.context_fields,
        )

    def _build_resource_context(
        self, obj: SQLModel, config: Optional[RLSConfig] = None
    ) -> Dict[str, Any]:
        """
        Build resource context from object and configuration

        Args:
            obj: SQLModel instance to build context from
            config: Optional RLSConfig to use for context fields. If None, uses all configs for the model.

        Returns:
            Dict containing resource context
        """
        resource_context = {}

        # Get configs for this model
        configs = [config] if config else self.model_configs[get_table_name(obj)]

        # Add context fields from configs
        for cfg in configs:
            if cfg.context_fields:
                for field in cfg.context_fields:
                    resource_context[field] = getattr(obj, field)

            # Add owner field if present
            if cfg.owner_field and cfg.owner_field not in resource_context:
                resource_context[cfg.owner_field] = getattr(obj, cfg.owner_field)

        return resource_context

    def _can_perform_action(
        self,
        auth_context: AuthContext,
        obj: SQLModel,
        action: str,
    ) -> bool:
        """Base method to check if user can perform an action on an object"""
        resource_context = self._build_resource_context(obj)
        subject_context = dict(auth_context.context)

        # Get first config for this model
        configs = self.model_configs[get_table_name(obj)]
        if not configs:
            return True

        config = configs[0]

        # Add subject context for fields that require it
        for field_name, auth_field in config.get_system_managed_fields:
            if auth_field.source == "id" and field_name not in subject_context:
                subject_context[field_name] = auth_context.id

        # TODO: Handle wildcard / default role properly
        return self.access_control.enforce(
            role=auth_context.role or "*",
            resource=get_table_name(obj),
            action=action,
            subject_context=subject_context,
            resource_context=resource_context,
        )

    def _get_object_from_insert_statement(self, statement: Insert) -> List[SQLModel]:
        """Helper function to get objects from an insert statement.

        Returns:
            List[SQLModel]: A list of model instances built from the statement's values.
        """
        table_name = statement.table.name
        model_class = self.model_configs[table_name][0].model
        objects = []

        # Handle single insert
        if hasattr(statement, "_values") and statement._values is not None:
            compiled = statement.compile()
            values = compiled.params
            objects.append(model_class(**values))

        # Handle bulk (multi) insert
        elif hasattr(statement, "_multi_values"):

            def _process_value_dict(value_dict: dict) -> dict:
                """Helper to process a value dictionary and normalize keys"""
                return {
                    key.name if hasattr(key, "name") else str(key): value
                    for key, value in value_dict.items()
                }

            for value_set in statement._multi_values:
                if isinstance(value_set, dict):
                    # Handle single value set
                    processed_values = _process_value_dict(value_set)
                    objects.append(model_class(**processed_values))
                else:
                    # Handle sequence of value sets
                    for entry in value_set:
                        if isinstance(entry, dict):
                            processed_values = _process_value_dict(entry)
                            objects.append(model_class(**processed_values))
        else:
            raise ValueError("Insert statement has no values")

        return objects

    def can_read(self, auth_context: AuthContext, obj: SQLModel) -> bool:
        """Check if the user can read the object"""
        return self._can_perform_action(auth_context, obj, "read")

    def can_update(self, auth_context: AuthContext, obj: SQLModel) -> bool:
        """Check if the user can update the object"""
        try:
            self.set_system_managed_fields_orm(obj, auth_context)
        except (SystemManagedFieldViolation, SystemManagedFieldRequired) as e:
            # Log at warning level since this is an expected auth check failure, not an error
            logger.warning("Authorization check failed: %s", str(e))
            return False

        return self._can_perform_action(auth_context, obj, "update")

    @overload
    def can_create(self, auth_context: AuthContext, *, obj: SQLModel) -> bool: ...

    @overload
    def can_create(
        self,
        auth_context: AuthContext,
        *,
        statement: Insert,
    ) -> bool: ...

    def can_create(
        self,
        auth_context: AuthContext,
        *,
        obj: Optional[SQLModel] = None,
        statement: Optional[Insert] = None,
    ) -> bool:
        """Check if the user can create the object"""
        if statement is not None:
            objs = self._get_object_from_insert_statement(statement)
        elif obj:
            objs = [obj]
        else:
            raise ValueError("Either obj or statement must be provided")

        return all(
            self._can_perform_action(auth_context, obj, "create") for obj in objs
        )

    def set_system_managed_fields_orm(self, obj: SQLModel, auth_context: AuthContext):
        """Set the system-managed fields for the object"""
        configs = self.model_configs.get(get_table_name(obj), [])
        if not configs:
            return

        system_managed_fields = configs[0].get_system_managed_fields
        values = obj.model_dump(exclude_unset=True)
        for field_name, auth_field in system_managed_fields:
            if (
                auth_field.get_value_from_context(auth_context) is None
                and auth_field.required
            ):
                raise SystemManagedFieldRequired(field_name)

            if field_name in values and values[
                field_name
            ] != auth_field.get_value_from_context(auth_context):
                raise SystemManagedFieldViolation(field_name)

            setattr(obj, field_name, auth_field.get_value_from_context(auth_context))

    def set_system_managed_fields_statement(
        self, statement: Insert, auth_context: AuthContext
    ) -> Insert:
        existing_values = []
        obj = self._get_object_from_insert_statement(statement)[0]

        configs = self.model_configs.get(get_table_name(obj), [])
        if not configs:
            return statement

        system_managed_fields = configs[0].get_system_managed_fields

        # Handle single insert
        if hasattr(statement, "_values") and statement._values is not None:
            value_dict = {}
            for key, value in statement._values.items():
                key_name = key.name if hasattr(key, "name") else key
                value_dict[key_name] = value
            existing_values.append(value_dict)

        # Handle bulk insert
        elif hasattr(statement, "_multi_values"):
            for value_set in statement._multi_values:
                # Handle both single dict and list of dicts cases
                entries = [value_set] if isinstance(value_set, dict) else value_set

                for entry in entries:
                    if isinstance(entry, dict):
                        # Normalize keys and build value dict
                        value_dict = {
                            key.name if hasattr(key, "name") else str(key): value
                            for key, value in entry.items()
                        }
                        existing_values.append(value_dict)

        # Process each set of values
        values_to_set = []
        for value_dict in existing_values:
            new_values = value_dict.copy()
            for field_name, auth_field in system_managed_fields:
                if (
                    auth_field.get_value_from_context(auth_context) is None
                    and auth_field.required
                ):
                    raise SystemManagedFieldRequired(field_name)

                if field_name in new_values:
                    actual_value = new_values[field_name]  # type: ignore
                    if isinstance(actual_value, BindParameter):
                        actual_value = actual_value.value

                    if actual_value != auth_field.get_value_from_context(auth_context):
                        raise SystemManagedFieldViolation(field_name)

                new_values[field_name] = auth_field.get_value_from_context(auth_context)  # type: ignore
            values_to_set.append(new_values)

        if len(values_to_set) == 1:
            statement = statement.values(values_to_set[0])
        else:
            statement = insert(statement.table).values(values_to_set)

        return statement

    def can_delete(self, auth_context: AuthContext, obj: SQLModel) -> bool:
        """Check if the user can delete the object"""
        return self._can_perform_action(auth_context, obj, "delete")

    def filter_query(
        self, query: Query, action: str, auth_context: AuthContext
    ) -> Query:
        conditions: List[ClauseElement] = []

        if isinstance(query, TextClause):
            raise NotImplementedError("TextClause queries are not supported for row-level security filtering")

        # Determine the FROM objects based on query type
        if query.is_select and isinstance(query, Select):
            from_objs = query.get_final_froms()
        elif query.is_update and isinstance(query, Update):
            from_objs = [query.table]
        elif query.is_delete and isinstance(query, Delete):
            from_objs = [query.table]
        elif query.is_insert and isinstance(query, Insert):
            from_objs = [query.table]
        else:
            raise ValueError("Unsupported query type")

        # Extract underlying tables from each FROM object.
        tables = []
        for from_obj in from_objs:
            tables.extend(extract_tables(from_obj))

        # Optionally, remove duplicates if needed.
        seen = set()
        unique_tables = []
        for table in tables:
            if table not in seen:
                unique_tables.append(table)
                seen.add(table)

        # Build a filter condition for each table based on its RLS configuration.
        for table in unique_tables:
            table_name = table.name
            configs = self.model_configs.get(table_name, [])
            if not configs:
                continue

            model_class = configs[0].model
            matching_configs = [
                cfg
                for cfg in configs
                if cfg.action == action and cfg.role in (auth_context.role, WILDCARD)
            ]

            # TODO: Revisit this to deterine if we should raise an error or return an empty query.
            # If there are no matching policies for this table, force no rows to be returned.
            if not matching_configs:
                conditions.append(False)
                continue

            table_filters = []
            for config in matching_configs:
                cfg_filters = []
                # Build context-based filters.
                if auth_context.context and config.context_fields:
                    context_conditions = []
                    for field in config.context_fields:
                        if field in auth_context.context and hasattr(
                            model_class, field
                        ):
                            value = auth_context.context[field]
                            if isinstance(value, (list, tuple)):
                                context_conditions.append(
                                    getattr(model_class, field).in_(value)
                                )
                            else:
                                context_conditions.append(
                                    getattr(model_class, field) == value
                                )
                    if context_conditions:
                        cfg_filters.append(or_(*context_conditions))

                # Build an ownership filter.
                if config.owner_field:
                    cfg_filters.append(
                        getattr(model_class, config.owner_field) == auth_context.id
                    )

                if cfg_filters:
                    table_filters.append(and_(*cfg_filters))

            if table_filters:
                conditions.append(or_(*table_filters))

        # Combine conditions from all tables with AND (each table's RLS must be enforced).
        if conditions:
            query = query.filter(and_(*conditions))
        else:
            # If no conditions, assume public access.
            query = query.filter(True)

        return query
