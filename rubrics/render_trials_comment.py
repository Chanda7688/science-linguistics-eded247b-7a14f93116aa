#!/usr/bin/env python3
import sys; sys.path.insert(0,__file__.rsplit("/",1)[0]); from _ashape import rewards
run=sys.argv[1]; k=sys.argv[2] if len(sys.argv)>2 else "?"
rw=rewards(run); solved=sum(1 for r in rw.values() if r>=1); fails=len(rw)-solved
L=["## 🧪 Agent Trials (advisory)","",f"pass@{k}: **{solved} solved · {fails} failed** of {len(rw)} trials.","","| Trial | Reward |","|---|---|"]
for n,r in sorted(rw.items()): L.append(f"| `{n}` | {'✅ '+str(r) if r>=1 else '❌ '+str(r)} |")
L+=["","<sub>Advisory — a maintainer judges the difficulty bar. Not a gate.</sub>"]; print("\n".join(L))
