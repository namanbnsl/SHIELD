# Bengaluru SHIELD baseline simulation

A minimal, headless traffic simulation for a real slice of Bengaluru around HSR Layout, Bellandur, and the Outer Ring Road. It downloads OpenStreetMap roads, converts them into a left-hand-driving SUMO network, creates deterministic weighted passenger-car trips, runs SUMO, and exports trip-level metrics.

This milestone is intentionally limited to baseline road traffic. It has no AI agents, LLMs, floods, emergency services, hospitals, optimization, or web interface.

## Setup

Install [uv](https://docs.astral.sh/uv/), then from this directory run:

```bash
uv sync
```

The pinned `eclipse-sumo` package supplies SUMO, `netconvert`, and `duarouter`; a separate system SUMO installation is not required. The first simulation needs internet access to fetch the OSM extract from an Overpass API.

## Run

```bash
uv run shield-sim
```

Defaults: 1,000 vehicles, seed `42`, departures spread across one simulated hour, and a two-hour simulation cutoff. The command caches the downloaded OSM snapshot and converted network for later offline runs.

Useful overrides:

```bash
uv run shield-sim --seed 123 --vehicles 1000
uv run shield-sim --refresh-network
uv run shield-sim --help
```

`--refresh-network` intentionally replaces the cached OSM snapshot. Do not use it when comparing seeded runs: live OSM data can change independently of the random seed.

## View the simulation

After generating a run, open it in SUMO's desktop viewer with:

```bash
uv run shield-gui
```

Press Play in SUMO to begin vehicle movement, or launch it already running:

```bash
uv run shield-gui --start
```

To view demand generated with another seed, use the matching value, for example `uv run shield-gui --seed 123`. The command reports which headless simulation command to run if those generated files do not exist.

## Outputs

- `results/run.csv`: one row per requested vehicle, including scheduled departure, actual departure, arrival, travel time, status, origin, and destination.
- `results/run_summary.csv`: total/completed/unfinished counts, mean and median completed-trip time, seed, bounds, SUMO version, and input hashes.
- `outputs/seed-<seed>/`: demand manifest, routed demand, raw SUMO output, and tool logs for audit/debugging.
- `data/`: the cached OSM extract and converted SUMO road network.

Vehicle status is one of `completed`, `unfinished`, `not_departed`, `vaporized`, or `missing`. Mean and median travel times use completed trips only. Every requested vehicle remains represented in `run.csv`, even if it never completes.

## Reproducibility

The seed controls origin/destination sampling, departure times, routing, and SUMO. Runs are single-threaded. With the cached OSM snapshot, the pinned SUMO version, and the same options, demand is byte-for-byte reproducible and simulation results should match. Input and demand SHA-256 hashes are recorded in the summary CSV.

## Tests

```bash
uv run pytest
```

## Scope and data note

The default bounding box is `77.62,12.90,77.69,12.94` (west, south, east, north), roughly 7.5 km by 4.5 km across the HSR–Bellandur/ORR corridor. Roads and tagged attributes come from OpenStreetMap; map data is © OpenStreetMap contributors and available under the ODbL.

This is a synthetic baseline, not a calibrated model of observed Bengaluru traffic. OSM provides real road geometry and attributes; demand now weights residential/local roads as origins and major primary/secondary/trunk corridors as destinations, with a minimum trip separation to avoid very short artifacts.

## Step 2: controlled road closure

After the default baseline run, run the matched disruption experiment with:

```bash
uv run shield-closure
```

For the weighted 2,500-vehicle operating baseline, target its isolated
scenario explicitly:

```bash
uv run shield-closure --project-dir outputs/calibration/vehicles-2500 --vehicles 2500
```

At `t = 900s`, this closes edge `377105483#0`, a three-lane Outer Ring Road
segment selected as the busiest real road edge in the cached network. The closure run reuses the exact cached
network and `outputs/seed-42/demand.rou.xml`; it does not regenerate demand.
The segment is made operationally impassable (0.01 m/s) while retaining route
validity, then SUMO reroutes vehicles whose remaining route reaches it.

The comparison is written to `results/closure_comparison.csv`. The closure
summary also records directly affected and successfully rerouted vehicles,
plus peak occupancy across the most frequently selected alternative-route
edges.
Maximum queue/congestion is the largest simultaneous vehicle count on the
closed segment and its directly incoming edges, sampled once per simulation
second after closure.

## Step 3: traffic-density calibration

Run paired baseline and `t = 900s` closure experiments at 1,000, 2,000,
4,000, and 6,000 vehicles with:

```bash
uv run shield-calibrate
```

Every pair uses seed 42, the same one-hour departure window, the same demand
generator, and the same cached network. Isolated raw artifacts are stored under
`outputs/calibration/vehicles-<count>/`; the combined comparison is written to
`results/density_calibration.csv`.

The intermediate Step 4 sweep requested by the experiment plan uses:

```bash
uv run shield-calibrate --step4
```

It tests 2,500, 2,750, 3,000, 3,250, and 3,500 vehicles and writes
`results/density_calibration_step4.csv`.

For the weighted-demand baseline-only pass:

```bash
uv run shield-calibrate --baseline-only --counts 2500,3000
```

This writes `results/density_baseline_calibration.csv`; it does not run a
closure.
