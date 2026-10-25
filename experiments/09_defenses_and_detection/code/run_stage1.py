from common import *
import h1, h2_dataset, traceback

if __name__ == '__main__':
    try:
        assert read(R / 'evidence/smoke-audit.json')['passed']
        assert (R / 'config/stage-gate-approved.md').exists()
        save(R / 'evidence/formal-state.json', {'stage': 'H1-running', 'approved': True, 'formal_started': True})
        h1.main();
        save(R / 'evidence/formal-state.json',
             {'stage': 'H2-dataset-running', 'H1': 'complete', 'formal_started': True})
        h2_dataset.main();
        save(R / 'evidence/formal-state.json',
             {'stage': 'H2-awaiting-local-detector-freeze', 'H1': 'complete', 'H2_dataset': 'complete',
              'formal_started': True})
    except Exception as e:
        save(R / 'evidence/formal-failure.json', {'error': str(e), 'traceback': traceback.format_exc()});
        raise
