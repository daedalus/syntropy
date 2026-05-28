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

EXTINCTION_LOG_FILE = "extinction.csv"

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
MUT_RATES = [0.08, 0.12, 0.16, 0.20, 0.24, 0.30]

GENES = [
    ("speed", 0, 3),
    ("sense", 0, 4),
    ("aggression", 0, 3),
    ("metabolic", 0, 3),
    ("wander", 0, 3),
    ("hue", 0, 5),
    ("mut_rate", 0, 5),
    ("thermal", 0, 4),
    ("diet", 0, 2),
    ("toxin", 0, 3),
]

GLYPH_SET = "●◆▲■★✦⬟⬢◈◎"
COLORS = [
    "\033[31m",
    "\033[33m",
    "\033[32m",
    "\033[36m",
    "\033[34m",
    "\033[35m",
    "\033[91m",
    "\033[95m",
    "\033[92m",
    "\033[93m",
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
    fat: float = 0.0


class World:
    def __init__(self):
        self.organisms: List[Organism] = []
        self.resources: Dict[Tuple[int, int], str] = {}
        self.tick = 0
        self.next_id = 0
        self.shift_timer = random.randint(*ENV_SHIFT_INTERVAL)
        self.events: List[str] = []
        self.extinction_log: List[str] = []
        self.pop_history: List[int] = []
        self.fitness_history: List[float] = []
        self._extinction_log_initialized = False
        self.max_gen_ever = 0
        self.max_age_ever = 0
        self.min_pop_ever = INITIAL_ORGANISMS
        self.genes_found: List[Set[int]] = [set(range(g[1], g[2]+1)) for g in GENES]
        self.genes_lost: List[str] = []
        self.migration_timer = random.randint(*MIGRATION_INTERVAL)
        self.fossil_lineages: List[Tuple[int, ...]] = []
        self.all_genomes_seen: Set[Tuple[int, ...]] = set()
        self.recorded_fossils: Set[Tuple[int, ...]] = set()
        self.fossil_count = 0
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

    def _log_extinction(self, etype: str, pop: int):
        if not self._extinction_log_initialized:
            with open(EXTINCTION_LOG_FILE, "w") as f:
                f.write("tick,event,pop,max_gen,season\n")
            self._extinction_log_initialized = True
        with open(EXTINCTION_LOG_FILE, "a") as f:
            f.write(f"{self.tick},{etype},{pop},{self.max_gen_ever},{self.season}\n")

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
        self.all_genomes_seen.add(tuple(genome))
        self.next_id += 1

    def _temperature_at(self, y: int) -> float:
        t = 1.0 - y / (HEIGHT - 1)
        if self.season == "summer":
            t = min(1.0, t + 0.25)
        else:
            t = max(0.0, t - 0.25)
        return t

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

    def _nearest_organism(self, org: Organism, dead: Set[int]) -> Optional[Organism]:
        sense = org.genome[1]
        best_d = sense + 1
        best = None
        for other in self.organisms:
            if other is org or other.id in dead:
                continue
            d = abs(other.x - org.x) + abs(other.y - org.y)
            if d < best_d:
                best_d = d
                best = other
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

            diet = org.genome[8]

            # --- SENSE & MOVE ---
            speed = org.genome[0] + 1
            wander = org.genome[4]

            # Choose target: organism (carnivore/omnivore) or resource (herbivore/omnivore)
            target = None
            if diet >= 1:
                prey = self._nearest_organism(org, dead)
                if diet == 1:
                    # Pure carnivore: hunt organisms only
                    target = (prey.x, prey.y) if prey else None
                elif diet == 2:
                    # Omnivore: chase closest of organism or resource
                    res_target = self._nearest_resource(org)
                    if prey and res_target:
                        p_dist = abs(prey.x - org.x) + abs(prey.y - org.y)
                        r_dist = abs(res_target[0] - org.x) + abs(res_target[1] - org.y)
                        target = (prey.x, prey.y) if p_dist <= r_dist else res_target
                    elif prey:
                        target = (prey.x, prey.y)
                    elif res_target:
                        target = res_target
            else:
                target = self._nearest_resource(org)

            # --- FEAR RESPONSE: herbivores flee from nearby predators ---
            if diet == 0 and org.genome[1] > 0:
                sense = org.genome[1]
                nearest_pred = None
                min_d = sense + 1
                for other in self.organisms:
                    if other is org or other.id in dead or other.genome[8] < 1:
                        continue
                    d = abs(other.x - org.x) + abs(other.y - org.y)
                    if d < min_d:
                        min_d = d
                        nearest_pred = other
                if nearest_pred and random.random() < 0.6:
                    fx = org.x + (org.x - nearest_pred.x)
                    fy = org.y + (org.y - nearest_pred.y)
                    target = (max(0, min(WIDTH-1, fx)), max(0, min(HEIGHT-1, fy)))
                    org.energy -= 0.05

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
                if diet == 0:
                    met_bonus += 0.3
                elif diet == 1 and rtype == "corpse":
                    met_bonus += 0.4
                org.energy += base_val * met_bonus

            # --- METABOLIC COST ---
            base_cost = SUMMER_BASE_COST if self.season == "summer" else WINTER_BASE_COST
            base_cost += org.genome[3] * 0.15
            speed_cost = org.genome[0] * 0.02
            sense_cost = org.genome[1] * 0.025
            agg_cost = org.genome[2] * 0.015
            if diet == 2:
                agg_cost += 0.02
            tox_cost = org.genome[9] * 0.01
            pref_temp = org.genome[7] / 4.0
            actual_temp = self._temperature_at(org.y)
            thermal_cost = abs(actual_temp - pref_temp) * 0.25
            org.energy -= base_cost + speed_cost + sense_cost + agg_cost + thermal_cost + tox_cost

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

            # --- HUNTING (carnivores actively attack adjacent organisms) ---
            if diet >= 1:
                for other in self.organisms:
                    if other is org or other.id in dead:
                        continue
                    if abs(other.x - org.x) <= 1 and abs(other.y - org.y) <= 1:
                        a = org.genome[2]
                        b = other.genome[2]
                        # Pack hunting: nearby predators coordinate
                        pack_count = 0
                        for packmate in self.organisms:
                            if packmate is org or packmate.id in dead or packmate.genome[8] < 1:
                                continue
                            if abs(packmate.x - other.x) <= 2 and abs(packmate.y - other.y) <= 2:
                                pack_count += 1
                        pack_bonus = 1.0 + pack_count * 0.15
                        atk_mult = (1.5 if diet == 1 else 1.0) * pack_bonus
                        # Prey camouflage: hue matching temperature zone defends
                        prey_hue = other.genome[5]
                        prey_y_zone = int(5 * (1.0 - other.y / (HEIGHT - 1)))
                        camo = 1.0 - abs(prey_hue - prey_y_zone) / 5.0
                        # Herding defense: nearby prey protect each other
                        herd_count = 0
                        for herdmate in self.organisms:
                            if herdmate is other or herdmate.id in dead or herdmate.genome[8] >= 1:
                                continue
                            if abs(herdmate.x - other.x) <= 2 and abs(herdmate.y - other.y) <= 2:
                                herd_count += 1
                        herd_bonus = 1.0 + herd_count * 0.15
                        org_power = max(0.1, org.energy) * (a + 1) / 4 * atk_mult
                        other_power = max(0.1, other.energy) * (b + 1) / 4 * (1.0 + camo * 0.5) * herd_bonus
                        total = org_power + other_power
                        if random.random() < org_power / total:
                            gain = other.energy * 0.6
                            if diet == 1:
                                gain *= 1.3
                            org.energy += gain
                            # Prey toxin damages predator
                            tox = other.genome[9]
                            if tox > 0:
                                org.energy -= tox * 0.4
                            dead.add(other.id)
                            break  # satiated for this tick
                        else:
                            # Failed hunt — prey fights back, predator injured
                            org.energy -= other.energy * 0.2
                            if org.energy <= 0:
                                dead.add(org.id)
                            break

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

            # --- FAT METABOLISM: store excess energy, draw during scarcity ---
            fat_cap = 1.0 + org.genome[3] * 1.5
            if org.energy > 2.0 and org.fat < fat_cap:
                store = min(org.energy - 2.0, fat_cap - org.fat, 0.5)
                org.fat += store
                org.energy -= store
            elif org.energy < 0.5 and org.fat > 0:
                draw = min(org.fat, 1.0)
                org.fat -= draw
                org.energy += draw * 0.7

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
        # Clean up diseased IDs for dead organisms
        self.diseased &= {o.id for o in self.organisms}
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

        # Extinction-level event tracking
        if pop_after < self.min_pop_ever:
            self.min_pop_ever = pop_after
            if pop_after <= 3:
                entry = f"⚠ CRITICAL: pop={pop_after} at T={self.tick} (max_gen={self.max_gen_ever})"
                self.extinction_log.append(entry)
                self.events.append(f"⚠ Only {pop_after} organisms remain!")
                self._log_extinction("CRITICAL", pop_after)
            elif pop_after <= 10:
                entry = f"📉 Bottleneck: pop={pop_after} at T={self.tick} (max_gen={self.max_gen_ever})"
                self.extinction_log.append(entry)
                self.events.append(f"📉 Population bottleneck: {pop_after}")
                self._log_extinction("BOTTLENECK", pop_after)

        # Radiation spike — injects genetic variation
        if self.tick > 50 and random.random() < 0.008:
            n_mutated = 0
            for org in self.organisms:
                i = random.randint(0, len(GENES) - 1)
                g_min, g_max = GENES[i][1], GENES[i][2]
                delta = random.choice([-1, 1])
                old = org.genome[i]
                org.genome[i] = max(g_min, min(g_max, org.genome[i] + delta))
                if org.genome[i] != old:
                    n_mutated += 1
            if n_mutated > 0:
                self.events.append(f"☢ Radiation spike — {n_mutated} organisms mutated")

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

        # --- FOSSIL RECORD: track extinct lineages ---
        current_genomes = {tuple(o.genome) for o in self.organisms}
        newly_extinct = self.all_genomes_seen - current_genomes - self.recorded_fossils
        if newly_extinct:
            for g in newly_extinct:
                self.recorded_fossils.add(g)
                self.fossil_lineages.append(g)
            self.fossil_count += len(newly_extinct)
            self.events.append(
                f"🦴 {len(newly_extinct)} lineage(s) fossilized "
                f"(total: {self.fossil_count})"
            )

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
                diet = org.genome[8]
                if diet == 1:
                    glyph = "⬢"
                    color = COLORS[org.genome[5] % len(COLORS)]
                elif diet == 2:
                    glyph = "⬟"
                    color = COLORS[org.genome[5] % len(COLORS)]
                else:
                    glyph = GLYPH_SET[org.genome[5] % len(GLYPH_SET)]
                    color = COLORS[org.genome[5] % len(COLORS)]
                if org.id == sentinel_id and org.generation > 0:
                    grid[org.y][org.x] = f"{BOLD}\033[47m\033[30m{glyph}{RESET}"
                elif org.id in self.diseased:
                    grid[org.y][org.x] = f"{BOLD}\033[41m{color}{glyph}{RESET}"
                elif org.fat > 1.5:
                    grid[org.y][org.x] = f"{BOLD}\033[43m{color}{glyph}{RESET}"
                elif org.genome[9] > 0:
                    grid[org.y][org.x] = f"{BOLD}\033[45m{color}{glyph}{RESET}"
                elif org.energy > 7:
                    grid[org.y][org.x] = f"{BOLD}{color}{glyph}{RESET}"
                elif org.energy > 3:
                    grid[org.y][org.x] = f"{color}{glyph}{RESET}"
                else:
                    grid[org.y][org.x] = f"{DIM}{color}{glyph}{RESET}"

        TEMP_COLORS = ["\033[34m", "\033[32m", "\033[33m", "\033[31m"]
        lines = [f"{BOLD}╔{'═' * WIDTH}╗{RESET}"]
        for y, row in enumerate(grid):
            temp_idx = min(3, int((1.0 - y / (HEIGHT - 1)) * 4))
            tc = TEMP_COLORS[temp_idx]
            lines.append(f"{BOLD}{tc}║{RESET}{''.join(row)}{BOLD}{tc}║{RESET}")
        lines.append(f"{BOLD}╚{'═' * WIDTH}╝{RESET}")

        if self.organisms:
            n = len(self.organisms)
            avg_e = sum(o.energy for o in self.organisms) / n
            max_g = max(o.generation for o in self.organisms)
            avg_spd = sum(o.genome[0] for o in self.organisms) / n
            avg_agg = sum(o.genome[2] for o in self.organisms) / n
            avg_met = sum(o.genome[3] for o in self.organisms) / n
            avg_mut = sum(o.genome[6] for o in self.organisms) / n
            avg_tmp = sum(o.genome[7] for o in self.organisms) / n
            avg_diet = sum(o.genome[8] for o in self.organisms) / n
            avg_tox = sum(o.genome[9] for o in self.organisms) / n
            avg_fat = sum(o.fat for o in self.organisms) / n
            n_herb = sum(1 for o in self.organisms if o.genome[8] == 0)
            n_carn = sum(1 for o in self.organisms if o.genome[8] == 1)
            n_omni = sum(1 for o in self.organisms if o.genome[8] == 2)
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
            avg_e = max_g = avg_spd = avg_agg = avg_met = avg_mut = avg_tmp = avg_diet = avg_tox = avg_fat = species = n = 0
            n_herb = n_carn = n_omni = 0
            dominant_key = ()
            dominant_pct = 0
            dominant_glyph = "?"

        lines.append(
            f"  Pop:{n:4d}  ⚡:{avg_e:.1f}  Gen:{max_g:3d}  "
            f"Sp:{species:2d}  Fos:{self.fossil_count:4d}  "
            f"H:{n_herb} C:{n_carn} O:{n_omni}  Spd:{avg_spd:.1f}  "
            f"Agg:{avg_agg:.1f}  Met:{avg_met:.1f}  "
            f"μMut:{avg_mut:.1f}  Tm:{avg_tmp:.1f}  D:{avg_diet:.1f}  Tx:{avg_tox:.1f}  "
            f"Ft:{avg_fat:.2f}  "
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
            labels = ["spd", "sen", "agg", "met", "wnd", "hue", "mut", "tmp", "die", "tox"]
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
                f"\033[0m sentinel: [{g[0]} {g[1]} {g[2]} {g[3]} {g[4]} {g[5]} {g[6]} {g[7]} {g[8]} {g[9]}]"
                f"  gen={sentinel.generation}  age={sentinel.age}  ⚡={sentinel.energy:.1f}{tag}"
            )

        # Events (last 3)
        self.events = self.events[-3:]
        for ev in self.events:
            lines.append(f"  {ev}")
        lines.append(
            "  " + " ".join(
                f"{COLORS[i]}{GLYPH_SET[i]}{RESET}{GENES[i][0]}"
                for i in range(min(len(GENES), len(GLYPH_SET)))
            ) + f"  {BOLD}·{RESET}food {BOLD}★{RESET}bounty {BOLD}✿{RESET}corpse"
        )
        return "\n".join(lines)


def main():
    world = World()
    print("\033c", end="")
    interrupted = False
    try:
        while world.organisms:
            print(world.render())
            world.step()
            time.sleep(TICK_RATE)
            print("\033[H", end="")
    except KeyboardInterrupt:
        interrupted = True

    total_extinct = sum(
        len(set(range(g[1], g[2]+1)) - world.genes_found[i])
        for i, g in enumerate(GENES)
    )
    print(f"\n\n{'═' * 40}")
    if not world.organisms:
        print(f"  ✦  All organisms went extinct at T={world.tick}")
        world._log_extinction("TOTAL_EXTINCTION", 0)
    elif interrupted:
        print(f"  ✦  Evolution halted after {world.tick} ticks")
    print(f"  Pop: {len(world.organisms)}  "
          f"Generations: {world.max_gen_ever}  "
          f"Max age: {world.max_age_ever}  "
          f"Min pop: {world.min_pop_ever}")
    print(f"  Species now: {len({tuple(o.genome) for o in world.organisms})}  "
          f"Infected: {len(world.diseased)}  "
          f"Fossil lineages: {world.fossil_count}")
    if total_extinct:
        print(f"  Gene values lost to extinction: {total_extinct}")
    if world.extinction_log:
        print(f"\n  {'─' * 36}")
        print(f"  Extinction events ({len(world.extinction_log)} total):")
        for entry in world.extinction_log[-5:]:
            print(f"  {entry}")
    print(f"\n  Extinction log written to {EXTINCTION_LOG_FILE}")
    if not world.organisms:
        print(f"\n  Extinction cause: Last {len(world.organisms)} organism(s)")
        if world.organisms:
            last = world.organisms[0]
            print(f"  Genome: [{last.genome[0]} {last.genome[1]} {last.genome[2]} "
                  f"{last.genome[3]} {last.genome[4]} {last.genome[5]} "
                  f"{last.genome[6]} {last.genome[7]} {last.genome[8]} {last.genome[9]}]")
            print(f"  Age: {last.age}  Energy: {last.energy:.1f}")
    print(f"{'═' * 40}")


if __name__ == "__main__":
    main()
