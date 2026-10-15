from dataclasses import dataclass

import pandapower as pp
import pandapower.networks as pn
from pandapower.auxiliary import pandapowerNet


def ieee_bus(pp_index: int) -> int:
    # pandapower numbers buses from 0; the IEEE case and our graph use 1-33.
    return int(pp_index) + 1


def line_id(from_pp: int, to_pp: int) -> str:
    a, b = sorted((ieee_bus(from_pp), ieee_bus(to_pp)))
    return f"{a}-{b}"


def load_network() -> pandapowerNet:
    # case33bw stores the 5 ties as out-of-service lines; lengths are 1 km, so ohm/km is ohm.
    return pn.case33bw()


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
