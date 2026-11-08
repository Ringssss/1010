"""Narrow CPU-only mount ordering and immutable setup-resume regression check."""
import argparse
import copy,hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from orchestrate import HostOps,RESULTS,ROOT
parser=argparse.ArgumentParser()
parser.add_argument('--out',type=Path,required=True)
parser.add_argument('--prior',type=Path,required=True)
parser.add_argument('--setup-dir',type=Path,required=True)
args=parser.parse_args()
out=args.out.resolve();prior=args.prior.resolve();setupdir=args.setup_dir.resolve()
if out.parent!=RESULTS or not out.name.startswith('execution_host_recovery_cpu_'):
 raise ValueError('new owned recovery CPU output required')
out.mkdir(exist_ok=False)
before=json.loads((prior/'before_node27/stdout.json').read_text())
after=json.loads((prior/'after_node27/stdout.json').read_text())
checks=[]
def check(name,test):checks.append(dict(name=name,passed=bool(test)))
HostOps.protected(before,after);check('actual_rotated_Docker_mounts_preserve_all_old_identities',True)
base=dict(protected_containers=[dict(id='CPU',name='CPU',mounts=[dict(Source='/srcA',Destination='/a',RW=False),dict(Source='/srcB',Destination='/b',RW=True)])],original_xml_sha256={})
rotated=copy.deepcopy(base);rotated['protected_containers'][0]['mounts'].reverse();HostOps.protected(base,rotated);check('mount_order_ignored',True)
for key,value in [('Source','/changed'),('RW',True),('Destination','/changed')]:
 changed=copy.deepcopy(base);changed['protected_containers'][0]['mounts'][0][key]=value
 try:HostOps.protected(base,changed)
 except ValueError:check('mount_'+key+'_change_rejected',True)
 else:check('mount_'+key+'_change_rejected',False)
ops=HostOps.__new__(HostOps);ops.out=out;ops.events=0;ops.deadline_at=None
ops.specs={n:json.loads((setupdir/(n+'.setup.spec.json')).read_text()) for n in ('node27','node30')}
helper=copy.deepcopy(after);s=ops.specs['node30'];helper['boot_id']=s['boot_id'];ci=helper['owned_container'];ci.update(Id='f'*64,Name='/'+s['container_name']);ci['State']['StartedAt']='CPU_FIXTURE_HELPER_STARTED';ci['HostConfig']['Memory']=s['container_hard_cap_bytes'];ci['HostConfig']['MemorySwap']=s['container_hard_cap_bytes'];ci['Mounts']=[dict(Source=s['stage']+'/project',Destination=str(ROOT),RW=True),dict(Source='/opt/hyhal',Destination='/opt/hyhal',RW=False)];helper['protected_containers']=copy.deepcopy(before['protected_containers'])
helperbefore=copy.deepcopy(before);helperbefore['boot_id']=s['boot_id']
calls=[]
def fakehost(node,name):
 body=copy.deepcopy(after if node=='node27' else helperbefore if name=='before_node30' else helper)
 d=out/name;d.mkdir();(d/'stdout.json').write_text(json.dumps(body));calls.append(('host',node,name));return body
ops.host=fakehost

def fakerecord(node,argv,name,timeout,payload=None):
 calls.append(('record',node,name));d=out/name;d.mkdir();(d/'command.json').write_text(json.dumps({'CPU_FIXTURE':True,'argv':argv}));return json.dumps({'container':{'id':'f'*64}}).encode()
ops.record=fakerecord
ops.setup(setupdir,prior)
result=json.loads((out/'setup.json').read_text());provenance=json.loads((out/'recovery_provenance.json').read_text())
check('resume_skips_all_node27_mutation',not any(t[0]=='record' and t[1]=='node27' for t in calls))
check('resume_keeps_exact_prior_node27_CID_and_started',result['containers']['node27']==ops.identity('node27',after))
check('resume_creates_only_node30',provenance['new_nodes']==['node30'] and any(t==('record','node30','setup_node30') for t in calls))
check('frozen_129_assets_not_restaged',len(ops.specs['node27']['assets'])==129 and all(t[2].split('_',1)[0]!='stage' for t in calls))
check('local_fix_SHA_differs_from_frozen_remote_orchestrator',provenance['local_orchestrator_ref']['sha256']!=provenance['skipped_existing_nodes'][0]['frozen_asset_orchestrator_sha256'])
qualification=dict(CPU_FIXTURE_ONLY=True,GPU_launches=0,SSH_commands=0,checks=checks,passed=all(c['passed'] for c in checks),calls=calls)
(out/'qualification.json').write_text(json.dumps(qualification,indent=2)+'\n')
print(json.dumps(dict(passed=qualification['passed'],checks=len(checks),out=str(out))))
if not qualification['passed']:sys.exit(1)
