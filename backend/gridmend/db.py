from falkordb import FalkorDB

from gridmend.config import load_settings


def connect() -> FalkorDB:
    settings = load_settings()
    return FalkorDB(host=settings.falkordb_host, port=settings.falkordb_port)


def ping() -> list[str]:
    db = connect()
    # A plain Redis would answer PING; GRAPH.LIST only succeeds with the graph module loaded.
    db.connection.ping()
    return db.list_graphs()
