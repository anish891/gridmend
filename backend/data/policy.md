# Service restoration priority policy

Synthetic policy for the GridMend demo feeder. It states which customers get power back first
after a fault, and how conditions on the day change that order.

## 1. Priority scale

1.1 Every facility on the feeder has a priority weight from 1 to 20. Ordinary customers are 1.
1.2 A facility with a weight above 1 is a critical site for the incident at hand.
1.3 Restoration plans are ranked by the total weight of the critical sites they bring back,
    then by the load restored, then by the fewest switch operations.

## 2. Standing priorities

2.1 Hospital: 10. Life safety. Never lowered.
2.2 Fire station: 10. Emergency response.
2.3 Water pumping: 10. Drinking water and fire-fighting pressure.
2.4 Telecom exchange: 10. Emergency calls and grid communications.
2.5 Cooling centre: 1. A community hall that only serves the public during a heat advisory.

## 3. Conditions that change priorities

3.1 Heat advisory (declared, or 40 °C and above forecast): an open cooling centre is life
    safety and rises to 10.
3.2 During a heat advisory the fire station drops to 6: it has on-site backup generation for
    at least 8 hours, while heat illness risk rises within hours.
3.3 Severe storm warning: the telecom exchange rises to 12, because emergency calls spike and
    crews rely on it to coordinate.
3.4 Water emergency (contamination, drought order or a major fire): water pumping rises to 12.
3.5 A condition only applies when the operator's report states it. Facilities the report does
    not affect keep their standing priority.

## 4. Choosing between safe plans

These rules guide the choice among plans that already passed every safety check. They never
justify a plan that failed one.

4.1 When a repair crew will reach the fault within 1 hour, prefer the plan with fewer switch
    operations if it restores the same critical sites, because the feeder will soon return to
    normal.
4.2 When further storms are forecast, prefer the plan with more margin (lower line loading,
    higher minimum voltage) if it restores the same critical sites.
4.3 Explain the choice to the operator and cite the rule numbers used.
