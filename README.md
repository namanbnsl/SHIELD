# Bengaluru SHIELD traffic experiments

SHIELD is a controlled traffic experiment built with SUMO and a real
OpenStreetMap road network from the HSR Layout–Bellandur–Outer Ring Road area
of Bengaluru.

The central question is simple:

> When an important road becomes unavailable, does telling more drivers about
> it improve the overall traffic situation?

The answer from the current experiments is: **usually, but not reliably**.
Across 50 matched tests, global information reduced average total delay, but
it made things worse in 20 of the 50 tests. This is evidence of a variable
effect, not a general rule that global information is always helpful.

The next experiment varied the number of informed vehicles. In the first
20-seed curve, 100% information had the lowest average delay, but the curve
was not smooth and no interior optimum was established.

This is a synthetic simulation, not a live traffic prediction system. The
roads and road attributes come from OpenStreetMap, but the trips are generated
by the project and are not calibrated against observed Bengaluru traffic.

## What every experiment has in common

Unless a table says otherwise, the experiments use:

- the same cached Bengaluru road network, imported for left-hand traffic;
- SUMO 1.27.1, one simulation thread, and a two-hour simulation;
- trips departing during the first simulated hour;
- the same road closure: edge `377105483#0`, named Outer Ring Road;
- the same closure time: `t = 900` seconds, or 15 minutes;
- paired runs: the baseline and the disrupted run receive the same demand;
- a fixed random seed for each comparison.

The closure does not delete the edge from the network. At 900 seconds its lane
speed is set to `0.01 m/s`, making it effectively impassable while keeping the
network and route definitions valid. Vehicles that need that edge can then be
rerouted through the remaining network.

## The important terminology

### SUMO

SUMO is the microscopic traffic simulator. “Microscopic” means that it moves
individual vehicles, rather than treating traffic as one mathematical fluid.
It tracks each vehicle's route, departure, movement, delay, arrival, and
whether the vehicle gets stuck.

### Edge and route

An **edge** is one directed road segment in the SUMO network. A **route** is
the ordered list of edges a vehicle plans to use. The closed edge is one such
directed segment, not the whole Outer Ring Road.

### Baseline and closure run

A **baseline** is normal traffic with no disruption. A **closure run** uses the
same demand and network, but makes the selected edge unusable at 900 seconds.
Comparing these paired runs isolates the effect of the closure as much as this
simulation allows.

### Weighted demand

Trips are **not sampled uniformly from all road edges**. The demand generator
assigns larger sampling weights to likely residential and local roads when
choosing origins, and larger weights to primary, secondary, and trunk roads
when choosing destinations. It also rejects very short origin/destination
pairs; the effective minimum separation is 1,200 metres.

This is a modelling assumption: it creates a more plausible mixture of local
origins and major-road destinations, but it does not prove that the trips
match real travel patterns.

There is no separate “weighted closure” mechanism in this project. **The
demand is weighted; the closure is an effectively binary road disruption.**
If “weighted closures” appears in a discussion of this work, the more precise
description is “weighted demand under a road-closure experiment.”

### Minimal, global, and partial information

- **Minimal information:** only vehicles whose planned route is affected by
  the closure are allowed to reroute.
- **Global information:** every active vehicle is told about the closure and
  is allowed to reroute using current travel times. This can help vehicles
  avoid the closed road, but it can also send too many vehicles onto the same
  alternatives.
- **Partial information:** only a selected fraction of vehicles receives the
  global information. The selection is deterministic for a given seed.

“Information” here means a routing change inside the simulator. It does not
model how a real driver would receive or understand an alert.

### Teleport

A **teleport** is SUMO's emergency escape from a traffic jam. If a vehicle is
blocked for too long or the simulated traffic cannot resolve normally, SUMO
removes the vehicle from the jam and records a teleport in `statistics.xml`.

It does **not** mean that a real car travelled instantly. It means the
simulation could no longer represent that vehicle's movement faithfully. A
high teleport count is therefore a warning that the scenario is congested or
numerically difficult. A teleported vehicle is not counted as a completed
trip.

### The outcome measures

- **Completed trips:** vehicles that reached their destination.
- **Unfinished trips:** vehicles still in the network when the simulation
  ended.
- **Travel time:** arrival time minus departure time. Mean and median travel
  time use completed trips only.
- **Time loss:** SUMO's per-vehicle measure of time lost compared with moving
  at the vehicle's ideal/free-flow speed. The main information experiment sums
  this over all vehicles, so unfinished and end-vaporized vehicles are not
  silently dropped.
- **Vehicle-second:** one vehicle delayed for one second. For example,
  10,000 vehicle-seconds could mean 1,000 vehicles delayed by 10 seconds each.
- **Directly affected:** vehicles whose planned route reaches the closed edge
  after the closure.
