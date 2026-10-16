"""Read PyWPEM's exported symmetric peak components on the original grid."""
from pathlib import Path


def phase_components(directory, combo, labels, points, calculated, preview):
    import numpy as np
    import pandas as pd
    directory = Path(directory) / 'DecomposedComponents'
    x = points[:, 0]
    curves = []
    for index, identifier in enumerate(combo):
        filename = f'System{index}.csv' if len(combo) > 1 else 'sub_peaks.csv'
        table = pd.read_csv(directory / filename)
        y = np.zeros_like(x)
        for row in table.itertuples():
            # The engine exports Ai=0/0 for an exactly zero-area peak.
            # Its contribution is identically zero; nonzero invalid peaks still fail.
            if np.isfinite(row.wi) and row.wi == 0:
                continue
            values = [row.wi, row.Ai, row.mu_i, row.L_gamma_i, row.G_sigma2_i]
            if not np.all(np.isfinite(values)) or row.wi < 0 or not 0 <= row.Ai <= 1 or row.L_gamma_i <= 0 or row.G_sigma2_i <= 0:
                raise ValueError('Invalid PyWPEM component parameters')
            delta = x-row.mu_i
            y += row.wi * (row.Ai*row.L_gamma_i/(np.pi*(delta**2+row.L_gamma_i**2)) +
                          (1-row.Ai)*np.exp(-delta**2/(2*row.G_sigma2_i))/np.sqrt(2*np.pi*row.G_sigma2_i))
        curves.append(y)
    background = np.loadtxt(directory / 'upbackground.csv', delimiter=',')
    if background.shape != points.shape or not np.all(np.isfinite(background)) or not np.allclose(background[:, 0], x, rtol=0, atol=1e-7):
        raise ValueError('Component background grid mismatch')
    # PyWPEM rounds values in its component export. Reject mismatches beyond
    # export precision, including an unsupported asymmetric-profile export.
    residual = calculated-sum(curves)-background[:, 1]
    relative_error = float(np.max(np.abs(residual))/max(float(np.max(np.abs(calculated))), 1.))
    if relative_error > 1e-5:
        raise ValueError('PyWPEM components do not reconstruct the final profile')
    return ([{'candidate_id': identifier, 'label': label,
              'points': np.column_stack((x[preview], y[preview])).tolist()}
             for identifier, label, y in zip(combo, labels, curves)],
            {'maximum_relative_closure_error': relative_error, 'tolerance': 1e-5,
             'method': 'PyWPEM exported symmetric pseudo-Voigt components plus final background'})
