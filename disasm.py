#!/usr/bin/env python3
"""Disassemble syntropy RV32I genomes from a search.py --output JSON file.

Each result's ``metrics.best_genome`` is a list of 32-bit instruction words.
Entries written by older versions (opcode-triplet ``best_vm`` strings only) have
no ``best_genome`` and are skipped with a warning.
"""

import argparse
import json
import sys

import syntropy


def disasm_genome(genome, name="vm", score=0, config="", file=None):
    out = file or sys.stdout
    words = [int(w) & 0xFFFFFFFF for w in genome]
    print(f"# {name}  {config}  score={score}", file=out)
    print(f"# {len(words)} RV32I instructions, regs={syntropy.NUM_REGS}, "
          f"{syntropy.VM_MEM_SIZE}-slot local mem", file=out)
    for i, w in enumerate(words):
        asm = syntropy.disasm_rv32(w, addr=i * 4)
        try:
            py = syntropy.rv32_to_py(w)
        except Exception as exc:  # decoder gaps must not abort the listing
            py = f"# <untranslatable: {type(exc).__name__}>"
        print(f"{i * 4:04x}: {w:08x}  {asm:<28s} | {py}", file=out)


def load_entries(data, min_score=0):
    entries, skipped = [], 0
    for r in data.get("results", []):
        m = r.get("metrics", {})
        if m.get("best_genome"):
            if r.get("score", 0) >= min_score:
                entries.append(r)
        elif m.get("best_vm"):
            skipped += 1
    entries.sort(key=lambda e: e.get("score", 0), reverse=True)
    return entries, skipped


def main(argv=None):
    ap = argparse.ArgumentParser(description="Disassemble search.json genomes")
    ap.add_argument("input", nargs="?", default="search.json", help="search results JSON")
    ap.add_argument("-n", type=int, default=0, help="show top N only (0 = all with genomes)")
    ap.add_argument("--min-score", type=int, default=0, help="minimum score threshold")
    args = ap.parse_args(argv)

    with open(args.input) as f:
        data = json.load(f)

    entries, skipped = load_entries(data, args.min_score)
    if skipped:
        print(f"warning: skipped {skipped} legacy entr{'y' if skipped == 1 else 'ies'} "
              f"without best_genome (pre-RV32 format)", file=sys.stderr)
    if args.n:
        entries = entries[:args.n]

    for r in entries:
        m = r["metrics"]
        config = f"E={r['max_energy']} M={r['max_movement_speed']} R={r['n_registers']}"
        print()
        print("=" * 74)
        print(f"  {m['ticks']:>5} ticks  {config}  score={r['score']}")
        print("=" * 74)
        disasm_genome(m["best_genome"], name="trial", score=r["score"], config=config)


if __name__ == "__main__":
    main()
