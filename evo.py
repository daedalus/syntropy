#!/usr/bin/env python3
"""evo - A self-evolving ecosystem in your terminal.

Organisms with tiny genomes navigate a shifting 2D world, consuming resources,
reproducing with mutation, and adapting as niches open and collapse.

No stable equilibrium. Ever-evolving.
"""

import random
import time
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Set

random.seed()

# --- CONFIG ---
WIDTH = 64
HEIGHT = 22
INITIAL_ORGANISMS = 40
INITIAL_RESOURCES = 80
RESOURCE_REGEN = 4
SEASON_LENGTH = (70, 110)
SUMMER_REGEN = 6
WINTER_REGEN = 2
SUMMER_BASE_COST = 0.03
WINTER_BASE_COST = 0.08
REPRODUCTION_THRESHOLD = 5.0
SEXUAL_THRESHOLD = 3.0
ENERGY_COST_PER_CHILD = 2.5
BASE_MUTATION_RATE = 0.10
ENV_SHIFT_INTERVAL = (30, 60)
MIGRATION_INTERVAL = (80, 150)
MIGRATION_BATCH = (3, 8)
TICK_RATE = 0.06

# Two resource types: common food, rare bounty
RESOURCE_TYPES = {
    "food":  {"value": 1.5, "symbol": "·", "weight": 0.65},
    "bounty":{"value": 5.5, "symbol": "★", "weight": 0.20},
    "corpse":{"value": 2.0, "symbol": "✿", "weight": 0.15},
}
RESOURCE_KEYS = list(RESOURCE_TYPES.keys())

# Mutation rate per gene value (0=low mut, 5=high mut)
MUT_RATES = [0.05, 0.08, 0.12, 0.16, 0.22, 0.30]

GENES = [
    ("speed", 0, 3),
    ("sense", 0, 4),
    ("aggression", 0, 3),
    ("metabolic", 0, 3),
    ("wander", 0, 3),
    ("hue", 0, 5),
    ("mut_rate", 0, 5),
]

GLYPH_SET = "●◆▲■★✦"
COLORS = [
    "\033[31m",
    "\033[33m",
    "\033[32m",
    "\033[36m",
    "\033[34m",
    "\033[35m",
]
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


@dataclass
class Organism:
    x: int
    y: int
    genome: list
    energy: float
    age: int
    generation: int
    id: int


