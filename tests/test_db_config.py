"""Connection settings that only bite in production (no FalkorDB needed: creating a pool does not connect)."""
from backend.graph import db


def test_falkordb_read_timeout_exceeds_server_timeout_max():
    # redis-py 8 defaults socket_timeout to 5 s, which cut off the prod canary scan mid-seed.
    # The client read timeout must outlast FalkorDB's TIMEOUT_MAX (60 000 ms) so the server's error wins.
    kwargs = db.get_db().connection.connection_pool.connection_kwargs
    assert kwargs["socket_timeout"] >= 60
    assert kwargs["socket_timeout"] == db.SOCKET_TIMEOUT_S
    assert kwargs["socket_connect_timeout"] <= 10
