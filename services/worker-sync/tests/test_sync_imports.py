"""Basic import tests for worker-sync."""


def test_import_sync_engine():
    from kt_worker_sync.sync_engine import SyncEngine
    assert SyncEngine is not None


def test_import_keys():
    from kt_db.keys import make_node_key, make_edge_key
    assert make_node_key("concept", "test") == "concept:test"
    assert make_edge_key("related", "concept:a", "concept:b") == "related:concept:a--concept:b"


def test_import_write_models():
    from kt_db.write_models import WriteNode, WriteEdge, WriteDimension, WriteBase
    assert WriteNode.__tablename__ == "write_nodes"
    assert WriteEdge.__tablename__ == "write_edges"
    assert WriteDimension.__tablename__ == "write_dimensions"


def test_import_write_repositories():
    from kt_db.repositories.write_nodes import WriteNodeRepository
    from kt_db.repositories.write_edges import WriteEdgeRepository
    from kt_db.repositories.write_dimensions import WriteDimensionRepository
    assert WriteNodeRepository is not None
    assert WriteEdgeRepository is not None
    assert WriteDimensionRepository is not None
