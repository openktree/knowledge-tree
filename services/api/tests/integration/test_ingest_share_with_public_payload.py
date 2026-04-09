"""Contract tests for the ``share_with_public_graph`` dispatch payload.

PR6 forces this flag ``False`` server-side for any ingest that has no
link sources (file uploads are always private). The PR5 workflow side
already validates that ``IngestConfirmInput.share_with_public_graph``
plumbs through to the bridge, but nothing in this codebase pinned the
*API → workflow input* contract end-to-end. These tests fill that gap by
driving ``_confirm_ingest_impl`` / ``_decompose_ingest_impl`` directly
and asserting the dispatch payload that hits ``dispatch_workflow``.

Each test creates its own minimal Conversation + IngestSource fixture
rather than reusing the heavy ASGI fixtures — the impl helpers take
plain args, so the test surface is small.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kt_api.research import _confirm_ingest_impl, _decompose_ingest_impl
from kt_api.schemas import IngestConfirmRequest, IngestDecomposeRequest
from kt_db.models import (
    Conversation,
    ConversationMessage,
    IngestSource,
    User,
)

USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000060")


def _stub_user(*, with_api_key: bool = True) -> User:
    user = User()
    user.id = USER_ID
    user.email = f"user-{USER_ID}@example.com"
    user.is_active = True
    user.is_superuser = False
    user.is_verified = True
    # ``require_api_key`` is patched in tests, so the field value here is
    # irrelevant — leaving it as a sentinel keeps the helper happy.
    user.openrouter_api_key = "sk-test" if with_api_key else None
    return user


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def user_row(session_factory):
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    async with session_factory() as s:
        await s.execute(
            pg_insert(User)
            .values(
                id=USER_ID,
                email=f"user-{USER_ID}@example.com",
                hashed_password="x",
                is_active=True,
                is_superuser=False,
                is_verified=True,
            )
            .on_conflict_do_nothing(index_elements=[User.id])
        )
        await s.commit()
    yield


async def _make_conversation(
    session_factory,
    *,
    source_types: list[str],
) -> str:
    """Insert a fresh ingest conversation with the requested source mix."""
    conv_id = uuid.uuid4()
    async with session_factory() as s:
        s.add(Conversation(id=conv_id, title="Test ingest", mode="ingest"))
        for st in source_types:
            s.add(
                IngestSource(
                    id=uuid.uuid4(),
                    conversation_id=conv_id,
                    source_type=st,
                    original_name=("https://example.com/x" if st == "link" else "doc.pdf"),
                    status="ready",
                )
            )
        await s.commit()
    return str(conv_id)


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def cleanup(session_factory):
    yield
    async with session_factory() as s:
        await s.execute(delete(ConversationMessage))
        await s.execute(delete(IngestSource))
        await s.execute(delete(Conversation).where(Conversation.title == "Test ingest"))
        await s.commit()


from contextlib import contextmanager


@contextmanager
def _patch_dispatch_and_auth():
    """Stack of patches every test needs.

    * ``dispatch_workflow`` is what ``dispatch_with_graph`` ultimately
      calls — capturing it lets us inspect the exact payload built by
      the impl helper.
    * ``require_api_key`` is bypassed because the impl helper raises
      403 when the user has no decrypted key, and we don't want to wire
      a real key path through these tests.

    Yields the dispatch mock so each test can assert on its call args.
    """
    dispatch_mock = AsyncMock(return_value="run-test")
    with (
        patch("kt_hatchet.client.dispatch_workflow", dispatch_mock),
        patch("kt_api.research.require_api_key", return_value="sk-test"),
    ):
        yield dispatch_mock


# ---------------------------------------------------------------------------
# _confirm_ingest_impl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_links_only_passes_through_true(session_factory, user_row, cleanup):
    """All-link ingest with the default ``share_with_public_graph=True`` must
    forward ``True`` to the workflow."""
    conv_id = await _make_conversation(session_factory, source_types=["link", "link"])
    with _patch_dispatch_and_auth() as dispatch_mock:
        async with session_factory() as session:
            await _confirm_ingest_impl(session, conv_id, IngestConfirmRequest(nav_budget=10), _stub_user())

    payload = dispatch_mock.await_args.args[1]
    assert payload["share_with_public_graph"] is True


@pytest.mark.asyncio
async def test_confirm_files_only_forces_false(session_factory, user_row, cleanup):
    """File-only ingest must be forced ``False`` regardless of client value.

    This is the load-bearing privacy guarantee: an uploaded file should
    NEVER be pushed to the public graph, even if a buggy client sends
    ``share_with_public_graph=True``.
    """
    conv_id = await _make_conversation(session_factory, source_types=["file"])
    with _patch_dispatch_and_auth() as dispatch_mock:
        async with session_factory() as session:
            await _confirm_ingest_impl(
                session,
                conv_id,
                IngestConfirmRequest(nav_budget=10, share_with_public_graph=True),
                _stub_user(),
            )

    payload = dispatch_mock.await_args.args[1]
    assert payload["share_with_public_graph"] is False


@pytest.mark.asyncio
async def test_confirm_per_ingest_opt_out_overrides_links(session_factory, user_row, cleanup):
    """``share_with_public_graph=False`` from the client must be honoured even
    when there are link sources — that's how a user privately ingests a URL."""
    conv_id = await _make_conversation(session_factory, source_types=["link"])
    with _patch_dispatch_and_auth() as dispatch_mock:
        async with session_factory() as session:
            await _confirm_ingest_impl(
                session,
                conv_id,
                IngestConfirmRequest(nav_budget=10, share_with_public_graph=False),
                _stub_user(),
            )

    payload = dispatch_mock.await_args.args[1]
    assert payload["share_with_public_graph"] is False


