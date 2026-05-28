# evo — self-evolving ecosystem in your terminal

Digital organisms with 7-gene genomes navigate a shifting 2D world, consuming resources, reproducing with mutation, and adapting as niches open and collapse.

```
╔════════════════════════════════════════════════════════════════╗
║            ★    ◆                              ·             ║
║   ·            ·                          ▲                   ║
║        ✿   ◆          ★                                      ║
║                   ·          ■                             ·  ║
║                                                              ║
║  ▲                                 ■                         ║
║           ·                                  ◆               ║
║★                   ■                                          ║
║                       ◆                      ·                ║
║    ■                                        ◆                ║
║             ★                              ·                  ║
║  ✿                                                     ▲     ║
║                              ·                              ║
║       ·    ▲                                       ·        ║
║                                                            ║
║                    ◆ ⋆                                      ║
║                                                    ★        ║
║    ·     ✿                                        ◆        ║
║                                                              ║
║·               ·    ✿                                        ║
║       ·                                   ■                ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝
  Pop:  54  ⚡:2.8  Gen: 12  Sp:24  Speed:1.6  Agg:1.6  Met:1.2  μMut:2.1  Res: 38  ☀S  T:28
  └─────────────────────────────────────────────────  ▄▅▇█▆▅▄▃▃▂▃▄▅▇█▆▅▄▃▃▂▃▄▅▇█▆▅▄▃▃▂
  └fit              ▃▄▅▆▇▆▅▄▃▂▃▄▅▆▇▆▅▄▃▂▃▄▅▆▇▆▅▄▃▂
  spd       ▇   sen      ▇  agg       ▇  met ▇     wnd ▇     hue ▁ ▁▅▇   mut ▇▁   
  ● dominant: [3 4 3 0 0 4 1] (24% of pop)
  ■ sentinel: [2 4 0 1 1 2 3]  gen=12  age=3  ⚡=2.1
  💀 6 died in a single tick
  🌋 Environment shift: -4 resources, +clusters in new locations
  ●=speed  ◆=sense  ▲=aggression  ■=metabolic  ★=wander  ✦=hue  ★=bounty  ✿=corpse
```

## Run

```bash
python3 evo.py
```

Ctrl+C to stop. A brief summary is printed on exit.

## Genes

Each organism has 7 genes. Every gene value has an energy cost per tick, creating trade-offs.

| Gene | Range | Cost/tick | Effect |
|---|---|---|---|
| `speed` | 0–3 | `gene × 0.02` | Moving further per tick toward resources |
| `sense` | 0–4 | `gene × 0.025` | Detection radius for finding resources |
| `aggression` | 0–3 | `gene × 0.015` | Fighting power when overlapping another organism |
| `metabolic` | 0–3 | `gene × 0.15` | Energy cost per tick; also gives +20%/level resource digestion bonus and affects lifespan |
| `wander` | 0–3 | none | Randomness in movement (higher = more exploratory) |
| `hue` | 0–5 | none | Visual color/glyph (no fitness effect — neutral marker) |
| `mut_rate` | 0–5 | none | Mutation rate per gene at reproduction (maps to 0.05–0.30) |

### Trade-offs drive specialization

- **Speedsters** (speed=3): cover ground fast but pay high movement cost
- **Seers** (sense=4): find bounty and corpses from far away but pay high sense cost
- **Pacifists** (agg=0): avoid fight costs but get killed by aggressors
- **Bolters** (met=3): extract more energy from resources but burn through it fast and die young
- **Marathoners** (met=0): minimal upkeep cost, live long lives but extract less per resource
- **Mutators** (mut_rate=5): explore the fitness landscape rapidly but lose good genomes to drift
- **Conservatives** (mut_rate=0): keep stable genomes but can't adapt to change

## Resources

| Type | Symbol | Value | Notes |
|---|---|---|---|
| Food | `·` | 1.5 | Common (65% of new spawns) — the everyday diet |
| Bounty | `★` | 5.5 | Rare (20%) — jackpot, fuels reproduction |
| Corpse | `✿` | 2.0 | From dead organisms (15%) — scavenger's meal |

Death recycles: 50% of organisms leave a corpse when they die. Energy-rich corpses give more.

## Reproduction

- **Asexual**: energy ≥ 5.0, clone + mutate, costs 2.75 energy
- **Sexual**: any adjacent pair both with energy ≥ 3.0, genome recombination, costs 1.75 each

Sexual reproduction is cheaper per parent and mixes genomes, but requires a nearby mate.

## Environment

| Mechanic | Interval | Effect |
|---|---|---|
| **Environment shift** | every 30–60 ticks | Removes resources in clusters, creates new clusters elsewhere |
| **Seasonal cycle** | every 70–110 ticks | Summer: high resource regen (6/tick), low base cost. Winter: low regen (2/tick), high cost |
| **Migration** | every 80–150 ticks | 3–8 random-genome organisms arrive from beyond |
| **Stress events** | 25% chance per shift after tick 100 | All organisms lose 0.5–1.5 energy |

## Visualization

| Element | Meaning |
|---|---|
| Glyph (●◆▲■★✦) | hue gene value — neutral marker |
| Color (red→magenta) | hue gene value |
| Brightness | energy: **bold** >7, normal >3, dim ≤3 |
| White background | **sentinel** — most-evolved organism |
| `spd ▇▂▅▃ sen ▂▇▂▄▆ ...` | Gene frequency histogram — height = prevalence of each allele |
| `└── ... ▄▅▇█▆▅▄▃` | Population sparkline — recent history |
| `└fit ... ▃▄▅▆▇` | Average energy trend — is the population getting fitter? |
| Event log | Last 3 events: gen milestones, extinctions, die-offs, migrations, seasons |

## Emergent behaviors observed

1. **Gene convergence**: speed, aggression, metabolic trend toward specific values based on the current environment and trade-off economics
2. **Mutator/conservative cycles**: after a shift, high-mutation variants rise and explore; once stable, low-mutation variants overtake them
3. **Population boom-bust**: seasonal winter + environmental shifts cause population crashes; survivors repopulate with different gene distributions
4. **Corpse clustering**: organisms that die in the same area create resource hotspots, attracting scavengers, which then fight, creating more corpses
5. **Sentinel churn**: the most-evolved (highest-generation) organism changes frequently — a "crown" that rarely sits on the same head for long
6. **Extinction cascades**: when a gene value is lost, it's gone forever (if it was ever discovered in the first place)
7. **Invasion resilience**: migrants usually die quickly but occasionally introduce a gene combination that outcompetes the natives

## Requirements

Python 3.8+. No dependencies.