class World:
    def __init__(self):
        self.organisms: List[Organism] = []
        self.resources: Dict[Tuple[int, int], str] = {}
        self.tick = 0
        self.next_id = 0
        self.shift_timer = random.randint(*ENV_SHIFT_INTERVAL)
        self.events: List[str] = []
        self.pop_history: List[int] = []
        self.fitness_history: List[float] = []
        self.max_gen_ever = 0
        self.max_age_ever = 0
        self.genes_found: List[Set[int]] = [set(range(g[1], g[2]+1)) for g in GENES]
        self.genes_lost: List[str] = []
        self.migration_timer = random.randint(*MIGRATION_INTERVAL)
        self.fossil_lineages: List[Tuple[int, ...]] = []
        self.season = "summer"
        self.season_timer = random.randint(*SEASON_LENGTH)
        self.diseased: Set[int] = set()

        for _ in range(INITIAL_RESOURCES):
            rtype = random.choices(RESOURCE_KEYS, weights=[t["weight"] for t in RESOURCE_TYPES.values()])[0]
            self._add_resource(
                random.randint(0, WIDTH - 1), random.randint(0, HEIGHT - 1), rtype
            )

        for _ in range(INITIAL_ORGANISMS):
            self._spawn(
                random.randint(0, WIDTH - 1),
                random.randint(0, HEIGHT - 1),
                [random.randint(g[1], g[2]) for g in GENES],
            )

    def _add_resource(self, x: int, y: int, rtype: str = "food"):
        self.resources[(x, y)] = rtype

    def _spawn(
        self, x: int, y: int, genome: list, energy: float = 3.0, generation: int = 0
    ):
        self.organisms.append(
            Organism(
                x=max(0, min(WIDTH - 1, x)),
                y=max(0, min(HEIGHT - 1, y)),
                genome=genome,
                energy=energy,
                age=0,
                generation=generation,
                id=self.next_id,
            )
        )
        self.next_id += 1

    def _mutate(self, genome: list, mut_rate: float = BASE_MUTATION_RATE) -> list:
        new = list(genome)
        for i in range(len(new)):
            if random.random() < mut_rate:
                g_min, g_max = GENES[i][1], GENES[i][2]
                delta = random.choice([-1, 1])
                new[i] = max(g_min, min(g_max, new[i] + delta))
        return new

    def _nearest_resource(self, org: Organism) -> Optional[Tuple[int, int]]:
        sense = org.genome[1]
        best_d = sense + 1
        best = None
        x, y = org.x, org.y
        for dx in range(-sense, sense + 1):
            for dy in range(-sense, sense + 1):
                pos = (x + dx, y + dy)
                if pos in self.resources:
                    d = abs(dx) + abs(dy)
                    if d < best_d:
                        best_d = d
                        best = pos
        return best

    def step(self):
        self.tick += 1
        self.shift_timer -= 1

        pre_pop = len(self.organisms)

        if self.shift_timer <= 0:
            self._shift_environment()
            self.shift_timer = random.randint(*ENV_SHIFT_INTERVAL)

        self.season_timer -= 1
        if self.season_timer <= 0:
            self.season = "winter" if self.season == "summer" else "summer"
            self.season_timer = random.randint(*SEASON_LENGTH)
            self.events.append(
                f"🌤️ Season: {'☀ summer' if self.season == 'summer' else '❄ winter'}"
            )

        dead: Set[int] = set()

        random.shuffle(self.organisms)

        for org in self.organisms:
            if org.id in dead:
                continue

            org.age += 1

            # --- SENSE & MOVE ---
            speed = org.genome[0] + 1
            wander = org.genome[4]
            target = self._nearest_resource(org)

            if target:
                tx, ty = target
                steps = min(speed, abs(tx - org.x) + abs(ty - org.y))
                for _ in range(steps):
                    dx = 1 if tx > org.x else -1 if tx < org.x else 0
                    dy = 1 if ty > org.y else -1 if ty < org.y else 0
                    if random.random() < 0.15 + wander * 0.12:
                        dx += random.choice([-1, 0, 1])
                        dy += random.choice([-1, 0, 1])
                    org.x = max(0, min(WIDTH - 1, org.x + (dx if dx else 0)))
                    org.y = max(0, min(HEIGHT - 1, org.y + (dy if dy else 0)))
                    if (org.x, org.y) in self.resources:
                        break
            else:
                for _ in range(speed):
                    org.x = max(
                        0,
                        min(
                            WIDTH - 1,
                            org.x + random.choice([-1, 0, 1]),
                        ),
                    )
                    org.y = max(
                        0,
                        min(
                            HEIGHT - 1,
                            org.y + random.choice([-1, 0, 1]),
                        ),
                    )

            # --- CONSUME RESOURCE ---
            pos = (org.x, org.y)
            if pos in self.resources:
                rtype = self.resources.pop(pos)
                base_val = RESOURCE_TYPES[rtype]["value"]
                met_bonus = 1.0 + org.genome[3] * 0.2
                org.energy += base_val * met_bonus

            # --- METABOLIC COST ---
            base_cost = SUMMER_BASE_COST if self.season == "summer" else WINTER_BASE_COST
            base_cost += org.genome[3] * 0.15
            speed_cost = org.genome[0] * 0.02
            sense_cost = org.genome[1] * 0.025
            agg_cost = org.genome[2] * 0.015
            org.energy -= base_cost + speed_cost + sense_cost + agg_cost

            # --- FIGHT (overlapping organisms) ---
            for other in self.organisms:
                if other is org or other.id in dead:
                    continue
                if other.x == org.x and other.y == org.y:
                    a = org.genome[2]
                    b = other.genome[2]
                    if a > 0 and b > 0:
                        org_power = max(0, org.energy) * (a + 1) / 4
                        other_power = max(0, other.energy) * (b + 1) / 4
                        total = org_power + other_power
                        if total > 0 and random.random() < org_power / total:
                            org.energy += other.energy * 0.25
                            dead.add(other.id)
                        else:
                            other.energy += org.energy * 0.25
                            dead.add(org.id)
                            break
                    elif a > 0 and random.random() < a / 3:
                        org.energy += other.energy * 0.2
                        dead.add(other.id)

            if org.id in dead:
                continue

            # --- DISEASE ---
            if org.id in self.diseased:
                # Spread BEFORE drain so patient zero infects others before dying
                for other in self.organisms:
                    if other.id in dead or other.id in self.diseased:
                        continue
                    if abs(other.x - org.x) <= 2 and abs(other.y - org.y) <= 2:
                        if random.random() < 0.20:
                            self.diseased.add(other.id)

                met = org.genome[3]
                drain = 0.12 * max(0.1, 1.0 - met * 0.15)
                org.energy -= drain
                if random.random() < 0.05 + met * 0.05:
                    self.diseased.discard(org.id)

            if org.id in dead:
                continue

            # --- STARVATION ---
            if org.energy <= 0:
                dead.add(org.id)
                continue

            # --- OLD AGE ---
            max_age = 180 // (org.genome[3] + 1) + 60
            if org.age > max_age:
                dead.add(org.id)
                continue

            # --- REPRODUCTION ---
            if (
                org.energy >= REPRODUCTION_THRESHOLD
                or (org.energy >= SEXUAL_THRESHOLD
                    and any(
                        o is not org and o.id not in dead
                        and o.energy >= SEXUAL_THRESHOLD
                        and abs(o.x - org.x) <= 1 and abs(o.y - org.y) <= 1
                        for o in self.organisms
                    ))
            ):
                # Try to find a mate for sexual reproduction
                mate = None
                # Only seek mate if both have >= SEXUAL_THRESHOLD
                if org.energy >= SEXUAL_THRESHOLD:
                    for other in self.organisms:
                        if other is org or other.id in dead:
                            continue
                        if other.energy >= SEXUAL_THRESHOLD:
                            dx = abs(other.x - org.x)
                            dy = abs(other.y - org.y)
                            if dx <= 1 and dy <= 1:
                                mate = other
                                break

                # Find adjacent empty cell
                neighbors = []
                for dx, dy in [(0,1),(0,-1),(1,0),(-1,0)]:
                    nx, ny = org.x + dx, org.y + dy
                    if 0 <= nx < WIDTH and 0 <= ny < HEIGHT:
                        if not any(
                            o.x == nx and o.y == ny
                            for o in self.organisms
                            if o.id not in dead
                        ):
                            neighbors.append((nx, ny))
                if neighbors:
                    nx, ny = random.choice(neighbors)
                    if mate:
                        # SEXUAL: recombine genomes from both parents
                        child_genome = []
                        for i in range(len(GENES)):
                            if random.random() < 0.5:
                                child_genome.append(org.genome[i])
                            else:
                                child_genome.append(mate.genome[i])
                        child_genome = self._mutate(child_genome, MUT_RATES[org.genome[6]])
                        child_gen = max(org.generation, mate.generation) + 1
                        energy_cost = ENERGY_COST_PER_CHILD * 0.7
                        self._spawn(nx, ny, child_genome, energy_cost, child_gen)
                        org.energy -= energy_cost
                        mate.energy -= energy_cost
                        if child_gen > self.max_gen_ever:
                            self.max_gen_ever = child_gen
                            self.events.append(
                                f"⚡ Gen {self.max_gen_ever} (sexual! "
                                f"{GLYPH_SET[child_genome[5] % len(GLYPH_SET)]})"
                            )
                    elif org.energy >= REPRODUCTION_THRESHOLD:
                        # ASEXUAL: clone + mutate
                        child_genome = self._mutate(org.genome, MUT_RATES[org.genome[6]])
                        self._spawn(nx, ny, child_genome, ENERGY_COST_PER_CHILD, org.generation + 1)
                        org.energy -= ENERGY_COST_PER_CHILD * 1.1
                        if org.generation + 1 > self.max_gen_ever:
                            self.max_gen_ever = org.generation + 1
                            self.events.append(
                                f"⚡ Gen {self.max_gen_ever} reached! "
                                f"({GLYPH_SET[child_genome[5] % len(GLYPH_SET)]})"
                            )

        # Remove dead — but leave corpse resources
        pop_before = len(self.organisms)
        dead_list = []
        kept = []
        for o in self.organisms:
            if o.id in dead:
                dead_list.append(o)
            else:
                kept.append(o)
        self.organisms = kept
        pop_after = len(self.organisms)
        died = pop_before - pop_after
        for o in dead_list:
            if random.random() < 0.5 and (o.x, o.y) not in self.resources:
                bounty_val = min(2.5, 0.3 + o.energy * 0.3)
                # corpseless corpses become food; energy-rich corpses become bounty-ish
                rtype = "corpse" if o.energy > 1.0 else "food"
                self._add_resource(o.x, o.y, rtype)

        # Track max age
        for o in self.organisms:
            if o.age > self.max_age_ever:
                self.max_age_ever = o.age

        # Gene loss detection
        current_gene_vals = [set() for _ in GENES]
        for o in self.organisms:
            for i, v in enumerate(o.genome):
                current_gene_vals[i].add(v)
        lost_this_tick = []
        for i, (expected, actual) in enumerate(zip(self.genes_found, current_gene_vals)):
            lost = expected - actual
            if lost:
                lost_this_tick.append((i, lost))
                self.genes_found[i] = current_gene_vals[i]
        for gene_idx, lost_vals in lost_this_tick:
            for v in lost_vals:
                self.events.append(
                    f"🧬 Extinct: {GENES[gene_idx][0]}={v} "
                    f"(never again)"
                )

        # Record history
        self.pop_history.append(pop_after)
        if len(self.pop_history) > 60:
            self.pop_history = self.pop_history[-60:]

        # Population crash event
        if died > 5 and pop_after > 0:
            self.events.append(f"💀 {died} died in a single tick")

        # Population recovery event
        if pre_pop < 10 and pop_after > pre_pop and pop_after >= 10:
            self.events.append(f"🌱 Population recovered to {pop_after}")

        # Spontaneous disease outbreak
        if not self.diseased and len(self.organisms) > 25 and random.random() < 0.03:
            candidates = [o for o in self.organisms if o.energy > 1.5]
            if len(candidates) >= 2:
                n = random.randint(2, min(5, len(candidates)))
                victims = random.sample(candidates, n)
                for v in victims:
                    self.diseased.add(v.id)
                self.events.append(f"🦠 Disease outbreak! {len(victims)} infected")
        elif self.diseased and random.random() < 0.002 and len(self.diseased) > 10:
            self.events.append(f"🦠 Epidemic: {len(self.diseased)} infected")

        # --- MIGRATION (invasion from outside) ---
        self.migration_timer -= 1
        if self.migration_timer <= 0:
            batch = random.randint(*MIGRATION_BATCH)
            for _ in range(batch):
                x = random.randint(0, WIDTH - 1)
                y = random.randint(0, HEIGHT - 1)
                genome = [random.randint(g[1], g[2]) for g in GENES]
                self._spawn(x, y, genome, 4.0)
            self.events.append(
                f"🌊 {batch} invaders arrived from beyond"
            )
            self.migration_timer = random.randint(*MIGRATION_INTERVAL)

        # --- REGENERATE RESOURCES ---
        regen = SUMMER_REGEN if self.season == "summer" else WINTER_REGEN
        for _ in range(regen):
            if len(self.resources) < WIDTH * HEIGHT * 0.25:
                x, y = random.randint(0, WIDTH - 1), random.randint(0, HEIGHT - 1)
                if (x, y) not in self.resources:
                    rtype = random.choices(
                        RESOURCE_KEYS,
                        weights=[t["weight"] for t in RESOURCE_TYPES.values()]
                    )[0]
                    self._add_resource(x, y, rtype)

        # Track fitness
        if self.organisms:
            self.fitness_history.append(
                sum(o.energy for o in self.organisms) / len(self.organisms)
            )
        if len(self.fitness_history) > 80:
            self.fitness_history = self.fitness_history[-80:]

    def _shift_environment(self):
        remove_n = int(len(self.resources) * random.uniform(0.15, 0.4))
        self.events.append(
            f"🌋 Environment shift: -{remove_n} resources, "
            f"+clusters in new locations"
        )
        if self.tick > 100 and random.random() < 0.25:
            self.events.append(
                f"🔥 Environmental stress event — all organisms lose energy"
            )
        if remove_n > 0 and self.resources:
            for pos in random.sample(
                list(self.resources.keys()), min(remove_n, len(self.resources))
            ):
                del self.resources[pos]

        clusters = random.randint(2, 5)
        for _ in range(clusters):
            cx = random.randint(6, WIDTH - 6)
            cy = random.randint(3, HEIGHT - 3)
            for _ in range(random.randint(5, 15)):
                x = max(0, min(WIDTH - 1, cx + random.randint(-4, 4)))
                y = max(0, min(HEIGHT - 1, cy + random.randint(-2, 2)))
                if (x, y) not in self.resources:
                    rtype = random.choices(RESOURCE_KEYS, weights=[t["weight"] for t in RESOURCE_TYPES.values()])[0]
                    self._add_resource(x, y, rtype)

        if random.random() < 0.3:
            for org in self.organisms:
                org.energy -= random.uniform(0.5, 1.5)

    def render(self) -> str:
        grid = [[" " for _ in range(WIDTH)] for _ in range(HEIGHT)]

        for (x, y), rtype in self.resources.items():
            if 0 <= x < WIDTH and 0 <= y < HEIGHT:
                occupied = any(o.x == x and o.y == y for o in self.organisms)
                if not occupied:
                    grid[y][x] = RESOURCE_TYPES[rtype]["symbol"]

        # Find sentinel (most-evolved organism)
        sentinel = max(self.organisms, key=lambda o: o.generation) if self.organisms else None
        sentinel_id = sentinel.id if sentinel else -1

        for org in self.organisms:
            if 0 <= org.x < WIDTH and 0 <= org.y < HEIGHT:
                glyph = GLYPH_SET[org.genome[5] % len(GLYPH_SET)]
                color = COLORS[org.genome[5] % len(COLORS)]
                if org.id == sentinel_id and org.generation > 0:
                    grid[org.y][org.x] = f"{BOLD}\033[47m\033[30m{glyph}{RESET}"
                elif org.id in self.diseased:
                    grid[org.y][org.x] = f"{BOLD}\033[41m{color}{glyph}{RESET}"
                elif org.energy > 7:
                    grid[org.y][org.x] = f"{BOLD}{color}{glyph}{RESET}"
                elif org.energy > 3:
                    grid[org.y][org.x] = f"{color}{glyph}{RESET}"
                else:
                    grid[org.y][org.x] = f"{DIM}{color}{glyph}{RESET}"

        lines = [f"{BOLD}╔{'═' * WIDTH}╗{RESET}"]
        for row in grid:
            lines.append(f"{BOLD}║{RESET}{''.join(row)}{BOLD}║{RESET}")
        lines.append(f"{BOLD}╚{'═' * WIDTH}╝{RESET}")

        if self.organisms:
            n = len(self.organisms)
            avg_e = sum(o.energy for o in self.organisms) / n
            max_g = max(o.generation for o in self.organisms)
            avg_spd = sum(o.genome[0] for o in self.organisms) / n
            avg_agg = sum(o.genome[2] for o in self.organisms) / n
            avg_met = sum(o.genome[3] for o in self.organisms) / n
            avg_mut = sum(o.genome[6] for o in self.organisms) / n
            species = len({tuple(o.genome) for o in self.organisms})

            # Dominant genome
            genome_counts: Dict[Tuple[int, ...], int] = {}
            for o in self.organisms:
                key = tuple(o.genome)
                genome_counts[key] = genome_counts.get(key, 0) + 1
            dominant_key = max(genome_counts, key=genome_counts.get) if genome_counts else ()
            dominant_pct = genome_counts.get(dominant_key, 0) / n * 100
            dominant_glyph = GLYPH_SET[dominant_key[5] % len(GLYPH_SET)] if dominant_key else "?"
        else:
            avg_e = max_g = avg_spd = avg_agg = avg_met = species = n = 0
            dominant_key = ()
            dominant_pct = 0
            dominant_glyph = "?"

        lines.append(
            f"  Pop:{n:4d}  ⚡:{avg_e:.1f}  Gen:{max_g:3d}  "
            f"Sp:{species:2d}  Speed:{avg_spd:.1f}  "
            f"Agg:{avg_agg:.1f}  Met:{avg_met:.1f}  "
            f"μMut:{avg_mut:.1f}  "
            f"Res:{len(self.resources):3d}  Inf:{len(self.diseased):2d}  "
            f"{'☀' if self.season == 'summer' else '❄'}{'S' if self.season == 'summer' else 'W'}  T:{self.tick}"
        )

        # Population sparkline (compact)
        if self.pop_history:
            max_pop = max(self.pop_history)
            min_pop = min(self.pop_history)
            span = max_pop - min_pop if max_pop > min_pop else 1
            SPARK = "▁▂▃▄▅▆▇█"
            sparkline = ""
            window = self.pop_history[-min(60, len(self.pop_history)):]
            for p in window:
                idx = int((p - min_pop) / span * (len(SPARK) - 1))
                sparkline += SPARK[idx]
            lines.append(f"  └{'─' * min(50, len(window))}  {sparkline}")

        # Fitness sparkline (rolling avg energy)
        if len(self.fitness_history) > 5:
            feats = self.fitness_history[-40:]
            mn, mx = min(feats), max(feats)
            rng = mx - mn if mx > mn else 1
            fitline = ""
            for f in feats:
                idx = int((f - mn) / rng * 7)
                fitline += "▁▂▃▄▅▆▇█"[idx]
            lines.append(f"  └fit              {fitline}")

        # Gene frequency bars (compact histogram per gene)
        if self.organisms:
            labels = ["spd", "sen", "agg", "met", "wnd", "hue", "mut"]
            bar_parts = []
            for i, (label, (_, g_min, g_max)) in enumerate(zip(labels, GENES)):
                counts = [0] * (g_max - g_min + 1)
                for o in self.organisms:
                    counts[o.genome[i]] += 1
                max_c = max(counts) if max(counts) > 0 else 1
                bars = "".join(
                    " ▁▂▃▄▅▆▇█"[min(7, int(c / max_c * 7))]
                    for c in counts
                )
                bar_parts.append(f"{label} {bars}")
            lines.append("  " + "  ".join(bar_parts))

        # Dominant genome line
        if dominant_key:
            genome_str = " ".join(str(g) for g in dominant_key)
            lines.append(
                f"  {BOLD}{dominant_glyph}{RESET} dominant: [{genome_str}] "
                f"({dominant_pct:.0f}% of pop)"
            )

        # Sentinel genome (most-evolved organism)
        if sentinel and sentinel.generation > 0:
            g = sentinel.genome
            tag = " 🦠" if sentinel.id in self.diseased else ""
            lines.append(
                f"  {BOLD}\033[47m\033[30m{GLYPH_SET[g[5] % len(GLYPH_SET)]}"
                f"\033[0m sentinel: [{g[0]} {g[1]} {g[2]} {g[3]} {g[4]} {g[5]} {g[6]}]"
                f"  gen={sentinel.generation}  age={sentinel.age}  ⚡={sentinel.energy:.1f}{tag}"
            )

        # Events (last 3)
        self.events = self.events[-3:]
        for ev in self.events:
            lines.append(f"  {ev}")
        lines.append(
            "  " + "  ".join(
                f"{COLORS[i]}{GLYPH_SET[i]}{RESET}={GENES[i][0]}"
                for i in range(min(len(GENES), len(GLYPH_SET)))
            ) + f"  {BOLD}★{RESET}=bounty  {BOLD}✿{RESET}=corpse"
        )
        return "\n".join(lines)


def main():
    world = World()
    print("\033c", end="")
    try:
        while world.organisms:
            print(world.render())
            world.step()
            time.sleep(TICK_RATE)
            print("\033[H", end="")
    except KeyboardInterrupt:
        total_extinct = sum(
            len(set(range(g[1], g[2]+1)) - world.genes_found[i])
            for i, g in enumerate(GENES)
        )
        print(f"\n\n{'═' * 40}")
        print(f"  ✦  Evolution halted after {world.tick} ticks")
        print(f"  Pop: {len(world.organisms)}  "
              f"Generations: {world.max_gen_ever}  "
              f"Max age: {world.max_age_ever}  ")
        print(f"  Species now: {len({tuple(o.genome) for o in world.organisms})}  "
              f"Infected: {len(world.diseased)}")
        if total_extinct:
            print(f"  Gene values lost to extinction: {total_extinct}")
        print(f"{'═' * 40}")


if __name__ == "__main__":
    main()
