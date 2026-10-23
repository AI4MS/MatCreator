"""Small synthetic local API acceptance. Run with the XRD scientific interpreter."""
import base64, json, math, os, time, urllib.request
from pathlib import Path
from pymatgen.core import Lattice, Structure
from pymatgen.analysis.diffraction.xrd import XRDCalculator
from pymatgen.io.cif import CifWriter

URL=os.environ.get('MC_XRD_API_URL','http://127.0.0.1:8001/api/xrd').rstrip('/')
def api(path,body=None):
    request=urllib.request.Request(URL+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)
def main():
    structure=Structure.from_spacegroup('Fd-3m',Lattice.cubic(5.43),['Si'],[[0,0,0]])
    pattern=XRDCalculator(wavelength=1.540593).get_pattern(structure,two_theta_range=(15,75))
    points=[[15+i*.03,30+sum(20*y*math.exp(-((15+i*.03-x)/.10)**2) for x,y in zip(pattern.x,pattern.y))] for i in range(2001)]
    references='PDF#SYNTHETIC: software acceptance\nSynthetic Silicon\nSi\nLambda = 1.540593\n'+'\n'.join(f'{x} {d} {y} (1 1 1)' for x,d,y in zip(pattern.x,pattern.d_hkls,pattern.y))
    owner=api('/workspaces',{'name':'合成 Si · 接入验收（非实验）'})['id']
    def command(action,args={}):return api('/workspaces/'+owner+'/command',{'action':action,'arguments':args})
    def upload(kind,name,text):
        target=command('ensure_input',{'kind':kind})
        command('document',{'target':target,'action':'import','revision':0,'arguments':{'filename':name,'source_base64':base64.b64encode(text.encode()).decode()}})
        return target
    upload('pattern','synthetic-silicon.csv','\n'.join(f'{x},{y}' for x,y in points))
    reference=upload('reference','synthetic-silicon-reference.txt',references)
    cif=upload('cif','synthetic-silicon.cif',str(CifWriter(structure)))
    command('document',{'target':cif,'action':'associate','revision':1,'arguments':{'reference_node_id':reference}})
    receipts={}
    for stage in ['search','preopt','fit']:
        config={'library_engine':'native','workflow_stage':stage,'iterations':1,'preopt_max_nfev':1,'low_angle':15,'high_angle':75}
        if stage!='search':config.update(workflow_match_run_id=receipts['search']['run_id'],selected_candidate_ids=[reference])
        if stage=='fit':config['workflow_preopt_run_id']=receipts['preopt']['run_id']
        receipt=command('start',{'request_id':'synthetic-acceptance-'+stage,'config':config});receipts[stage]=receipt
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            info=api('/workspaces/'+owner)
            if info['status'] not in {'running','waiting'}:break
            time.sleep(1)
        else:raise RuntimeError('Synthetic stage timeout; inspect existing run, do not resubmit: '+receipt['run_id'])
        if info['error']:raise RuntimeError(info['error'])
        result=info['details']['result'] if stage=='search' else info['details']['workflow'][stage]['result']
        if stage!='search':assert result['candidates'][0]['status']!='failed',result['candidates'][0]
        print(json.dumps({'workspace_id':owner,'stage':stage,'run_id':receipt['run_id'],'status':result.get('status','completed'),'candidate_status':result['candidates'][0].get('status'),'converged':result['candidates'][0].get('converged')},ensure_ascii=False),flush=True)
    output=Path('.local-review-logs/xrd-smoke-result.json')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps({'workspace_id':owner,'receipts':receipts},indent=2),encoding='utf-8')
if __name__=='__main__':main()
