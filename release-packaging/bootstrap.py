"""Digest-checked entry for a proposed single disposable public CI job.

This file is source preparation. No job is authorized by its presence.
"""
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

REPO = "itcustomsolution/task-handoff-check"
REF = "refs/heads/codex/package-two-python-tools-20261021"

# Root executes only this fixed staging text, then immutable verified copies.
# No checkout Python module is imported by this root program.
STAGE = r'''
import atexit,hashlib,json,os,pathlib,re,resource,select,shutil,stat,subprocess,sys,time
source,expected,run,commit,start=sys.argv[1:]
assert re.fullmatch(r"[0-9]{1,20}",run) and re.fullmatch(r"[a-f0-9]{40}",commit)
assert re.fullmatch(r"[a-f0-9]{64}",expected) and 0 <= time.monotonic()-float(start) < 10
resource.setrlimit(resource.RLIMIT_AS,(268435456,268435456))
resource.setrlimit(resource.RLIMIT_CPU,(5,5))
resource.setrlimit(resource.RLIMIT_NOFILE,(64,64))
base=pathlib.Path('/run/pkgtwov5'+run)
assert not base.exists()
rootfd=os.open(source,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
def read(rel,limit):
 parts=pathlib.PurePosixPath(rel).parts
 assert parts and not rel.startswith('/') and all(p not in ('','.','..') for p in parts)
 fd=os.dup(rootfd)
 try:
  for part in parts[:-1]:
   nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=nxt
  leaf=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
  with os.fdopen(leaf,'rb') as stream:
   st=os.fstat(stream.fileno());assert stat.S_ISREG(st.st_mode) and st.st_nlink==1 and st.st_size<=limit
   data=stream.read(limit+1);assert len(data)<=limit;return data
 finally:os.close(fd)
raw=read('MANIFEST.json',262144);assert hashlib.sha256(raw).hexdigest()==expected
m=json.loads(raw);assert m['schema']==1 and 1<=len(m['files'])<=128
copies={};total=0
for rel,pin in m['files'].items():
 data=read(rel,2097152);assert len(data)==pin['bytes'] and hashlib.sha256(data).hexdigest()==pin['sha256']
 total+=len(data);assert total<=2097152;copies[rel]=data
assert 'native.py' in copies and 'payload.py' in copies
os.close(rootfd)
env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}
# The immutable native module is first staged in a root-only ordinary directory.
# Its code registers expiry before creating the work tmpfs or starting services.
stage=pathlib.Path('/run/pkgtwov5'+run+'-stage');stage.mkdir(mode=0o700)
def partial_cleanup():
 group=pathlib.Path('/sys/fs/cgroup')/('pkgtwov5'+run+'.slice')
 if time.monotonic()<float(start)+600 and stage.exists() and not stage.is_symlink() and (not group.exists() or 'populated 0' in (group/'cgroup.events').read_text()) and not base.exists():
  shutil.rmtree(stage)
atexit.register(partial_cleanup)
params={'run':run,'commit':commit,'start':float(start),'manifest':expected,'stage':str(stage)}
(stage/'PARAMS.json').write_text(json.dumps(params));(stage/'PARAMS.json').chmod(0o444)
os.umask(0o022)
for rel,data in copies.items():
 p=stage/rel;p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('xb') as out:out.write(data)
 p.chmod(0o444)
(stage/'MANIFEST.json').write_bytes(raw);(stage/'MANIFEST.json').chmod(0o444)
# Only public immutable control/input bytes are exposed inside the chroot.
stage.chmod(0o755)
stem='pkgtwov5'+run;parent=stem+'.slice';broker=stem+'broker.service'
def native(args,seconds=3,check=True):
 def childcap():resource.setrlimit(resource.RLIMIT_CPU,(1,1))
 remaining=float(start)+600-time.monotonic();assert remaining>0
 result=subprocess.run(args,env=env,timeout=min(seconds,remaining),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,preexec_fn=childcap)
 if check:assert result.returncode==0
 return result.returncode
# Do not adopt a previously loaded unit, mount or staged operation.
def probe_cap():resource.setrlimit(resource.RLIMIT_CPU,(1,1))
def fresh(unit):
 fields=('Id','LoadState','ActiveState','SubState','ControlGroup','FragmentPath','DropInPaths','Transient','Job')
 args=['systemctl','show','--all',unit]
 for field in fields:args.extend(['-p',field])
 probe=subprocess.run(args,env=env,timeout=min(2,max(0.001,float(start)+600-time.monotonic())),capture_output=True,preexec_fn=probe_cap)
 values={};reason=''
 try:
  assert len(probe.stdout)<=4096 and len(probe.stderr)<=4096,'probe-byte-limit'
  for line in probe.stdout.decode('utf-8','strict').splitlines():
   assert '=' in line,'probe-line'
   key,value=line.split('=',1)
   assert key in fields and key not in values,'probe-field'
   values[key]=value
  required=set(fields)-{'ControlGroup'}
  assert required<=values.keys(),'probe-missing-fields'
  assert values['Id']==unit,'unit-identity'
  assert values['ActiveState']=='inactive' and values['SubState']=='dead','unit-not-dead'
  assert values['FragmentPath']==values['DropInPaths']=='','configured-unit'
  assert values['Transient']=='no' and values['Job'] in ('','0'),'transient-or-pending-unit'
  assert values.get('ControlGroup','')=='','existing-control-group'
  assert not (pathlib.Path('/sys/fs/cgroup')/parent).exists(),'existing-parent-cgroup'
  if unit==parent and values['LoadState']=='loaded':
   # Slice units are synthesized without a file; loaded is not active ownership.
   assert probe.returncode==0 and 'ControlGroup' in values,'slice-probe-status'
  else:
   assert probe.returncode in (0,4) and values['LoadState']=='not-found','nonfresh-unit'
 except (AssertionError,UnicodeError) as error:
  reason=str(error)[:80] or type(error).__name__
 if reason:
  public={}
  enums={'LoadState':('stub','loaded','not-found','bad-setting','error','merged','masked'),
         'ActiveState':('active','reloading','inactive','failed','activating','deactivating','maintenance','refreshing'),
         'SubState':('dead','active','running','waiting','failed'),'Transient':('yes','no')}
  for key,value in values.items():
   if key=='Id':public[key]={'matches':value==unit}
   elif key in enums and value in enums[key]:public[key]=value
   elif key=='Job' and value in ('','0'):public[key]='none'
   else:public[key]={'empty':not value,'sha256':hashlib.sha256(value.encode()).hexdigest()}
  record={'kind':'fresh-unit-refusal','unit':unit,'reason':reason,'returncode':probe.returncode,
          'properties':public,
          'stdout_bytes':len(probe.stdout),'stderr_bytes':len(probe.stderr),
          'stdout_sha256':hashlib.sha256(probe.stdout).hexdigest(),'stderr_sha256':hashlib.sha256(probe.stderr).hexdigest()}
  raw=json.dumps(record,sort_keys=True,separators=(',',':'))
  if len(raw.encode())>1800-len('PACKAGING_ADMISSION_REFUSED ')-1:raw=json.dumps({'kind':'fresh-unit-refusal','unit':unit,'reason':reason,'returncode':probe.returncode,'detail_sha256':hashlib.sha256(raw.encode()).hexdigest()},sort_keys=True)
  print('PACKAGING_ADMISSION_REFUSED '+raw,flush=True)
  raise AssertionError('fresh-unit-admission')
for unit in (parent,broker,stem+'ctl.service',stem+'work.service',stem+'expiry.timer',stem+'expiry.service'):
 fresh(unit)
assert time.monotonic()-float(start)<15
argv=['systemd-run','--quiet','--wait','--pipe','--service-type=exec','--unit='+broker,'--slice='+parent,
 '-p','MemoryMax=268435456','-p','MemorySwapMax=0','-p','TasksMax=8','-p','CPUQuota=10%',
 '-p','LimitAS=4294967296','-p','LimitNPROC=64','-p','LimitCORE=0','-p','LimitFSIZE=134217728',
 '-p','RuntimeMaxSec='+str(max(1,int(float(start)+570-time.monotonic()))),'-p','TimeoutStopSec=3',
 '-p','KillMode=control-group','-p','SendSIGKILL=yes','-p','UMask=0022',
 '/usr/bin/env','-i','PATH=/usr/sbin:/usr/bin:/sbin:/bin','LANG=C.UTF-8','PYTHONDONTWRITEBYTECODE=1',
 '/usr/bin/python3','-I','-S',str(stage/'native.py'),'host-root',str(stage)]
proc=None;packet=bytearray();ok=False
try:
 proc=subprocess.Popen(argv,env=env,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,preexec_fn=probe_cap)
 os.set_blocking(proc.stdout.fileno(),False)
 while True:
  assert time.monotonic()<float(start)+565
  ready,_,_=select.select([proc.stdout],[],[],0.1)
  if ready:
   block=os.read(proc.stdout.fileno(),65536)
   if not block:break
   packet.extend(block);assert len(packet)<=8384512
 assert proc.wait(timeout=2)==0
 receipt=json.loads((stage/'BROKER-CLEANUP.json').read_text())
 assert receipt['run']==run and receipt['commit']==commit and receipt['manifest']==expected
 assert receipt['packet_sha256']==hashlib.sha256(packet).hexdigest()
 ok=True
finally:
 errors=[]
 def attempt(fn):
  try:return fn()
  except BaseException as error:errors.append(type(error).__name__);return None
 def terminal_units(units):
  for unit in units:
   probe=subprocess.run(['systemctl','show',unit,'-p','LoadState','-p','ActiveState'],env=env,timeout=min(2,max(0.001,float(start)+600-time.monotonic())),capture_output=True,preexec_fn=probe_cap)
   assert len(probe.stdout)<=256
   values=dict(line.split('=',1) for line in probe.stdout.decode().splitlines() if '=' in line)
   assert values.get('LoadState')=='not-found' or values.get('ActiveState') in ('inactive','failed')
  return True
 if proc is not None and proc.poll() is None:
  attempt(proc.kill);attempt(lambda:proc.wait(timeout=2))
 group=pathlib.Path('/sys/fs/cgroup')/parent
 if (group/'cgroup.kill').exists():attempt(lambda:(group/'cgroup.kill').write_text('1'))
 attempt(lambda:native(['systemctl','stop',stem+'work.service',stem+'ctl.service',broker,parent],10,check=False))
 terminal=attempt(lambda:terminal_units([stem+'work.service',stem+'ctl.service',broker,parent]))
 def empty():
  if group.exists():
   if ok:assert receipt['identity']['path']==str(group) and group.stat().st_ino==receipt['identity']['inode']
   assert 'populated 0' in (group/'cgroup.events').read_text()
  return True
 empty_group=attempt(empty)
 if terminal and empty_group:
  if base.exists():
   mounted=any(line.split()[4]==str(base) for line in pathlib.Path('/proc/self/mountinfo').read_text().splitlines())
   if mounted:attempt(lambda:native(['umount',str(base)],3))
   attempt(base.rmdir)
  if not base.exists():
   attempt(lambda:native(['systemctl','stop',stem+'expiry.timer',stem+'expiry.service'],3,check=False))
   attempt(lambda:terminal_units([stem+'expiry.timer',stem+'expiry.service']))
   assert stage.parent==pathlib.Path('/run') and stage.name==stem+'-stage' and not stage.is_symlink()
   if stage.exists():attempt(lambda:shutil.rmtree(stage))
 assert not errors and terminal and empty_group
 assert not base.exists() and not stage.exists() and not any(line.split()[4]==str(base) or line.split()[4].startswith(str(base)+'/') for line in pathlib.Path('/proc/self/mountinfo').read_text().splitlines())
 assert time.monotonic()<float(start)+600

assert ok
remaining=memoryview(packet);os.set_blocking(1,False)
while remaining:
 assert time.monotonic()<float(start)+600
 _,ready,_=select.select([],[1],[],0.05)
 if ready:
  try:remaining=remaining[os.write(1,remaining):]
  except BlockingIOError:pass
closed={'kind':'closed','run':run,'commit':commit,'manifest':expected,'cleanup':True,'success':True,'packet_sha256':hashlib.sha256(packet).hexdigest()}
line=('PKGTWO '+json.dumps(closed,sort_keys=True,separators=(',',':'))+'\n').encode()
while line:
 assert time.monotonic()<float(start)+600
 _,ready,_=select.select([],[1],[],0.05)
 if ready:
  try:line=line[os.write(1,line):]
  except BlockingIOError:pass

'''


