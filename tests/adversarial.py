#!/usr/bin/env python3
"""Adversarial oracle: malformed and hostile input must never 5xx or hang."""
import sys
import subprocess, json, os
API = os.environ.get('TOOL_API', 'http://localhost:5000/api/mutate')
IMG=os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','examples','input','sample.jpg')

def post(fields, label):
    cmd=['curl','-s','-m','40','-w','\n<<%{http_code}>>','-X','POST',API]
    for f in fields: cmd += ['-F', f]
    out=subprocess.run(cmd,capture_output=True,text=True).stdout
    body,_,code=out.rpartition('<<'); code=code.rstrip('>>\n')
    body=body.strip()
    # a crash shows as empty body / connection error
    if not code or code=='000':
        return label,'NO RESPONSE (server crash?)',body[:80]
    try:
        d=json.loads(body); ok=d.get('success')
        return label, code, ('ok' if ok else 'err: '+str(d.get('error'))[:70])
    except Exception:
        return label, code, 'NON-JSON: '+body[:70]

cases=[
 (['mutation=blur','parameters={"sigma":5}'], 'no image field'),
 ([f'image=@{IMG}'], 'no mutation'),
 ([f'image=@{IMG}','mutation='], 'empty mutation'),
 ([f'image=@{IMG}','mutation=../../etc/passwd'], 'path traversal in mutation'),
 ([f'image=@{IMG}','mutation=__init__'], 'dunder mutation'),
 ([f'image=@{IMG}','mutation=save'], 'wand method as mutation'),
 ([f'image=@{IMG}','mutation=blur','parameters=not json'], 'malformed params'),
 ([f'image=@{IMG}','mutation=blur','parameters=[]'], 'params is a list'),
 ([f'image=@{IMG}','mutation=blur','parameters=null'], 'params null'),
 ([f'image=@{IMG}','mutation=blur','parameters={"sigma":"abc"}'], 'string sigma'),
 ([f'image=@{IMG}','mutation=blur','parameters={"sigma":-5}'], 'negative sigma'),
 ([f'image=@{IMG}','mutation=blur','parameters={"sigma":1e12}'], 'huge sigma'),
 ([f'image=@{IMG}','mutation=blur','parameters={"sigma":null}'], 'null sigma'),
 ([f'image=@{IMG}','mutation=blur','parameters={"bogus":1}'], 'unknown param'),
 ([f'image=@{IMG}','mutation=rotate','parameters={"degrees":1e9}'], 'huge rotate'),
 ([f'image=@{IMG}','mutation=border','parameters={"pixels":100000}'], 'huge border'),
 ([f'image=@{IMG}','mutation=chop','parameters={"pixels":999999}'], 'chop beyond size'),
 ([f'image=@{IMG}','mutation=colors','parameters={"colors":0}'], 'colors=0'),
 ([f'image=@{IMG}','mutation=median','parameters={"kernel":999}'], 'huge median kernel'),
 ([f'image=@{IMG}','mutation=colorspace','parameters={"colorspace":"../../x"}'], 'traversal colorspace'),
 ([f'image=@{IMG}','mutation=profile','parameters={"profile":"../../../etc/passwd"}'], 'traversal profile'),
 ([f'image=@{IMG}','mutation=grayscale','parameters={"method":"Nope"}'], 'bad grayscale method'),
]
print(f"{'case':38s} {'code':6s} result")
print('-'*100)
crashes=[]
for fields,label in cases:
    l,c,r = post(fields,label)
    flag = '  <-- ' if (str(c).startswith('5') or 'NO RESPONSE' in str(c) or 'NON-JSON' in r) else ''
    if flag: crashes.append(l)
    print(f"{l:38s} {str(c):6s} {r}{flag}")
print()
print("needs attention:", crashes or 'none')

sys.exit(1 if crashes else 0)
