"""Bounded, symmetry-preserving lattice update for OAW's local PyWPEM engine.

Optimize the selected Bragg-angle residuals. Reject invalid metrics, inaccessible
reflections and non-improving steps; never coerce complex spacings to real values.
"""
import numpy as np
from scipy.optimize import least_squares


def angles(cell, hkl, wavelength):
    cell = np.asarray(cell, dtype=float)
    if not np.all(np.isfinite(cell)) or np.any(cell[:3] <= 0) or np.any(cell[3:] <= 0) or np.any(cell[3:] >= 180):
        raise ValueError('Invalid cell parameters')
    ca, cb, cg = np.cos(np.deg2rad(cell[3:]))
    correlation = np.array([[1, cg, cb], [cg, 1, ca], [cb, ca, 1]])
    if np.linalg.eigvalsh(correlation).min() <= 1e-8:
        raise ValueError('Cell metric must be positive definite')
    metric = correlation * np.outer(cell[:3], cell[:3])
    reciprocal = np.linalg.inv(metric)
    invd2 = np.einsum('ni,ij,nj->n', hkl, reciprocal, hkl)
    argument = np.sqrt(invd2)[:, None] * np.asarray(wavelength)[None, :] / 2
    if not np.all(np.isfinite(argument)) or np.any(argument <= 0) or np.any(argument >= 1):
        raise ValueError('Reflection outside Bragg domain')
    return np.rad2deg(2 * np.arcsin(argument)).ravel()


def optimize_cell(system, old, target, subset, low, high, h, k, l, cell, wavelength, fixed=False):
    original = np.asarray(cell, dtype=float)
    if fixed:
        return (*original.tolist(), list(old))
    hkl = np.column_stack((h, k, l)).astype(float)
    initial = angles(original, hkl, wavelength)
    old, target = np.asarray(old, dtype=float).ravel(), np.asarray(target, dtype=float).ravel()
    if initial.shape != target.shape or old.shape != target.shape or not np.all(np.isfinite(target)):
        raise ValueError(f'Invalid Bragg targets: initial={initial.shape}, old={old.shape}, target={target.shape}, finite={np.all(np.isfinite(target))}')
    groups = {1: [(0,1,2)], 2: [(0,1),(2,)], 3: [(0,1),(2,)],
              4: [(0,),(1,),(2,)], 5: [(0,1,2),(3,4,5)],
              6: [(0,),(1,),(2,),(4,)], 7: [(i,) for i in range(6)]}[system]
    x0 = np.array([original[g[0]] for g in groups])
    def expand(x):
        value = original.copy()
        for group, v in zip(groups, x): value[list(group)] = v
        return value
    if not np.allclose(expand(x0), original, atol=1e-6, rtol=1e-6):
        raise ValueError('Cell does not satisfy declared crystal system')
    rays = len(wavelength)
    valid = ((target >= low) & (target <= high)).reshape(-1, rays).all(axis=1)
    order = np.argsort(((target-old)**2).reshape(-1, rays).max(axis=1), kind='stable')
    chosen = [i for i in order if valid[i]][:max(0, int(subset))]
    # Do not refine more independent parameters than independent reflection positions.
    if len(chosen) < len(groups): return (*original.tolist(), initial.tolist())
    indices = np.array([i*rays+j for i in chosen for j in range(rays)])
    def residual(x):
        try: return angles(expand(x), hkl, wavelength)[indices] - target[indices]
        except (ValueError, np.linalg.LinAlgError): return np.full(len(indices), 1e4)
    widths = np.array([abs(v)*.01 if g[0] < 3 else .5 for g,v in zip(groups,x0)])
    lower = np.maximum(x0-widths, [1e-6 if g[0]<3 else .01 for g in groups])
    upper = np.minimum(x0+widths, [np.inf if g[0]<3 else 179.99 for g in groups])
    fit = least_squares(residual, x0, bounds=(lower, upper), x_scale=widths,
                        max_nfev=100, ftol=1e-10, xtol=1e-10, gtol=1e-10)
    try: proposed = angles(expand(fit.x), hkl, wavelength)
    except (ValueError, np.linalg.LinAlgError): return (*original.tolist(), initial.tolist())
    if not fit.success or np.sum((proposed[indices]-target[indices])**2) > np.sum((initial[indices]-target[indices])**2):
        return (*original.tolist(), initial.tolist())
    return (*expand(fit.x).tolist(), proposed.tolist())


def stable_peak_center(numerators, denominators, previous):
    n, d = np.asarray(numerators), np.asarray(denominators)
    if not np.all(np.isfinite(n)) or not np.all(np.isfinite(d)):
        raise ValueError('Nonfinite EM responsibilities')
    if np.any(d <= np.finfo(float).tiny):
        return previous
    center = np.sum(n / d)
    if not np.isfinite(center):
        raise ValueError('Nonfinite EM peak center')
    return float(center)