# Fixed cleanup only. It never imports staged source or starts a workload.
CLEAN = r'''
import json,os,pathlib,re,resource,shutil,subprocess,sys,time
expected,run,commit=sys.argv[1:]
assert re.fullmatch(r'[a-f0-9]{64}',expected) and re.fullmatch(r'\d{1,20}',run) and re.fullmatch(r'[a-f0-9]{40}',commit)
resource.setrlimit(resource.RLIMIT_AS,(268435456,268435456));resource.setrlimit(resource.RLIMIT_CPU,(5,5))
env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'}
stem='pkgtwov5'+run;stage=pathlib.Path('/run')/(stem+'-stage');base=pathlib.Path('/run')/stem
units=[stem+'work.service',stem+'ctl.service',stem+'broker.service',stem+'.slice']
def terminal():
 for unit in units:
  r=subprocess.run(['systemctl','show',unit,'-p','LoadState','-p','ActiveState'],env=env,timeout=2,capture_output=True,preexec_fn=lambda:resource.setrlimit(resource.RLIMIT_CPU,(1,1)))
  assert len(r.stdout)<=256
  values=dict(line.split('=',1) for line in r.stdout.decode().splitlines() if '=' in line)
  assert values.get('LoadState')=='not-found' or values.get('ActiveState') in ('inactive','failed')
if not stage.exists() and not base.exists():
 terminal();sys.exit(0)
assert stage.is_dir() and not stage.is_symlink() and stage.stat().st_uid==0
p=json.loads((stage/'PARAMS.json').read_text());assert p['run']==run and p['commit']==commit and p['manifest']==expected and p['stage']==str(stage)
deadline=p['start']+600
assert time.monotonic()<deadline
def native(args,maximum):
 def childcap():resource.setrlimit(resource.RLIMIT_CPU,(1,1))
 remaining=deadline-time.monotonic();assert remaining>0
 subprocess.run(args,env=env,timeout=min(maximum,remaining),check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,preexec_fn=childcap)
group=pathlib.Path('/sys/fs/cgroup')/(stem+'.slice')
if (group/'cgroup.kill').exists():(group/'cgroup.kill').write_text('1')
native(['systemctl','stop',*units],10)
terminal()
assert not group.exists() or 'populated 0' in (group/'cgroup.events').read_text()
if base.exists():
 mounted=any(line.split()[4]==str(base) for line in pathlib.Path('/proc/self/mountinfo').read_text().splitlines())
 if mounted:native(['umount',str(base)],3)
 base.rmdir()
assert not base.exists() and not any(line.split()[4]==str(base) or line.split()[4].startswith(str(base)+'/') for line in pathlib.Path('/proc/self/mountinfo').read_text().splitlines())
native(['systemctl','stop',stem+'expiry.timer',stem+'expiry.service'],3)
shutil.rmtree(stage)
assert not stage.exists() and time.monotonic()<deadline
'''


