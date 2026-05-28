# evo — self-evolving ecosystem in your terminal

Digital organisms with 11-gene genomes navigate a shifting 2D world, consuming resources, reproducing with mutation, and adapting as niches open and collapse.

No stable equilibrium. Ever-evolving.

```
╔══════════════════════════════════════════════════════════════════╗
║  ◆·  ▲     ◆     ··       ··          ·     ▲          ⬟      ║
║      ··  ·★  ★·✿  ·        ··   ·                         ·    ║
║  ▲  ·  ·  ·  ·   ···  ·✿   ·       ·  ·   ·  ·   ·             ║
║ ··  ★· ★ ·  ··   ·    ·    ·  ··  ·  ▲   ·    ·   ·            ║
║·  ·  ·   ·  ·   ·  ★   · ★  ·  ··  ·     ··        ·          ║
║  ·  ·   ··  ·    ·   ·  ·  ·  ·   ··  ·      ··  ·  ·          ║
║  ··  ·     ··   ··    ·   ·  ·   ·   ·   ·  ·   ·  ·  ·✿       ║
║   ·   ·   ·  ·  ·  ·    ·  ·   ·  ·  ·  ·  ·   ·  ·  ·         ║
║  ·  ·  ·   ·  ·   ··  ★   ·  ·   ·  ·  ·    ·  · ···   ·       ║
║·  ·   ·  ·   ·  ·  ·✿ ·   ·  ·★  ·  ·  ·  ·  ·  ·   ·        ║
║  ·   ·  ·· ·   ·   ·   ·  · ·  ·    ·  ·   ·    ·   ·          ║
║  ·    ·   ·  · ··   ·   ··  ·  · ·  ·   ·  ·    ▲     ·         ║
╚══════════════════════════════════════════════════════════════════╝
  Pop:  40  ⚡:3.0  Gen:  0  Age:0.0  Sp:40  H':3.69  Fos:   0  Y:40 M:0 O:0  ...
  spd ▅▅▇▅  sen ▆▁▇▄▁  agg ▃▇▄▅  met ▄▅▅▇  wnd ▇▆▃▆  hue ▃▁▇▄▃▅  ...
  ● Sapiens herba v.88: [2 2 1 2 3 0 1 0 0 0 2] (3% of pop)
```

## Methodology

Evo is an **open-ended artificial life simulation**. It applies no fitness function, selection pressure, or objective. Organisms live, eat, fight, and reproduce in a shared spatial grid. Evolution emerges bottom-up from the interplay of:

- **Resource competition** — food is finite, spatially distributed, and seasonally variable
- **Predator-prey dynamics** — diet gene creates trophic levels with emergent arms races
- **Environmental volatility** — shifts, seasons, radiation, and disease punctuate equilibrium
- **Trade-off economics** — every gene has an energy cost; no free lunch
- **Information inheritance** — genome + mutation + sexual recombination + horizontal gene transfer

This is exploratory simulation, not optimization. There is no "best" genome — only genomes that survive the current configuration of resources, predators, temperature, and disease.

### Experimental methodology

Perturb any parameter, observe what changes, repeat. The `--seed` flag ensures reproducibility. The `--continuous` flag auto-restarts on extinction to accumulate statistics across runs. The extinction log (`extinction.json`) records every critical event with full population state for later analysis.

## Run

```bash
python3 evo.py
```

Ctrl+C to stop. A summary is printed on exit.

### Options

| Flag | Default | Description |
|---|---|---|
| `--seed N` | 42 | Random seed (reproducible runs) |
| `--width N` | 64 | Grid width (16–256, affects extinction rate) |
| `--height N` | 26 | Grid height (8–64, affects extinction rate) |
| `--tick-rate N` | 0.06 | Seconds per tick (lower = faster) |
| `--volume N` | 0.3 | Sound volume 0–1 |
| `--no-sound` | — | Disable all sounds |
| `--log FILE` | extinction.json | Extinction event log (JSON Lines array) |
| `--continuous` | — | Auto-restart on extinction, accumulate log |

## Genes

Each organism has 11 genes. Every gene value has an energy cost per tick, creating trade-offs.