- **Rerouted:** vehicles whose route actually changed to avoid the closure.
- **Maximum queue near closure:** the largest simultaneous number of vehicles
  on the closed edge and its directly incoming edges after the closure.
- **Maximum alternative-road congestion:** the largest simultaneous count on
  the set of alternative edges most frequently selected by rerouting vehicles.

Travel time is easy to understand, but it is censored: a vehicle that never
finishes has no completed-trip travel time. That is why the 50-seed information
experiment uses total time loss as its primary measure.

## Experiments that were run

The tables below describe the result files currently present in `results/`.
The generated simulation artifacts are ignored by Git, so these files are
local experiment outputs rather than permanent source data.

### 1. Basic 1,000-vehicle baseline

Command: `uv run shield-sim`

Seed 42, 1,000 vehicles, no closure:

| Measure | Result |
| --- | ---: |
| Mean completed-trip time | 400.40 s |
| Median completed-trip time | 379.00 s |
| Completed trips | 1,000 / 1,000 |
| Teleports | 0 |

This is the reference run for the first closure test.

### 2. One road closure at low demand

Command: `uv run shield-closure`

This repeats the 1,000-vehicle scenario with the Outer Ring Road edge closed
at 900 seconds.

| Measure | Baseline | Closure | Change |
| --- | ---: | ---: | ---: |
| Mean trip time | 400.40 s | 413.73 s | +13.33 s / +3.3% |
| Median trip time | 379.00 s | 391.50 s | +12.50 s |
| Completed trips | 1,000 | 1,000 | no change |
| Vehicles rerouted | 0 | 205 | — |
| Teleports | 0 | 1 | +1 |

At this low demand, the result behaves as expected: the closure makes trips
slower and forces 205 vehicles onto other routes.

Source: [`results/closure_comparison.csv`](results/closure_comparison.csv).

### 3. Coarse traffic-density calibration

Command: `uv run shield-calibrate`

Seed 42 was used at four traffic levels. Each row is a matched normal run and
closure run. The percentage is the change in mean completed-trip time:

| Vehicles | Baseline mean | Closure mean | Change | Completed: baseline / closure | Rerouted | Teleports: baseline / closure |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 | 400.40 s | 413.73 s | +3.3% | 1,000 / 1,000 | 205 | 0 / 1 |
| 2,000 | 411.54 s | 421.76 s | +2.5% | 2,000 / 2,000 | 398 | 3 / 1 |
| 4,000 | 660.60 s | 629.85 s | −4.7% | 3,826 / 4,000 | 849 | 324 / 176 |
| 6,000 | 1,093.77 s | 1,043.97 s | −4.6% | 5,640 / 5,584 | 1,282 | 907 / 866 |

The negative values at 4,000 and 6,000 vehicles do **not** show that closing a
road improves traffic. The baseline is already heavily unstable, and the
mean ignores vehicles that did not finish. Removing the slowest or stuck
vehicles from the average can make the remaining completed trips look faster.
The teleport counts also show that these scenarios are far more congested.

Source: [`results/density_calibration.csv`](results/density_calibration.csv).

### 4. Intermediate 2,500–3,500-vehicle sweep

Command: `uv run shield-calibrate --step4`

This was an intermediate calibration pass at seed 42:

| Vehicles | Baseline mean | Closure mean | Change | Completed: baseline / closure | Rerouted | Teleports: baseline / closure |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2,500 | 595.97 s | 620.93 s | +4.2% | 2,497 / 2,466 | 527 | 110 / 127 |
| 2,750 | 575.03 s | 624.17 s | +8.5% | 2,651 / 2,694 | 560 | 182 / 160 |
| 3,000 | 607.85 s | 582.21 s | −4.2% | 2,967 / 2,919 | 624 | 148 / 189 |
| 3,250 | 562.62 s | 671.56 s | +19.4% | 3,141 / 3,111 | 693 | 209 / 313 |
| 3,500 | 608.79 s | 626.51 s | +2.9% | 3,341 / 3,340 | 750 | 290 / 290 |

These are calibration results, not the final information comparison. They use
the demand files created for this intermediate pass. Their demand hashes are
different from the final weighted 2,500-vehicle baseline, so their travel
times should not be treated as the same scenario.

Source: [`results/density_calibration_step4.csv`](results/density_calibration_step4.csv).

### 5. Final weighted-demand baseline

Command: `uv run shield-calibrate --baseline-only --counts 2500`

This is the baseline used for the driver-information study: 2,500 vehicles,
seed 42, weighted demand, and no closure.

| Measure | Result |
| --- | ---: |
| Mean completed-trip time | 373.18 s |
| Median completed-trip time | 352.00 s |
| Completed trips | 2,500 / 2,500 |
| Teleports | 0 |