@pytest.mark.asyncio
async def test_confirm_mixed_sources_passes_true_when_any_link(session_factory, user_row, cleanup):
    """A mixed file+link ingest is still considered a public-eligible ingest
    overall — the worker filters per-source, so the workflow flag stays True.
    """
    conv_id = await _make_conversation(session_factory, source_types=["file", "link"])
    with _patch_dispatch_and_auth() as dispatch_mock:
        async with session_factory() as session:
            await _confirm_ingest_impl(session, conv_id, IngestConfirmRequest(nav_budget=10), _stub_user())

    payload = dispatch_mock.await_args.args[1]
    assert payload["share_with_public_graph"] is True


# ---------------------------------------------------------------------------
# _decompose_ingest_impl — same matrix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decompose_links_only_passes_through_true(session_factory, user_row, cleanup):
    conv_id = await _make_conversation(session_factory, source_types=["link"])
    with _patch_dispatch_and_auth() as dispatch_mock:
        async with session_factory() as session:
            await _decompose_ingest_impl(session, conv_id, IngestDecomposeRequest(), _stub_user())

    payload = dispatch_mock.await_args.args[1]
    assert payload["share_with_public_graph"] is True


@pytest.mark.asyncio
async def test_decompose_files_only_forces_false(session_factory, user_row, cleanup):
    conv_id = await _make_conversation(session_factory, source_types=["file"])
    with _patch_dispatch_and_auth() as dispatch_mock:
        async with session_factory() as session:
            await _decompose_ingest_impl(
                session,
                conv_id,
                IngestDecomposeRequest(share_with_public_graph=True),
                _stub_user(),
            )

    payload = dispatch_mock.await_args.args[1]
    assert payload["share_with_public_graph"] is False


@pytest.mark.asyncio
async def test_decompose_client_opt_out_short_circuits_query(session_factory, user_row, cleanup):
    """When the client already opted out, the impl must NOT issue the
    ``IngestSource.get_by_conversation`` lookup at all — the per-call
    short-circuit added in PR6 saves an unnecessary roundtrip on the
    common file-upload path.
    """
    conv_id = await _make_conversation(session_factory, source_types=["file"])

    with (
        _patch_dispatch_and_auth() as dispatch_mock,
        patch(
            "kt_api.research.IngestSourceRepository.get_by_conversation",
            new=AsyncMock(side_effect=AssertionError("must not be called when client opted out")),
        ),
    ):
        async with session_factory() as session:
            await _decompose_ingest_impl(
                session,
                conv_id,
                IngestDecomposeRequest(share_with_public_graph=False),
                _stub_user(),
            )

    payload = dispatch_mock.await_args.args[1]
    assert payload["share_with_public_graph"] is False