def main():
    cleanup = len(sys.argv) == 3 and sys.argv[1] == 'cleanup'
    expected = sys.argv[2] if cleanup else sys.argv[1]
    assert (cleanup or len(sys.argv) == 2) and len(expected) == 64
    assert sys.platform == "linux" and os.geteuid() != 0
    assert os.environ.get("GITHUB_REPOSITORY") == REPO
    assert os.environ.get("GITHUB_REF") == REF
    assert os.environ.get("GITHUB_EVENT_NAME") == "push"
    assert os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
    if cleanup:
        result = subprocess.run(
            ['sudo', '-n', '/usr/bin/python3', '-I', '-S', '-c', CLEAN,
             expected, os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_SHA']],
            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'}, timeout=30)
        raise SystemExit(result.returncode)
    assert os.environ.get("ImageOS") == "ubuntu24"
    assert os.environ.get("ImageVersion") in ("20261012.334.1", "20261018.339.1")
    # Never route around stricter original caller limits through PID1.
    for key, minimum in ((resource.RLIMIT_AS, 4 * 1024**3), (resource.RLIMIT_NPROC, 64)):
        assert all(v == resource.RLIM_INFINITY or v >= minimum for v in resource.getrlimit(key))
    here = Path(__file__).resolve().parent
    assert hashlib.sha256((here / "MANIFEST.json").read_bytes()).hexdigest() == expected
    start = time.monotonic()
    result = subprocess.run(
        ["sudo", "-n", "/usr/bin/python3", "-I", "-S", "-c", STAGE, str(here),
         expected, os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_SHA"], str(start)],
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}, timeout=600,
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
