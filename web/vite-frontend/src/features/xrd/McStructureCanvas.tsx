import {useEffect,useRef,useState} from 'react';
import { mount, unmount } from 'svelte';
import StructureViewer from '../../structure/StructureViewer.svelte';
export function McStructureCanvas({structure,onReady}:any) {
  const target=useRef<HTMLDivElement>(null);const [error,setError]=useState('');
  const ready=useRef(onReady);ready.current=onReady;
  useEffect(()=>{
    const abort=new AbortController(); let viewer:any;let disposed=false;
    setError('');
    fetch('/api/xrd/structure',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(structure),signal:abort.signal})
      .then(async r=>{const v=await r.json();if(!r.ok)throw Error(v.detail);return v;})
      .then(data=>{if(disposed||!target.current)return;
        const background=getComputedStyle(document.body).getPropertyValue('--panel').trim()||'#18212b';
        viewer=mount(StructureViewer,{target:target.current,props:{structure_string:data.structure_string,background_color:background,performance_mode:data.n_atoms>500?'speed':'quality',xrd_mode:true}});
        ready.current?.();
      }).catch(e=>{if(e.name!=='AbortError'&&!disposed){setError(e.message);ready.current?.();}});
    return()=>{disposed=true;abort.abort();if(viewer)void unmount(viewer);};
  },[structure.source_base64]);
  return <div className="mc-xrd-crystal"><div ref={target}/>{error&&<p role="alert">{error}</p>}<small>MC 结构画布 · XRD 结果只读 · 拖动旋转 / 滚轮缩放</small></div>;
}
