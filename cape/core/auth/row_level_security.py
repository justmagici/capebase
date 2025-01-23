from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type, Union, overload

from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import Delete, Insert, Select, Update, and_, or_
from sqlalchemy.orm import Query
from sqlmodel import SQLModel

from cape.core.auth.access_control import AccessControl

WILDCARD = "*"


class RLSConfig(BaseModel):
    """Configuration for Row-Level Security on a SQL Model"""

    model_config = ConfigDict(extra='forbid', strict=True) 

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
        subject: str,
        subject_context: Optional[Dict[str, Any]],
        obj: SQLModel,
        action: str,
    ) -> bool:
        """Base method to check if user can perform an action on an object"""
        resource_context = self._build_resource_context(obj)
        return self.access_control.enforce(
            subject=subject,
            resource=get_table_name(obj),
            action=action,
            subject_context=subject_context,
            resource_context=resource_context,
        )

    def _get_object_from_insert_statement(self, statement: Insert) -> SQLModel:
        """Helper function to get the object from an insert statement"""
        table_name = statement.table.name
        model_class = self.model_configs[table_name][0].model

        compiled = statement.compile()
        values = compiled.params
        return model_class(**values)

    def can_read(
        self, subject: str, subject_context: Optional[Dict[str, Any]], obj: SQLModel
    ) -> bool:
        """Check if the user can read the object"""
        return self._can_perform_action(subject, subject_context, obj, "read")

    def can_update(
        self, subject: str, subject_context: Optional[Dict[str, Any]], obj: SQLModel
    ) -> bool:
        """Check if the user can update the object"""
        return self._can_perform_action(subject, subject_context, obj, "update")

    def can_create_with_statement(
        self, subject: str, subject_context: Optional[Dict[str, Any]], statement: Insert
    ) -> bool:
        """Check if the user can create the object"""
        return self._can_perform_action(
            subject,
            subject_context,
            self._get_object_from_insert_statement(statement),
            "create",
        )

    @overload
    def can_create(
        self, subject: str, subject_context: Optional[Dict[str, Any]], *, obj: SQLModel
    ) -> bool: ...

    @overload
    def can_create(
        self,
        subject: str,
        subject_context: Optional[Dict[str, Any]],
        *,
        statement: Insert,
    ) -> bool: ...

    def can_create(
        self,
        subject: str,
        subject_context: Optional[Dict[str, Any]],
        *,
        obj: Optional[SQLModel] = None,
        statement: Optional[Insert] = None,
    ) -> bool:
        """Check if the user can create the object"""
        if statement is not None:
            obj = self._get_object_from_insert_statement(statement)

        if not obj:
            raise ValueError("Either obj or statement must be provided")

        return self._can_perform_action(subject, subject_context, obj, "create")

    def can_delete(
        self, subject: str, subject_context: Optional[Dict[str, Any]], obj: SQLModel
    ) -> bool:
        """Check if the user can delete the object"""
        return self._can_perform_action(subject, subject_context, obj, "delete")

    def filter_query(
        self,
        query: Query,
        subject: str,
        action: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Query:
        """Modifies query to implement row-level filtering based on access control rules"""
        if query.is_select and isinstance(query, Select):
            table_name = query.get_final_froms()[0].name
        elif query.is_update and isinstance(query, Update):
            table_name = query.table.name
        elif query.is_delete and isinstance(query, Delete):
            table_name = query.table.name
        elif query.is_insert and isinstance(query, Insert):
            table_name = query.table.name
        else:
            raise ValueError("Unsupported query type")

        model_class = self.model_configs[table_name][0].model

        # Get all configurations for this model
        configs = self.model_configs.get(table_name, [])
        if not configs:
            return query

        # Find configs that match the requested action
        # TODO: Refactor wildcard across code base
        matching_configs = [
            cfg
            for cfg in configs
            if cfg.action == action and cfg.role in (subject, WILDCARD)
        ]

        if not matching_configs:
            return query.filter(False)

        # For each matching config, build its filters
        all_config_filters = []
        for config in matching_configs:
            config_filters = []

            # Add context-based filters
            if context and config.context_fields:
                context_conditions = []
                for field in config.context_fields:
                    if field in context and hasattr(model_class, field):
                        value = context[field]
                        if isinstance(value, (list, tuple)):
                            context_conditions.append(
                                getattr(model_class, field).in_(value)
                            )
                        else:
                            context_conditions.append(
                                getattr(model_class, field) == value
                            )
                if context_conditions:
                    config_filters.append(or_(*context_conditions))

            # Add ownership filter if specified
            if config.owner_field:
                config_filters.append(
                    getattr(model_class, config.owner_field) == subject
                )

            # If this config has filters, combine them with AND
            if config_filters:
                all_config_filters.append(and_(*config_filters))

        # If we have any filters, combine them with OR
        if all_config_filters:
            return query.filter(or_(*all_config_filters))

        return query.filter(True)
