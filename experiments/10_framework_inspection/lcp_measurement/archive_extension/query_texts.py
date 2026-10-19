"""Query inspection texts only; never modifies archived values or model input."""
def collect_query_texts(runs):
    texts = set()
    for run in runs:
        for arm in run['trace'].values():
            for native in arm['returns']:
                query = native['query']
                if isinstance(query, str):
                    values = (query,)
                elif isinstance(query, list) and all(isinstance(value, str) for value in query):
                    values = query
                else:
                    raise TypeError('native_query_must_be_str_or_flat_list_str')
                # Empty text is not a substring guard. Full nonempty elements remain exact.
                texts.update(value for value in values if value)
    return sorted(texts)
