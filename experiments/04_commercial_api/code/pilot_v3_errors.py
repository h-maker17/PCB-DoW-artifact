"""Credential-safe prospective error projection; never stringify an exception."""


def safe_error(exc, phase):
    permitted = {'DNS', 'TCP_CONNECT', 'TLS_HANDSHAKE', 'SSL_CONTEXT_SETUP', 'HTTP_OPENER_SETUP', 'REQUEST_BUILD',
                 'PHYSICAL_GATE', 'TRANSPORT_MARK', 'HTTPS_OPEN', 'BODY_READ', 'RAW_ARCHIVE', 'RESPONSE_ANALYSIS',
                 'CADENCE', 'PREFLIGHT', 'CAPTURE', 'ENTRY'}
    if phase not in permitted: raise ValueError('INVALID_ERROR_PHASE')
    chain = [];
    seen = set();
    current = exc
    while isinstance(current, BaseException) and id(current) not in seen and len(chain) < 5:
        seen.add(id(current));
        item = {'exception_class': type(current).__name__}
        for name in ('errno', 'winerror', 'verify_code', 'code'):
            v = getattr(current, name, None)
            if type(v) is int: item[name] = v
        chain.append(item)
        reason = getattr(current, 'reason', None)
        current = reason if isinstance(reason, BaseException) else current.__cause__ or current.__context__
    return {'failure_phase_observed': phase, 'exception_chain': chain, 'messages_retained': False,
            'headers_retained': False, 'credential_fields_retained': False, 'historical_cause_inferred': False}
