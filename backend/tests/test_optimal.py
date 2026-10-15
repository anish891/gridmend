from eval.optimal import Feeder, optimal_restoration


def test_oracle_sees_the_same_feeder():
    feeder = Feeder()
    assert len(feeder.segments) == 23
    assert sorted(set(feeder.zone_of.values())) == [2, 3, 7, 10, 14, 19, 23, 26, 30]


def test_healthy_feeder_passes_every_oracle_check():
    feeder = Feeder()
    normal = frozenset(s for s, row in feeder.switches.items() if row["status"] == "closed")
    assert feeder.evaluate(normal, faulted=-1).violations == ()


def test_optimal_z3_restoration_matches_the_hand_analysis():
    best = optimal_restoration(Feeder(), "4-5")
    assert best.critical == ("hospital", "fire station")
    assert round(best.restored_kw) == 850
    assert best.switch_ops == 4