| Gene | Range | Cost/tick | Effect |
|---|---|---|---|
| `speed` | 0–3 | `gene × 0.02` | Steps per tick toward targets |
| `sense` | 0–4 | `gene × 0.025` | Detection radius for food/prey/predators |
| `aggression` | 0–3 | `gene × 0.015` | Fight power when overlapping or hunting |
| `metabolic` | 0–3 | `gene × 0.15` | Base upkeep; +20%/level food bonus; affects lifespan |
| `wander` | 0–3 | none | Random drift in movement (0 = direct, 3 = erratic) |
| `hue` | 0–5 | none | Visual color/glyph; affects camouflage zone matching |
| `mut_rate` | 0–5 | none | Per-gene mutation rate: 0.08/0.12/0.16/0.20/0.24/0.30 |
| `thermal` | 0–4 | `\|actual − pref\| × 0.25` | Preferred temperature (0=cold, 4=hot); mismatch costs energy |
| `diet` | 0–2 | 0/+0.02 | 0=herbivore, 1=carnivore, 2=omnivore |
| `toxin` | 0–3 | `gene × 0.01` | Hurts attackers; triggers predator learning |
| `lumen` | 0–3 | none | Bioluminescence: glows yellow, attracts mates (extends range), attracts predators |

## Resources

| Type | Symbol | Value | Notes |
|---|---|---|---|
| Food | `·` | 1.5 | Common (65%) |
| Bounty | `★` | 5.5 | Rare (20%) |
| Corpse | `✿` | 2.0 | From dead organisms (15%) |

Death recycles: 50% of organisms leave a corpse. Herbivores enrich soil (niche construction).

## Phenomena

| Feature | What it does |
|---|---|
| **Camouflage** | Hue matching temperature zone = defense bonus |
| **Warning coloration** | Toxin ≥ 2: bright white-on-purple glyph |
| **Predator learning** | Predators remember toxic hues, hesitate to attack |
| **Herding** | Nearby prey boost each other's defense |
| **Pack hunting** | Nearby predators boost attack power |
| **Sentinel alarm** | High-sense herbivores amplify fear response |
| **Spatial memory** | Organisms remember good foraging spots (last 3) |
| **Fat storage** | Store excess energy, draw during scarcity |
| **Torpor** | Hibernate when energy ≤ 0.5, minimal upkeep |
| **Sleep cycles** | Rest restores energy, zero upkeep, but leave vulnerable |
| **Kin selection** | Similar genomes share energy when adjacent |
| **Kin recognition** | Close-genome fights are reduced; inhibits cannibalism |
| **Parental care** | Parents feed nearby young for 5 ticks |
| **Symbiosis** | Herbivore + carnivore adjacent = mutual energy bonus |
| **Horizontal gene transfer** | Adjacent organisms swap a random gene (3% chance) |
| **Nest building** | Organisms build permanent shelters; nests buffer temperature |
| **Cannibalism** | Starving predators attack any organism, reduced by kin recognition |
| **Bioluminescence** | Lumen gene: glows yellow, extends mate detection, but prey are easier to spot |
| **Niche construction** | Herbivores grazing enriches soil fertility |
| **Disease** | Spontaneous outbreaks; spreads through proximity; immunity possible |
| **Age structure** | Juveniles (<3 ticks) pay extra; elders (>30) get reproduction discount |
| **Radiation** | Random genome-wide mutations (rare, 0.8% chance after tick 50) |
| **Extinction log** | JSON Lines array logging every critical/bottleneck/total event |

## Emergent behaviors observed

1. **Trophic cascades**: carnivore boom → herbivore bust → carnivore starvation → herbivore recovery
2. **Temperature stratification**: thermal tolerance drives north-south distribution
3. **Mutator/conservative cycles**: after shifts, high-mutators explore; in stability, conservatives dominate
4. **Arms races**: predator speed and prey sense/espace co-evolve
5. **Nest clustering**: organisms build near existing nests, creating permanent settlement
6. **Nocturnal niche**: sleeping organisms are vulnerable, creating predation opportunities

## Requirements

Python 3.8+. No dependencies.
