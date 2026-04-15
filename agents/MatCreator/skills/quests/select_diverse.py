#!/usr/bin/env python3
"""Select top N most diverse frames from a dataset using QUEST entropy."""

import json
import sys
from pathlib import Path
from ase.io import read, write

# Add quests to path
sys.path.insert(0, '/home/ruoyu/miniconda3/envs/deepmd/lib/python3.12/site-packages')

from quests.descriptor import get_descriptors
from quests.entropy import delta_entropy, entropy
import numpy as np

def select_diverse_frames(input_file, n_select, cutoff=5.0, k=32, h=0.015, batch_size=5000):
    """Select top N most diverse frames based on entropy contribution."""
    
    print(f"Reading structures from {input_file}...")
    all_frames = read(input_file, index=':')
    print(f"Total frames: {len(all_frames)}")
    
    # Compute descriptors for all frames
    print("Computing descriptors...")
    all_desc = get_descriptors(all_frames, k=k, cutoff=cutoff, dtype='float32')
    
    # Build atom indices for each frame
    atom_indices = []
    start = 0
    for a in all_frames:
        end = start + len(a)
        atom_indices.append((start, end))
        start = end
    
    # Iteratively select frames with highest entropy contribution
    selected_indices = []
    selected_desc = []
    
    # Start with the first frame
    selected_indices.append(0)
    selected_desc.append(all_desc[atom_indices[0][0]:atom_indices[0][1]])
    
    remaining = list(range(1, len(all_frames)))
    
    print(f"Selecting {n_select} diverse frames...")
    for i in range(1, min(n_select, len(all_frames))):
        if len(remaining) == 0:
            break
            
        # Get descriptors for remaining frames
        remaining_desc = [all_desc[atom_indices[idx][0]:atom_indices[idx][1]] for idx in remaining]
        x = np.vstack(remaining_desc)
        y = np.vstack(selected_desc)
        
        # Compute entropy gain for each remaining frame
        delta = delta_entropy(x, y, h=h, batch_size=batch_size)
        
        # Sum delta per frame
        delta_sums = []
        for idx in remaining:
            s, e = atom_indices[idx]
            delta_sums.append(delta[s-atom_indices[remaining[0]][0]:e-atom_indices[remaining[0]][0]].sum())
        
        # Select frame with highest entropy gain
        best_idx = remaining[np.argmax(delta_sums)]
        selected_indices.append(best_idx)
        selected_desc.append(all_desc[atom_indices[best_idx][0]:atom_indices[best_idx][1]])
        remaining.remove(best_idx)
        
        if (i + 1) % 20 == 0:
            print(f"  Selected {i + 1} frames...")
    
    # Write selected frames
    selected_frames = [all_frames[i] for i in selected_indices]
    output_file = f"diverse_{len(selected_frames)}.extxyz"
    write(output_file, selected_frames)
    
    # Compute final entropy
    final_desc = np.vstack(selected_desc)
    final_entropy = entropy(final_desc, h=h, batch_size=batch_size)
    
    result = {
        "status": "success",
        "input_file": input_file,
        "total_frames": len(all_frames),
        "selected_frames": len(selected_frames),
        "output_file": str(Path(output_file).resolve()),
        "entropy": float(final_entropy),
        "parameters": {"cutoff": cutoff, "k": k, "h": h}
    }
    
    return result

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("input_file", help="Input extxyz file")
    parser.add_argument("--n-select", type=int, required=True, help="Number of frames to select")
    parser.add_argument("--cutoff", type=float, default=5.0)
    parser.add_argument("--k", type=int, default=32)
    parser.add_argument("--h", type=float, default=0.015)
    parser.add_argument("--batch-size", type=int, default=5000)
    args = parser.parse_args()
    
    result = select_diverse_frames(
        args.input_file, 
        args.n_select,
        cutoff=args.cutoff,
        k=args.k,
        h=args.h,
        batch_size=args.batch_size
    )
    print(json.dumps(result, indent=2))