Source: [`results/density_baseline_calibration.csv`](results/density_baseline_calibration.csv).

### 6. Minimal versus global information: one seed

Command:

```bash
uv run shield-information \
  --project-dir outputs/calibration/vehicles-2500 \
  --vehicles 2500
```

Both policies use the same 2,500-vehicle demand file, network, seed 42, road,
and closure time.

| Measure | Minimal information | Global information |
| --- | ---: | ---: |
| Mean completed-trip time | 406.57 s | 460.17 s |
| Median completed-trip time | 369.00 s | 383.00 s |
| Total time loss | 229,113 vehicle-s | 362,784 vehicle-s |
| Completed trips | 2,500 | 2,500 |
| Teleports | 27 | 65 |
| Rerouted vehicles | 522 | 556 |
| Maximum alternative-road congestion | 31 | 38 vehicles |

For seed 42, global information was clearly worse. It caused more vehicles to
reroute and increased congestion on the alternative roads. This is one seed,
not the final conclusion.

The paired output is under
`outputs/calibration/vehicles-2500/results/information_comparison.csv`.

### 7. Preliminary 20-seed information pass

An earlier pass repeated the same minimal/global comparison for seeds 42
through 61. At that stage, completed-trip mean travel time was still being
used as the main measure.

- Global information helped in **13 / 20 seeds**.
- Global information hurt in **7 / 20 seeds**.
- Global information's average completed-trip time was about 40 seconds
  lower.
- Alternative-road congestion increased slightly on average.

This pass motivated two changes: use total time loss as the primary measure,
and run a larger 50-seed comparison. Its standalone aggregate file was
superseded by the final sweep, so the 20-seed numbers are historical context,
not the basis for the final conclusion.

### 8. Final 50-seed information comparison

Command: `uv run shield-information-sweep --parallel 4`

This repeated the minimal/global comparison for seeds 42 through 91. Every
seed used 2,500 weighted-demand vehicles and the same closure. The primary
comparison for each seed is:

```text
global total time loss − minimal total time loss
```

A negative value means global information produced less total delay. A
positive value means it produced more.

| Measure across 50 seeds | Minimal | Global | Global minus minimal |
| --- | ---: | ---: | ---: |
| Mean total time loss per seed | 678,523 vehicle-s | 642,996 vehicle-s | −35,527 |
| Mean completed-trip time | 539.91 s | 520.41 s | −19.50 s |
| Mean teleports | 130.70 | 121.58 | −9.12 |
| Mean maximum alternative congestion | 34.68 | 35.12 vehicles | +0.44 |

Outcome counts:

- Global information was better in **30 / 50 seeds (60%)**.
- Global information was worse in **20 / 50 seeds (40%)**.
- Global information produced more alternative-road congestion in **24 / 50
  seeds (48%)**.
- Global information produced more teleports in **22 / 50 seeds (44%)**.

The average favored global information, but the seed-to-seed variation was
large. A rough 95% interval for the mean total-delay difference is about
−105,000 to +34,000 vehicle-seconds, which includes zero. The safest
conclusion is therefore “global information often helps in this setup,” not
“global information is beneficial.”

Sources:

- [`results/information_seed_sweep.csv`](results/information_seed_sweep.csv)
- [`results/information_seed_sweep_summary.csv`](results/information_seed_sweep_summary.csv)

### 9. Pre-closure condition analysis

Command: `uv run shield-information-conditions --parallel 4`

The 50 seeds were divided into the 30 where global information helped and the
20 where it hurt. The analysis then measured traffic before the closure to see
whether the two groups were already different.

| Pre-closure measure | Global better | Global worse |
| --- | ---: | ---: |
| Average vehicles on alternative roads | 3.30 | 3.33 |
| Maximum vehicles on alternative roads | 9.67 | 9.75 |
| Vehicles on alternatives at closure | 3.63 | 3.65 |
| Average queue near closed road | 0.64 | 0.68 |
| Alternative-route concentration (HHI) | 0.0386 | 0.0390 |

These values are almost identical. The available pre-closure measurements did
not provide a useful predictor of whether global information would help or
hurt. In the per-seed analysis, the correlations with the global-minus-minimal
delay difference were weak (about `r = 0.05` to `0.30`).

**HHI** is the Herfindahl–Hirschman Index: it increases when rerouted traffic
is concentrated on fewer edges and decreases when it is spread out. Here the
values are low and nearly the same in the two outcome groups.

Source: [`results/information_condition_summary.csv`](results/information_condition_summary.csv).

### 10. Information penetration: 20 seeds

Command: `uv run shield-information-penetration --parallel 4 --resume`

This experiment kept the same 2,500 vehicles, weighted demand, road closure,
and closure time. It changed only the fraction of vehicles that received the
global closure information. The default levels were 0%, 25%, 50%, 75%, and
100%, with seeds 42 through 61.

