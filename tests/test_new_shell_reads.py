# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import session_store as S
from pprint import pprint

print('home:', S.home())
root = S.sessions_root()
print('sessions_root:', root)
print('exists:', bool(root) and os.path.isdir(root))
try:
    ss = S.list_sessions()
    norm = [s for s in ss if s.get('id') != '_degraded']
    print('list count:', len(norm))
    for s in norm[:3]:
        print('  -', s.get('project'), '|', s.get('title'), '|', s.get('updated_at'), '| turns:', s.get('turns'))
    try:
        pi = S.providers_info()
        print('providers keys:', sorted(pi.keys()))
        print('route:', pi.get('route'), '| source:', pi.get('source'))
        if pi.get('providers'):
            print('providers:', [p['id'] for p in pi['providers']][:8])
        if pi.get('default_model'):
            print('default_model:', pi['default_model'])
    except Exception as e:
        print('providers ERR:', type(e).__name__, e)
except Exception as e:
    print('ERR:', type(e).__name__, e)