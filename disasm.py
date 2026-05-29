#!/usr/bin/env python3
"""Disassemble syntropy VM genomes from search.json into Python code."""

import json
import sys
import syntropy
from syntropy import Op, Action, NUM_REGS

OP_NAMES = ["NOP","MOV","ADD","SUB","MUL","DIV","JMP","JZ","JG","JL",
            "SENSE","ACT","PUSH","POP","CALL","RET","HALT","RAND","ENERGY",
            "MOD","CMP","AND","OR","XOR","NOT","IND","MIN","MAX","ABS","NEG",
            "DUP","JNE","SWAP","GEN","PICK","DEPTH","PC","SETPC","SQRT","EXP",
            "TICK","DROP","OVER","SHL","SHR","BIT","MLOAD","MSTORE","GLOAD","GSTORE"]

ACTION_NAMES = [
    "MOVE", "ATTACK", "EAT", "REPRODUCE", "REST", "SOUND", "EMIT", "SYMEMIT",
]

def _target(byte_val, glen):
    return (byte_val % max(3, glen)) // 3

def parse_vm_string(s):
    """Parse 'gen=9 age=1 len=72 vm=[GSTORE(0,60) POP(126,181) ...]' into metadata + decoded tokens."""
    meta = {}
    # extract gen, age, len
    parts = s.split("vm=[")
    head = parts[0].strip()
    for token in head.split():
        if "=" in token:
            k, v = token.split("=")
            meta[k] = v
    # extract instructions
    body = parts[1].rstrip("]")
    tokens = body.split(") ")
    tokens = [t.strip() + ")" for t in tokens if t.strip()]
    tokens[-1] = tokens[-1].rstrip(")") + ")"
    return meta, tokens

def genome_from_decoded(tokens):
    op_map = {name: i for i, name in enumerate(OP_NAMES)}
    g = []
    for token in tokens:
        name, rest = token.split("(")
        a1, a2 = rest.strip(")").split(",")
        g.append(op_map[name])
        g.append(int(a1))
        g.append(int(a2))
    return g