At 0%, vehicles follow the minimal-information policy. At 100%, they follow
the global-information policy. The intermediate levels give the global
warning to a deterministic subset of vehicles; all other vehicles keep the
minimal-information behavior. Each seed therefore has exactly 0, 625, 1,250,
1,875, or 2,500 informed vehicles.

| Informed | Mean total time loss | Median total time loss | Change from 0% | Better than 0% | Mean teleports | Mean alternative congestion |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0% | 678,736 vehicle-s | 639,253 vehicle-s | 0 | 0 / 20 | 129.8 | 34.80 vehicles |
| 25% | 719,954 vehicle-s | 665,780 vehicle-s | +41,218 | 7 / 20 | 134.3 | 35.15 vehicles |
| 50% | 678,910 vehicle-s | 567,149 vehicle-s | +174 | 10 / 20 | 121.1 | 34.10 vehicles |
| 75% | 692,169 vehicle-s | 647,352 vehicle-s | +13,433 | 9 / 20 | 133.4 | 34.50 vehicles |
| 100% | 587,861 vehicle-s | 462,943 vehicle-s | −90,875 | 12 / 20 | 103.5 | 35.45 vehicles |

```text
Mean total time loss across 20 seeds

  0% |███████████████████ | 678.7k vehicle-s
 25% |████████████████████| 720.0k vehicle-s
 50% |███████████████████ | 678.9k vehicle-s
 75% |███████████████████ | 692.2k vehicle-s
100% |████████████████    | 587.9k vehicle-s
```

The curve is not monotonic. In this sample, 25% informed was the worst level,
50% was almost the same as 0%, and 100% was the best. The seed-to-seed
variation is still large, so this does not yet prove that 100% is generally
optimal or that partial information is generally harmful.

The paired 95% uncertainty intervals for the change from 0% were:

- 25%: −35,656 to +118,092 vehicle-seconds
- 50%: −82,093 to +82,442 vehicle-seconds
- 75%: −71,353 to +98,218 vehicle-seconds
- 100%: −206,348 to +24,597 vehicle-seconds

All four intervals include zero. This is an informative first curve, not yet
a confirmed optimal penetration level.

Sources:

- [`results/information_penetration.csv`](results/information_penetration.csv)
- [`results/information_penetration_summary.csv`](results/information_penetration_summary.csv)

## What the experiments support

1. A single important-road closure measurably worsens travel at low demand.
2. More traffic makes the simulation unstable quickly; completed-trip means
   become unsafe to interpret once many vehicles fail to finish.
3. Global information can reduce total delay on average in the 2,500-vehicle
   weighted-demand scenario.
4. The benefit is not reliable: it failed in 40% of the 50 matched seeds.
5. The measured pre-closure traffic conditions did not explain the difference
   between the successful and unsuccessful seeds.
6. Rerouting can solve one problem while creating another: global information
   avoids the closed road but can overload alternative roads.
7. Information penetration produces a non-smooth curve in the first 20-seed
   test. The 100% level was best on average, but no interior optimum was
   established.

## What the experiments do not support

- They do not show that global information always helps.
- They do not show that alternative-road occupancy is a reliable trigger for
  withholding information.
- They do not estimate real Bengaluru travel times.
- They do not justify comparing the intermediate 2,500–3,500 sweep directly
  with the final weighted-demand baseline.
- They do not test floods, emergency vehicles, hospitals, traffic-light
  control, AI drivers, or a web interface.

## Reproduce the main results

Setup is intentionally short:

```bash
uv sync
```

The first run downloads and caches the OSM extract and builds the SUMO network.
After that, the main commands are:

```bash
uv run shield-sim
uv run shield-closure
uv run shield-calibrate
uv run shield-calibrate --step4
uv run shield-information-sweep --parallel 4
uv run shield-information-conditions --parallel 4
uv run shield-information-penetration --parallel 4 --resume
```

The information comparison expects the 2,500-vehicle scenario to exist; run
the calibration or demand-generation step first. The seed sweeps are
expensive because they run many SUMO simulations.

For tests:

```bash
uv run pytest
```

## Data and reproducibility

The default map covers approximately `77.62,12.90,77.69,12.94` (west, south,
east, north). OSM supplies the road geometry and tagged road attributes. The
demand, closure, and information policies are synthetic.

The seed controls demand sampling, departure times, candidate selection, and
SUMO's random choices. Reproducibility requires the cached OSM snapshot, the
pinned SUMO version, the same options, and one simulation thread. Summary
files record SHA-256 hashes for the network and demand so paired runs can be
checked to be genuinely like-for-like.

Map data © OpenStreetMap contributors, available under the ODbL.
