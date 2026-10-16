# pandapower takes over a minute to import on a small host, so it is imported only when a
# power flow actually runs.
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pandapower.auxiliary import pandapowerNet


def ieee_bus(pp_index: int) -> int:
    # pandapower numbers buses from 0; the IEEE case and our graph use 1-33.
    return int(pp_index) + 1


def line_id(from_pp: int, to_pp: int) -> str:
    a, b = sorted((ieee_bus(from_pp), ieee_bus(to_pp)))
    return f"{a}-{b}"


@cache
def _base_network() -> pandapowerNet:
    import pandapower.networks as pn

    return pn.case33bw()


def load_network() -> pandapowerNet:
    # case33bw stores the 5 ties as out-of-service lines; lengths are 1 km, so ohm/km is ohm.
    # Building the case is far slower than a power flow on it, so build once and hand out copies.
    return copy.deepcopy(_base_network())


# A power flow depends only on the fixed case and on which lines conduct, so results are kept
# per configuration; the evaluation saves them for the deployed image (see eval/run.py).
_VOLTAGES: dict[frozenset[str], dict[int, float]] = {}


class PowerFlowError(Exception):
    pass


def bus_voltages(conducting: set[str]) -> dict[int, float]:
    """Run a power flow with exactly the given line ids in service; pu voltage per fed bus."""
    key = frozenset(conducting)
    if key not in _VOLTAGES:
        import pandapower as pp

        net = load_network()
        net.line["in_service"] = [
            line_id(f, t) in conducting for f, t in zip(net.line.from_bus, net.line.to_bus)
        ]
        try:
            pp.runpp(net, numba=False)
        except pp.LoadflowNotConverged as exc:
            raise PowerFlowError("power flow did not converge") from exc
        # Buses cut off from the source come back as NaN.
        vm = net.res_bus.vm_pu.dropna()
        _VOLTAGES[key] = {ieee_bus(i): float(v) for i, v in vm.items()}
    return _VOLTAGES[key]


def save_voltages(path: Path) -> None:
    rows = [{"conducting": sorted(key), "vm_pu": volts} for key, volts in _VOLTAGES.items()]
    path.write_text(json.dumps(rows))


def load_voltages(path: Path) -> int:
    if not path.exists():
        return 0
    for row in json.loads(path.read_text()):
        _VOLTAGES[frozenset(row["conducting"])] = {int(b): v for b, v in row["vm_pu"].items()}
    return len(_VOLTAGES)


@dataclass(frozen=True)
class BaseCase:
    buses: int
    closed_lines: list[str]
    ties: list[str]
    load_kw: float
    losses_kw: float
    min_v_pu: float
    min_v_bus: int


def run_base_case() -> BaseCase:
    import pandapower as pp

    net = load_network()
    pp.runpp(net, numba=False)

    ids = [line_id(f, t) for f, t in zip(net.line.from_bus, net.line.to_bus)]
    in_service = net.line.in_service.tolist()
    vm = net.res_bus.vm_pu

    return BaseCase(
        buses=len(net.bus),
        closed_lines=[i for i, on in zip(ids, in_service) if on],
        ties=[i for i, on in zip(ids, in_service) if not on],
        load_kw=float(net.load.p_mw.sum() * 1000),
        losses_kw=float(net.res_line.pl_mw.sum() * 1000),
        min_v_pu=float(vm.min()),
        min_v_bus=ieee_bus(vm.idxmin()),
    )
