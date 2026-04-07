"""Repository for multi-graph management."""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.keys import validate_schema_name
from kt_db.models import DatabaseConnection, Graph, GraphMember


class GraphRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---- Graph CRUD -------------------------------------------------------

    async def get_by_id(self, graph_id: uuid.UUID) -> Graph | None:
        result = await self._session.execute(select(Graph).where(Graph.id == graph_id))
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Graph | None:
        result = await self._session.execute(select(Graph).where(Graph.slug == slug))
        return result.scalar_one_or_none()

    async def get_default(self) -> Graph | None:
        result = await self._session.execute(select(Graph).where(Graph.is_default.is_(True)))
        return result.scalar_one_or_none()

    async def list_accessible(self, user_id: uuid.UUID, is_superuser: bool = False) -> list[Graph]:
        """Return graphs the user can access.

        Superusers see all non-deleted graphs.
        Regular users see the default graph + graphs where they are a member.
        """
        stmt = select(Graph).where(Graph.status != "deleted")
        if not is_superuser:
            stmt = stmt.outerjoin(GraphMember, GraphMember.graph_id == Graph.id).where(
                or_(
                    Graph.is_default.is_(True),
                    GraphMember.user_id == user_id,
                )
            )
        stmt = stmt.order_by(Graph.is_default.desc(), Graph.name)
        result = await self._session.execute(stmt)
        return list(result.scalars().unique().all())

    async def create(
        self,
        *,
        slug: str,
        name: str,
        description: str | None = None,
        graph_type: str = "v1",
        byok_enabled: bool = False,
        storage_mode: str = "schema",
        schema_name: str | None = None,
        database_connection_id: uuid.UUID | None = None,
        created_by: uuid.UUID | None = None,
        is_default: bool = False,
        status: str = "provisioning",
    ) -> Graph:
        resolved_schema = schema_name or f"graph_{slug}"
        validate_schema_name(resolved_schema)

        graph = Graph(
            id=uuid.uuid4(),
            slug=slug,
            name=name,
            description=description,
            graph_type=graph_type,
            byok_enabled=byok_enabled,
            storage_mode=storage_mode,
            schema_name=resolved_schema,
            database_connection_id=database_connection_id,
            created_by=created_by,
            is_default=is_default,
            status=status,
        )
        self._session.add(graph)
        await self._session.flush()
        return graph

    async def update(
        self,
        graph: Graph,
        *,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> Graph:
        if name is not None:
            graph.name = name
        if description is not None:
            graph.description = description
        if status is not None:
            graph.status = status
        await self._session.flush()
        return graph

    # ---- Member management ------------------------------------------------

    async def get_members(self, graph_id: uuid.UUID) -> list[GraphMember]:
        result = await self._session.execute(select(GraphMember).where(GraphMember.graph_id == graph_id))
        return list(result.scalars().all())

    async def get_member_role(self, graph_id: uuid.UUID, user_id: uuid.UUID) -> str | None:
        result = await self._session.execute(
            select(GraphMember.role).where(
                GraphMember.graph_id == graph_id,
                GraphMember.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def add_member(
        self,
        graph_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str = "reader",
    ) -> GraphMember:
        member = GraphMember(
            id=uuid.uuid4(),
            graph_id=graph_id,
            user_id=user_id,
            role=role,
        )
        self._session.add(member)
        await self._session.flush()
        return member

    async def update_member_role(self, graph_id: uuid.UUID, user_id: uuid.UUID, role: str) -> GraphMember | None:
        result = await self._session.execute(
            select(GraphMember).where(
                GraphMember.graph_id == graph_id,
                GraphMember.user_id == user_id,
            )
        )
        member = result.scalar_one_or_none()
        if member is None:
            return None
        member.role = role
        await self._session.flush()
        return member

    async def remove_member(self, graph_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(GraphMember).where(
                GraphMember.graph_id == graph_id,
                GraphMember.user_id == user_id,
            )
        )
        member = result.scalar_one_or_none()
        if member is None:
            return False
        await self._session.delete(member)
        await self._session.flush()
        return True

    # ---- Database connections ---------------------------------------------

    async def get_database_connection(self, connection_id: uuid.UUID) -> DatabaseConnection | None:
        result = await self._session.execute(select(DatabaseConnection).where(DatabaseConnection.id == connection_id))
        return result.scalar_one_or_none()

    async def get_database_connection_by_key(self, config_key: str) -> DatabaseConnection | None:
        result = await self._session.execute(
            select(DatabaseConnection).where(DatabaseConnection.config_key == config_key)
        )
        return result.scalar_one_or_none()

    async def list_database_connections(self) -> list[DatabaseConnection]:
        result = await self._session.execute(select(DatabaseConnection).order_by(DatabaseConnection.name))
        return list(result.scalars().all())

    async def create_database_connection(self, *, name: str, config_key: str) -> DatabaseConnection:
        # "default" is reserved for the synthetic system-DB entry surfaced by
        # GET /api/v1/graphs/database-connections. A real row with this key
        # would be silently shadowed and graphs created against it would
        # actually land in the system DB.
        if config_key == "default":
            raise ValueError("config_key 'default' is reserved for the system database")
        conn = DatabaseConnection(
            id=uuid.uuid4(),
            name=name,
            config_key=config_key,
        )
        self._session.add(conn)
        await self._session.flush()
        return conn
