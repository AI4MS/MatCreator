import base64
import hashlib
import json
from matcreator.xrd.vendor.frames import FrameWriter, read_frames
import asyncio
from matcreator.xrd.vendor import structures
from matcreator.xrd.vendor.frames import search_frames

def test_live_index_only_exposes_complete_frames_and_preserves_cif(tmp_path):
    writer=FrameWriter(tmp_path,'preopt',[[10,1],[11,2]])
    text='data_test\n_cell_length_a 5\n'
    writer.append('a','A',cif=text,state='best_evaluation',quality=.5)
    first=read_frames(tmp_path)
    assert first['frames'][0]['cif']['sha256']==hashlib.sha256(text.encode()).hexdigest()
    assert base64.b64decode(first['frames'][0]['cif']['source_base64']).decode()==text
    writer.append('a','A',cif='original',state='fallback')
    assert first['frames'][0]['state']=='best_evaluation'
    assert read_frames(tmp_path)['frames'][-1]['state']=='fallback'
    assert len(list((tmp_path/'frames').glob('*.json')))==2

def test_all_search_cifs_are_cached_in_rank_order_with_failures_isolated(tmp_path, monkeypatch):
    snapshot=[{'node_id':'lib','value':{'kind':'library'}}, {'node_id':'exp','value':{'kind':'pattern','points':[[10,1],[20,2]]}}]
    (tmp_path/'input-snapshot.json').write_text(json.dumps(snapshot),encoding='utf-8')
    active=0; maximum=0; calls=[]
    async def fetch(value,args):
        nonlocal active,maximum
        cod=args['cod_id'];calls.append(cod);active+=1;maximum=max(maximum,active)
        try:
            await asyncio.sleep(.005 if cod.endswith('1') else 0)
            if cod.endswith('3'):raise ValueError('missing CIF')
            raw=('data_'+cod).encode()
            return {'structure':{'filename':cod+'.cif','source_base64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest()}}
        finally:active-=1
    monkeypatch.setattr(structures,'prepare_cod_structure',fetch)
    ids=[str(1000000+i) for i in range(1,7)]
    candidates=[{'node_id':'lib:'+cod,'filename':cod,'metadata':{'library_node_id':'lib','reference_code':cod},'score':.5,'peaks':[]} for cod in ids]
    asyncio.run(search_frames(tmp_path,{'candidates':candidates}))
    frames=read_frames(tmp_path)['frames']
    assert sorted(calls)==ids and maximum<=4
    assert [f['label'] for f in frames]==ids
    assert frames[2]['error']=='missing CIF' and 'cif' not in frames[2]
    assert all('cif' in f for i,f in enumerate(frames) if i!=2)
