"""Integration tests for ConversationRepository."""

import uuid

import pytest

from kt_db.repositories.conversations import ConversationRepository

pytestmark = pytest.mark.asyncio


async def test_create_conversation(db_session):
    repo = ConversationRepository(db_session)
    conv = await repo.create(title="Test Conversation")
    assert conv.id is not None
    assert conv.title == "Test Conversation"


async def test_create_conversation_no_title(db_session):
    repo = ConversationRepository(db_session)
    conv = await repo.create()
    assert conv.id is not None
    assert conv.title is None


async def test_get_by_id(db_session):
    repo = ConversationRepository(db_session)
    conv = await repo.create(title="Find Me")
    found = await repo.get_by_id(conv.id)
    assert found is not None
    assert found.title == "Find Me"


async def test_get_by_id_not_found(db_session):
    repo = ConversationRepository(db_session)
    found = await repo.get_by_id(uuid.uuid4())
    assert found is None


async def test_get_with_messages(db_session):
    repo = ConversationRepository(db_session)
    conv = await repo.create(title="With Messages")
    await repo.add_message(conv.id, 0, "user", "Hello")
    await repo.add_message(conv.id, 1, "assistant", "Hi there", status="completed")

    loaded = await repo.get_with_messages(conv.id)
    assert loaded is not None
    assert len(loaded.messages) == 2
    assert loaded.messages[0].role == "user"
    assert loaded.messages[1].role == "assistant"


async def test_list_recent(db_session):
    repo = ConversationRepository(db_session)
    await repo.create(title="conv_list_test_1")
    await repo.create(title="conv_list_test_2")
    results = await repo.list_recent(limit=10)
    assert len(results) >= 2


async def test_count(db_session):
    repo = ConversationRepository(db_session)
    await repo.create(title="conv_count_test")
    total = await repo.count()
    assert total >= 1


async def test_update_title(db_session):
    repo = ConversationRepository(db_session)
    conv = await repo.create(title="Old Title")
    await repo.update_title(conv.id, "New Title")
    await db_session.refresh(conv)
    assert conv.title == "New Title"


async def test_add_message(db_session):
    repo = ConversationRepository(db_session)
    conv = await repo.create(title="msg_test")
    msg = await repo.add_message(conv.id, 0, "user", "Question?")
    assert msg.id is not None
    assert msg.role == "user"
    assert msg.content == "Question?"
    assert msg.turn_number == 0


async def test_add_assistant_message_with_fields(db_session):
    repo = ConversationRepository(db_session)
    conv = await repo.create(title="asst_msg_test")
    msg = await repo.add_message(
        conv.id,
        1,
        "assistant",
        "Answer.",
        nav_budget=100,
        explore_budget=20,
        status="completed",
        visited_nodes=["node-1", "node-2"],
        created_nodes=["node-1"],
    )
    assert msg.nav_budget == 100
    assert msg.explore_budget == 20
    assert msg.status == "completed"
    assert msg.visited_nodes == ["node-1", "node-2"]
    assert msg.created_nodes == ["node-1"]


async def test_update_message(db_session):
    repo = ConversationRepository(db_session)
    conv = await repo.create(title="update_msg_test")
    msg = await repo.add_message(conv.id, 0, "assistant", "", status="pending")
    await repo.update_message(msg.id, status="completed", content="Done!")
    updated = await repo.get_message(msg.id)
    assert updated is not None
    assert updated.status == "completed"
    assert updated.content == "Done!"


async def test_get_message(db_session):
    repo = ConversationRepository(db_session)
    conv = await repo.create(title="get_msg_test")
    msg = await repo.add_message(conv.id, 0, "user", "Hello")
    found = await repo.get_message(msg.id)
    assert found is not None
    assert found.content == "Hello"


async def test_get_messages(db_session):
    repo = ConversationRepository(db_session)
    conv = await repo.create(title="get_msgs_test")
    await repo.add_message(conv.id, 0, "user", "First")
    await repo.add_message(conv.id, 1, "assistant", "Second", status="completed")
    await repo.add_message(conv.id, 2, "user", "Third")

    msgs = await repo.get_messages(conv.id)
    assert len(msgs) == 3
    assert msgs[0].turn_number == 0
    assert msgs[1].turn_number == 1
    assert msgs[2].turn_number == 2


async def test_get_all_visited_nodes(db_session):
    repo = ConversationRepository(db_session)
    conv = await repo.create(title="visited_test")
    await repo.add_message(
        conv.id, 1, "assistant", "A1",
        status="completed", visited_nodes=["n1", "n2"],
    )
    await repo.add_message(
        conv.id, 3, "assistant", "A2",
        status="completed", visited_nodes=["n2", "n3"],
    )
    # Pending message should not be included
    await repo.add_message(
        conv.id, 5, "assistant", "",
        status="pending", visited_nodes=["n4"],
    )

    visited = await repo.get_all_visited_nodes(conv.id)
    assert set(visited) == {"n1", "n2", "n3"}


async def test_get_latest_answer(db_session):
    repo = ConversationRepository(db_session)
    conv = await repo.create(title="latest_ans_test")
    await repo.add_message(
        conv.id, 1, "assistant", "First answer",
        status="completed",
    )
    await repo.add_message(
        conv.id, 3, "assistant", "Second answer",
        status="completed",
    )
    latest = await repo.get_latest_answer(conv.id)
    assert latest == "Second answer"


async def test_get_latest_answer_none(db_session):
    repo = ConversationRepository(db_session)
    conv = await repo.create(title="no_answer_test")
    latest = await repo.get_latest_answer(conv.id)
    assert latest is None


async def test_get_next_turn_number(db_session):
    repo = ConversationRepository(db_session)
    conv = await repo.create(title="turn_num_test")
    assert await repo.get_next_turn_number(conv.id) == 0

    await repo.add_message(conv.id, 0, "user", "Q")
    assert await repo.get_next_turn_number(conv.id) == 1

    await repo.add_message(conv.id, 1, "assistant", "A", status="completed")
    assert await repo.get_next_turn_number(conv.id) == 2


async def test_get_message_count(db_session):
    repo = ConversationRepository(db_session)
    conv = await repo.create(title="msg_count_test")
    assert await repo.get_message_count(conv.id) == 0

    await repo.add_message(conv.id, 0, "user", "Q")
    await repo.add_message(conv.id, 1, "assistant", "A", status="completed")
    assert await repo.get_message_count(conv.id) == 2
