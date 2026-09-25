"""/account/password: signed-in password change, and admin changes that survive restarts.

2026-09-25: the page was disabled for three defects (admin changes reverted by
ensure_admin on every startup, no rate limit, unescaped HTML). Runs the real
functions from app/auth.py (extracted with ast, as CLAUDE.md prescribes) against
stand-ins for the database layer.

Run:  python3 tests/test_password_page.py
"""
import ast, asyncio, html, time, types
from collections import defaultdict, deque
src = open(__import__('pathlib').Path(__file__).resolve().parent.parent / 'app' / 'auth.py').read(); tree = ast.parse(src)
def seg(name):
    for n in tree.body:
        if getattr(n,'name',None)==name: return ast.get_source_segment(src,n)
        if isinstance(n,ast.Assign) and any(getattr(t,'id',None)==name for t in n.targets): return ast.get_source_segment(src,n)
        if isinstance(n,ast.AnnAssign) and getattr(n.target,'id',None)==name: return ast.get_source_segment(src,n)
    raise KeyError(name)
FAILED=0
def ok(c,m):
    global FAILED
    FAILED+=0 if c else 1
    print(('PASS ' if c else 'FAIL ')+m)

# 1) rate limit
g={'time':time,'defaultdict':defaultdict,'deque':deque}
for n in ['PASSWORD_CHANGE_LIMIT','PASSWORD_CHANGE_WINDOW_S','_password_attempts','_password_rate_limited']: exec(seg(n),g)
rl=g['_password_rate_limited']
r=[rl('a@x',now=100+i) for i in range(6)]
ok(r==[False]*5+[True],'6th submission within 15 min is blocked')
ok(rl('b@x',now=105) is False,'other accounts unaffected')
ok(rl('a@x',now=100+901) is False,'allowed again after the window passes')

# 2) escaping
g={'html':html,'STYLE':''}; exec(seg('_password_form'),g)
out=g['_password_form'](email='<b>x@y</b>',error='<script>alert(1)</script>')
ok('<script>' not in out and '&lt;script&gt;' in out,'error message is escaped')
ok('&lt;b&gt;x@y' in out,'email is escaped')
ok('name="current"' not in out,'no current-password field')

# 3) ensure_admin: env hash applied only when new/rotated
class Q:  # stand-in for select/insert/update statements
    def __init__(s,kind,*a): s.kind=kind; s.vals={}
    def where(s,*a): return s
    def values(s,**k): s.vals=k; return s
db={}
class Conn:
    async def execute(s,q):
        class R:
            def __init__(s,row): s.row=row
            def first(s): return s.row
        if q.kind=='select_t': return R(object())
        if q.kind=='select_a':
            row=db.get('admin'); return R(types.SimpleNamespace(**row) if row else None)
        if q.kind=='insert': db['admin']=dict(q.vals)
        if q.kind=='update': db['admin'].update(q.vals)
        return R(None)
class Eng:
    def begin(s):
        class CM:
            async def __aenter__(s): return Conn()
            async def __aexit__(s,*a): pass
        return CM()
col=types.SimpleNamespace(id=1,email=1,env_hash_applied=1)
tbl=types.SimpleNamespace(c=col)
def select(*cols): return Q('select_a' if len(cols)==2 else 'select_t')
stub_store=types.ModuleType('app.integrations.store')
async def mig(conn): pass
stub_store.migrate_legacy_google_tokens=mig
import sys; sys.modules['app']=types.ModuleType('app'); sys.modules['app.integrations']=types.ModuleType('app.integrations'); sys.modules['app.integrations.store']=stub_store
settings=types.SimpleNamespace(admin_password_hash='ENV1',admin_username='Admin')
class Log:
    def info(s,*a): pass
    def warning(s,*a): pass
g={'settings':settings,'persistence':types.SimpleNamespace(tenants=tbl,accounts=tbl,engine=lambda:Eng()),
   'select':select,'insert':lambda t:Q('insert'),'update':lambda t:Q('update'),'ADMIN_TENANT':'admin','_now':lambda:0,'log':Log()}
exec(seg('ensure_admin'),g); ea=g['ensure_admin']
run=lambda: asyncio.run(ea())
run(); ok(db['admin']['password_hash']=='ENV1','fresh install: admin gets env password')
db['admin']['password_hash']='CHANGED_IN_APP'; run()
ok(db['admin']['password_hash']=='CHANGED_IN_APP','restart keeps a password changed in the app (bug #1 fixed)')
settings.admin_password_hash='ENV2'; run()
ok(db['admin']['password_hash']=='ENV2','rotating the env var still resets it (recovery path)')
db['admin']={'password_hash':'OLD','env_hash_applied':None}; settings.admin_password_hash='ENV2'; run()
ok(db['admin']['password_hash']=='ENV2' and db['admin']['env_hash_applied']=='ENV2','first boot after deploy on existing row: same as today, then recorded')

if FAILED:
    raise SystemExit(f"{FAILED} check(s) failed")
