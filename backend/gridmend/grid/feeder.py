"""Which feeder the twin models. The IEEE 33-bus case is the default; mv_oberrhein is a real
20 kV network. Each has its own graphs, so both can be loaded at once. The CLI picks one with
GRIDMEND_FEEDER; the API picks one per request (see api.py), which is why the choice lives in a
context variable rather than a module constant."""
import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

FEEDERS = {
    "ieee33": "IEEE 33-bus test feeder",
    "oberrhein": "Oberrhein, a real 20 kV network",
}
DEFAULT = os.getenv("GRIDMEND_FEEDER", "ieee33")
if DEFAULT not in FEEDERS:
    raise ValueError(f"GRIDMEND_FEEDER must be one of {', '.join(FEEDERS)}, not {DEFAULT}")

DATA = Path(__file__).resolve().parents[2] / "data"

_current: ContextVar[str] = ContextVar("feeder", default=DEFAULT)


def name() -> str:
    return _current.get()


@contextmanager
def use(feeder: str):
    if feeder not in FEEDERS:
        raise ValueError(f"unknown feeder {feeder}")
    token = _current.set(feeder)
    try:
        yield
    finally:
        _current.reset(token)


def assumptions_path() -> Path:
    return DATA / ("assumptions.yaml" if name() == "ieee33" else f"{name()}.yaml")


# The default feeder keeps its original graph names so existing deployments are unaffected.
def base_graph() -> str:
    return "grid_base" if name() == "ieee33" else f"{name()}_base"


def live_graph() -> str:
    return "grid_live" if name() == "ieee33" else f"{name()}_live"


def network_module():
    """The module that builds this feeder's pandapower network and maps its ids."""
    if name() == "oberrhein":
        from gridmend.grid import oberrhein

        return oberrhein
    from gridmend.grid import case33

    return case33
