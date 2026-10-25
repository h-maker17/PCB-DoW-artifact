from common import *
import evasion, token_mass, h3, traceback

if __name__ == '__main__':
    try:
        assert read(R / 'evidence/H2-dataset-state.json')['status'] == 'complete-awaiting-detector-freeze'
        assert (R / 'config/frozen-detector.json').exists()
        save(R / 'evidence/formal-state.json',
             {'stage': 'H2-evasion-running', 'H1': 'complete', 'H2_dataset': 'complete', 'detector': 'frozen'})
        evasion.main();
        token_mass.main();
        h3.main()
        save(R / 'evidence/formal-state.json',
             {'stage': 'H1-H3-complete-awaiting-analysis', 'H1': 'complete', 'H2': 'complete', 'H3': 'complete'})
    except Exception as e:
        save(R / 'evidence/formal-failure.json', {'error': str(e), 'traceback': traceback.format_exc()});
        raise
