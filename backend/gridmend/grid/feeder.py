"""Which feeder the twin models. The IEEE 33-bus case is the default and the one the UI draws;
GRIDMEND_FEEDER=oberrhein loads the real mv_oberrhein network into separate graphs."""
import os
from pathlib import Path

NAME = os.getenv("GRIDMEND_FEEDER", "ieee33")
FEEDERS = ("ieee33", "oberrhein")
if NAME not in FEEDERS:
    raise ValueError(f"GRIDMEND_FEEDER must be one of {', '.join(FEEDERS)}, not {NAME}")

DATA = Path(__file__).resolve().parents[2] / "data"
ASSUMPTIONS_PATH = DATA / ("assumptions.yaml" if NAME == "ieee33" else f"{NAME}.yaml")

# The default feeder keeps its original graph names so existing deployments are unaffected.
BASE_GRAPH = "grid_base" if NAME == "ieee33" else f"{NAME}_base"
LIVE_GRAPH = "grid_live" if NAME == "ieee33" else f"{NAME}_live"


def network_module():
    """The module that builds this feeder's pandapower network and maps its ids."""
    if NAME == "oberrhein":
        from gridmend.grid import oberrhein

        return oberrhein
    from gridmend.grid import case33

    return case33
