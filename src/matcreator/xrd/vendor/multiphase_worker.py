"""Private JSON-lines worker, executed only by the configured scientific Python."""
import contextlib
import json
import os
from pathlib import Path
import sys


def main():
    os.environ['OMP_NUM_THREADS'] = '1'
    os.environ['OPENBLAS_NUM_THREADS'] = '1'
    os.environ['MPLBACKEND'] = 'Agg'
    run = Path(sys.argv[1]).resolve()
    payload = json.loads((run / 'multiphase-input.json').read_text(encoding='utf-8'))
    from multiphase_science import evaluate, initial_combinations, propose_bo, decision_evidence
    for line in sys.stdin:
        try:
            request = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                if request['command'] == 'initial':
                    value = initial_combinations(payload)
                elif request['command'] == 'decision_evidence':
                    value = decision_evidence(payload, run / 'llm')
                elif request['command'] == 'propose_bo':
                    value = propose_bo(payload, request['evaluated'])
                elif request['command'] == 'evaluate':
                    arm = request['arm']
                    if arm not in {'llm', 'bo'}:
                        raise ValueError('Unknown evaluation arm')
                    value = evaluate(payload, request['candidate_ids'], run / arm)
                elif request['command'] == 'pywpem_review':
                    import subprocess
                    root = Path(os.environ.get('OAW_XRD_ROOT', str(run.parent.parent)))
                    # PyWPEM spawns multiprocessing children whose stdout bypasses
                    # redirect_stdout. Keep their banners off the JSON-lines pipe.
                    request_path = run / 'pywpem-request.json'
                    request_path.write_text(json.dumps({'combinations': request['combinations'],
                        'iterations': request.get('iterations', 20), 'drop_one': request.get('drop_one', False), 'root': str(root)}), encoding='utf-8')
                    subprocess.run([sys.executable, '-u', str(Path(__file__).with_name('multiphase_wpem.py')), str(run)],
                                   stdin=subprocess.DEVNULL, stdout=sys.stderr, stderr=sys.stderr, check=True)
                    value = json.loads((run / 'pywpem-review/review.json').read_text(encoding='utf-8'))
                else:
                    raise ValueError('Unknown worker command')
            result = {'ok': True, 'value': value}
        except Exception as exc:
            result = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == '__main__':
    main()