def disasm_genome(genome, name="vm", score=0, config=""):
    glen = len(genome)
    ninst = glen // 3
    lines = []

    for i in range(ninst):
        off = i * 3
        raw_op = int(genome[off]) % Op.TOTAL
        a1 = int(genome[off + 1]) % 256
        a2 = int(genome[off + 2]) % 256
        opname = OP_NAMES[raw_op]
        ridx = a1 % NUM_REGS
        rv_reg = a1 % NUM_REGS
        v_str = f"{a2 / 16.0:.2f}" if a2 >= 64 else f"r[{a2 % NUM_REGS}]"

        if raw_op == Op.NOP:            py = "nop"
        elif raw_op == Op.MOV:          py = f"r[{ridx}] = {v_str}"
        elif raw_op == Op.ADD:          py = f"r[{ridx}] += {v_str}"
        elif raw_op == Op.SUB:          py = f"r[{ridx}] -= {v_str}"
        elif raw_op == Op.MUL:          py = f"r[{ridx}] *= clamp({v_str}, -50, 50)"
        elif raw_op == Op.DIV:          py = f"r[{ridx}] /= {v_str}  # guard: div by ~0"
        elif raw_op == Op.JMP:          py = f"pc = {_target(a1, glen)}"
        elif raw_op == Op.JZ:           py = f"if abs(r[{rv_reg}]) < 0.001: pc = {_target(a2, glen)}"
        elif raw_op == Op.JG:           py = f"if r[{rv_reg}] > 0: pc = {_target(a2, glen)}"
        elif raw_op == Op.JL:           py = f"if r[{rv_reg}] < 0: pc = {_target(a2, glen)}"
        elif raw_op == Op.JNE:          py = f"if abs(r[{rv_reg}]) >= 0.001: pc = {_target(a2, glen)}"
        elif raw_op == Op.SENSE:        py = f"r[{ridx}] = sense[{a1 % syntropy.NUM_SENSORS}]"
        elif raw_op == Op.ACT:          py = f"yield '{ACTION_NAMES[a1 % Action.TOTAL]}'"
        elif raw_op == Op.PUSH:         py = f"stack.push(int(r[{rv_reg}]))"
        elif raw_op == Op.POP:          py = f"r[{ridx}] = stack.pop()"
        elif raw_op == Op.CALL:         py = f"stack.push(pc); pc = {_target(a1, glen)}"
        elif raw_op == Op.RET:          py = "pc = stack.pop() if stack else break"
        elif raw_op == Op.HALT:         py = "break"
        elif raw_op == Op.RAND:         py = f"r[{ridx}] = random() * 10.0"
        elif raw_op == Op.ENERGY:       py = f"r[{ridx}] = budget - used"
        elif raw_op == Op.MOD:          py = f"r[{ridx}] = r[{rv_reg}] % {v_str}  # guard"
        elif raw_op == Op.CMP:          py = f"r[{ridx}] = 1 if r[{rv_reg}] > {v_str} else -1 if r[{rv_reg}] < {v_str} else 0"
        elif raw_op == Op.AND:          py = f"r[{ridx}] = int(r[{rv_reg}]) & int({v_str})"
        elif raw_op == Op.OR:           py = f"r[{ridx}] = int(r[{rv_reg}]) | int({v_str})"
        elif raw_op == Op.XOR:          py = f"r[{ridx}] = int(r[{rv_reg}]) ^ int({v_str})"
        elif raw_op == Op.NOT:          py = f"r[{ridx}] = ~int(r[{rv_reg}])"
        elif raw_op == Op.IND:          py = f"r[{ridx}] = r[int({v_str}) % {NUM_REGS}]"
        elif raw_op == Op.MIN:          py = f"r[{ridx}] = min(r[{rv_reg}], {v_str})"
        elif raw_op == Op.MAX:          py = f"r[{ridx}] = max(r[{rv_reg}], {v_str})"
        elif raw_op == Op.ABS:          py = f"r[{ridx}] = abs(r[{rv_reg}])"
        elif raw_op == Op.NEG:          py = f"r[{ridx}] = -r[{rv_reg}]"
        elif raw_op == Op.DUP:          py = "if stack: stack.append(stack[-1])"
        elif raw_op == Op.SWAP:         py = f"r[{ridx}], r[{a2 % NUM_REGS}] = r[{a2 % NUM_REGS}], r[{ridx}]"
        elif raw_op == Op.GEN:          py = f"r[{a2 % NUM_REGS}] = genome[{a1 % max(1, glen)}]"
        elif raw_op == Op.PICK:         py = f"r[{a2 % NUM_REGS}] = stack[-(1+{a1}%len(stack))] if stack else 0"
        elif raw_op == Op.DEPTH:        py = f"r[{a2 % NUM_REGS}] = len(stack)"
        elif raw_op == Op.PC:           py = f"r[{a2 % NUM_REGS}] = pc * 3"
        elif raw_op == Op.SETPC:        py = f"pc = (int(abs(r[{rv_reg}])) % max(3,{glen})) // 3"
        elif raw_op == Op.SQRT:         py = f"r[{a2 % NUM_REGS}] = sqrt(max(0, r[{rv_reg}]))"
        elif raw_op == Op.EXP:          py = f"r[{a2 % NUM_REGS}] = exp(clamp(r[{rv_reg}], -10, 10))"
        elif raw_op == Op.TICK:         py = f"r[{a2 % NUM_REGS}] = tick"
        elif raw_op == Op.DROP:         py = "if stack: stack.pop()"
        elif raw_op == Op.OVER:         py = "if len(stack) >= 2: stack.append(stack[-2])"
        elif raw_op == Op.SHL:          py = f"r[{ridx}] = int(r[{rv_reg}]) << {a2 % 16}"
        elif raw_op == Op.SHR:          py = f"r[{ridx}] = int(r[{rv_reg}]) >> {a2 % 16}"
        elif raw_op == Op.BIT:          py = f"r[{a2 % NUM_REGS}] = 1 if (int(abs(r[{rv_reg}])) >> {a1 & 7}) & 1 else 0"
        elif raw_op == Op.MLOAD:        py = f"r[{ridx}] = mem[{a2 % 16}]"
        elif raw_op == Op.MSTORE:       py = f"mem[{a2 % 16}] = r[{rv_reg}]"
        elif raw_op == Op.GLOAD:        py = f"r[{ridx}] = shared_mem[{a2 % 64}]"
        elif raw_op == Op.GSTORE:       py = f"shared_mem[{a2 % 64}] = r[{rv_reg}]"
        else:                           py = f"# unknown opcode {raw_op}"

        lines.append((i, opname, a1, a2, py))

    print(f"# {name}  {config}  score={score}")
    print(f"# {glen} bytes = {ninst} insns, 4 regs, stack, 16-slot mem, 64-slot shared_mem")
    print(f"def {name}(r, mem, shared_mem, sense, tick, budget):")
    print(f"    stack = []; used = 0; pc = 0")
    print(f"    while pc < {ninst}:")
    print(f"        i = pc; pc += 1")
    for idx, opname, a1, a2, py in lines:
        if opname in ("JMP", "JZ", "JG", "JL", "JNE", "CALL", "ACT", "RET", "HALT"):
            print("")
        print(f"        {py:55s}  # {idx:2d}. {opname}({a1:3d},{a2:3d})")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Disassemble search.json VMs to Python")
    ap.add_argument("input", nargs="?", default="search.json", help="search results JSON")
    ap.add_argument("-n", type=int, default=0, help="show top N only (0 = all with VMs)")
    ap.add_argument("--min-score", type=int, default=0, help="minimum score threshold")
    args = ap.parse_args()

    with open(args.input) as f:
        data = json.load(f)

    entries = []
    for r in data.get("results", []):
        vm = r.get("metrics", {}).get("best_vm", "")
        if vm:
            entries.append(r)
        elif r.get("metrics", {}).get("ticks", 0) >= args.min_score:
            pass  # skip entries without VM

    entries.sort(key=lambda e: e["score"], reverse=True)

    if args.n:
        entries = entries[:args.n]

    for r in entries:
        vm_str = r["metrics"]["best_vm"]
        meta, tokens = parse_vm_string(vm_str)
        genome = genome_from_decoded(tokens)
        config = f"E={r['max_energy']} M={r['max_movement_speed']} R={r['n_registers']}"
        gen_info = f"gen={meta.get('gen','?')} age={meta.get('age','?')}"
        print()
        print("=" * 74)
        print(f"  #{r['metrics']['ticks']:>5} ticks  {config}  {gen_info}  score={r['score']}")
        print("=" * 74)
        disasm_genome(genome, name=f"trial", score=r["score"], config=config)


if __name__ == "__main__":
    main()
