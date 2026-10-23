import React,{useEffect,useLayoutEffect,useMemo,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import plugin from './vendor/index';
import {FrameCanvas,resetWorkflowCanvas} from './vendor/FrameCanvas';
import {McStructureCanvas} from './McStructureCanvas';
import './mc-xrd.css';
import {createMultiphaseReview} from './hostCommands';
import {JevSettings} from './JevSettings';
import {dragOffset} from './dragGeometry';
import {installDialogDragging} from './dialogDragging';
const Settings=plugin.views.settings;
export async function api(path='',body) {
  const response=await fetch('/api/xrd'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await response.json(); if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||data)); return data;
}
function Workbench({openChat,createNew=false}) {
  const [owner,setOwner]=useState(''),[workspaces,setWorkspaces]=useState([]),[snapshot,setSnapshot]=useState(null),[error,setError]=useState('');
  const [canvas,setCanvas]=useState(''),[zoom,setZoom]=useState(1),[position,setPosition]=useState({x:0,y:0});
  const [candidateStructure,setCandidateStructure]=useState(null);
  const [edgeOffset,setEdgeOffset]=useState({x:0,y:0});
  const edgeDrag=useRef(null),suppressEdgeClick=useRef(false);
  const hostRef=useRef(null),surface=useRef(null),floating=useRef(null),latest=useRef(null);
  latest.current=snapshot;
  useEffect(()=>installDialogDragging(),[]);
  useEffect(()=>{let active=true;(async()=>{let list=await api('/workspaces');let id=createNew?(await api('/workspaces',{})).id:localStorage.getItem('mc.xrd.workspace');if(createNew)list=await api('/workspaces');if(!list.some(w=>w.id===id)){const d=list.at(-1)||await api('/workspaces',{});id=d.id;list=await api('/workspaces');}if(active){setWorkspaces(list);setOwner(id);}})().catch(e=>active&&setError(e.message));return()=>{active=false;};},[]);
  useEffect(()=>{if(!owner)return;setCandidateStructure(null);localStorage.setItem('mc.xrd.workspace',owner);let alive=true,timer;setSnapshot(null);const load=async()=>{try{const next=await api(`/workspaces/${owner}`);if(alive){setSnapshot(next);setError('');}}catch(e){if(alive)setError(e.message);}finally{if(alive)timer=setTimeout(load,2500);}};void load();return()=>{alive=false;clearTimeout(timer);};},[owner]);
  const host=useMemo(()=>{
    if(!owner)return null;
    const path=`/workspaces/${owner}`;
    const command=(action,arguments_={})=>api(path+'/command',{action,arguments:arguments_});
    const h={
      getAgentInfo:()=>api(path),getInputs:()=>api(path+'/inputs'),
      readDocument:(target)=>api(path+'/document'+(target?'?target='+encodeURIComponent(target):'')),
      documentAction:async(action,args,revision,target)=>{const d=await command('document',{action,arguments:args,revision,target});if(action==='import')resetWorkflowCanvas(owner);return d;},
      ensureXrdInput:(kind)=>command('ensure_input',{kind}),
      updateConfig:async(config)=>{const next=await command('update_config',config);setSnapshot(old=>old?{...old,card:{...old.card,config:next}}:old);return next;},
      runAnalysis:()=>command('start',{request_id:crypto.randomUUID()}),stopAnalysis:()=>command('stop'),
      resourceAction:command,
      listCards:async(types)=>{const inputs=await api(path+'/inputs');return inputs.filter(i=>types.includes('xrd.'+i.kind));},
      openLinkedCanvas:async(type,_name,structure)=>{setCandidateStructure(structure||null);setCanvas(type==='xrd.structure-canvas'?'structure':'spectrum');},
      openInputNode:async(type,name,document)=>{const id=await command('ensure_input',{kind:type.split('.')[1]});const d=await h.readDocument(id);await h.documentAction('import',document,d.revision,id);if(type==='xrd.cif')setCanvas('structure');return id;},
      startXrdWorkflow:async(file)=>{const bytes=new Uint8Array(await file.arrayBuffer());let data='';for(let i=0;i<bytes.length;i+=32768)data+=String.fromCharCode(...bytes.subarray(i,i+32768));const id=await h.ensureXrdInput('pattern');const d=await h.readDocument(id);await h.documentAction('import',{filename:file.name,source_base64:btoa(data)},d.revision,id);resetWorkflowCanvas(owner);setSnapshot(await api(path));},
      getMultiphase:()=>api(path+'/multiphase'),getMultiphaseFrame:(args)=>command('multiphase_frame',args),
      saveMultiphaseOptions:args=>command('multiphase_options',args),startMultiphase:async args=>{const r=await command('multiphase_start',args);if(args.agent_node_id!=='jev')openAgentDiscussion('多相筛选已准备。请使用 xrd_action 的 multiphase_decision / multiphase_evaluate 工具逐步选择候选，达到预算后调用 multiphase_finish。');return r;},
      stopMultiphase:()=>command('multiphase_stop'),reviewMultiphase:createMultiphaseReview(command),
      openResultsConversation:async()=>openAgentDiscussion('请读取此 XRD 工作台的结果，结合运行历史解释拟合指标、收敛状态与物相判断的证据边界。'),
    };
    function openAgentDiscussion(text){openChat();const input=document.getElementById('text-input');input.value=`${text}\nXRD workspace_id: ${owner}`;input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();return {conversation_id:owner};}
    return h;
  },[owner,openChat]);
  hostRef.current=host;
  useLayoutEffect(()=>{
    if(!canvas)return;
    const keepVisible=()=>{
      const element=floating.current;if(!element)return;
      const rect=element.getBoundingClientRect(),scale=rect.width/(element.offsetWidth*zoom);
      setPosition(origin=>dragOffset(rect,{width:innerWidth,height:innerHeight},origin,{x:0,y:0},scale));
    };
    keepVisible();window.addEventListener('resize',keepVisible);
    return()=>window.removeEventListener('resize',keepVisible);
  },[canvas,zoom,Boolean(snapshot)]);
  function drag(event) {
    if(event.button!==0||event.target.closest('button,select,input'))return;
    event.preventDefault();
    const rect=floating.current.getBoundingClientRect(),start={x:event.clientX,y:event.clientY};
    const element=event.currentTarget;element.setPointerCapture(event.pointerId);
    const scale=rect.width/(floating.current.offsetWidth*zoom);
    const move=e=>setPosition(dragOffset(rect,{width:innerWidth,height:innerHeight},position,{x:e.clientX-start.x,y:e.clientY-start.y},scale));
    const end=()=>{element.removeEventListener('pointermove',move);element.removeEventListener('pointerup',end);element.removeEventListener('pointercancel',end);};
    element.addEventListener('pointermove',move);element.addEventListener('pointerup',end);element.addEventListener('pointercancel',end);
  }
  function startEdgeDrag(event) {
    if(event.button!==0)return;
    const button=event.currentTarget,rect=button.getBoundingClientRect();
    const panel=surface.current.closest('[role="tabpanel"]')||surface.current;
    const bounds=panel.getBoundingClientRect();
    edgeDrag.current={id:event.pointerId,startX:event.clientX,startY:event.clientY,origin:edgeOffset,rect,bounds,scale:surface.current.getBoundingClientRect().width/surface.current.offsetWidth};
    suppressEdgeClick.current=false;button.setPointerCapture(event.pointerId);
  }
  function moveEdge(event) {
    const drag=edgeDrag.current;if(!drag||drag.id!==event.pointerId)return;
    let dx=event.clientX-drag.startX,dy=event.clientY-drag.startY;
    if(Math.hypot(dx,dy)<5&&!suppressEdgeClick.current)return;
    suppressEdgeClick.current=true;
    dx=Math.max(drag.bounds.left-drag.rect.left,Math.min(drag.bounds.right-drag.rect.right,dx));
    dy=Math.max(drag.bounds.top-drag.rect.top,Math.min(Math.min(innerHeight,drag.bounds.bottom)-drag.rect.bottom,dy));
    setEdgeOffset({x:drag.origin.x+dx/drag.scale,y:drag.origin.y+dy/drag.scale});
  }
  function endEdgeDrag(event) {
    if(edgeDrag.current?.id!==event.pointerId)return;
    edgeDrag.current=null;if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);
  }
  async function newWorkspace(){try{const d=await api('/workspaces',{});setWorkspaces(await api('/workspaces'));setOwner(d.id);setCanvas('');}catch(e){setError(e.message);}}
  return <div className="mc-xrd-workbench" ref={surface}>
    <header className="mc-xrd-header"><JevSettings api={api}/><select title={workspaces.find(w=>w.id===owner)?.name} aria-label="XRD 工作台" value={owner} onChange={e=>{setOwner(e.target.value);setCanvas('');}}>{workspaces.map((w,i)=><option key={w.id} value={w.id}>{w.name}</option>)}</select><button type="button" onClick={newWorkspace} aria-label="新建 XRD 工作台" className="mc-xrd-new" title="新建独立 XRD 工作台">＋</button></header>
    <div className="mc-xrd-edge" style={{translate:`${edgeOffset.x}px ${edgeOffset.y}px`}}><button type="button" className={'mc-xrd-edge-button '+(canvas?'active':'')} aria-label="打开谱图与结构" aria-expanded={Boolean(canvas)} title="谱图与结构 · 可拖动" onPointerDown={startEdgeDrag} onPointerMove={moveEdge} onPointerUp={endEdgeDrag} onPointerCancel={endEdgeDrag} onLostPointerCapture={()=>{edgeDrag.current=null;}} onClick={event=>{if(suppressEdgeClick.current&&event.detail!==0){suppressEdgeClick.current=false;return;}setCandidateStructure(null);setPosition({x:0,y:0});setZoom(1);setCanvas(canvas?'':'combined');}}><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M4 24h24M5 21h5l3-14 3 14h3l2-7 3 7h3M21 4l6 3v6l-6 3-6-3V7Z"/></svg></button></div>
    {error&&<p className="mc-xrd-error" role="alert">{error}</p>}
    <div className="mc-xrd-main">{snapshot&&host?<Settings key={owner} card={snapshot.card} host={host} level="workspace" definition={{config_schema:{properties:{}}}}/>:<p>正在读取工作台…</p>}</div>
    {canvas&&snapshot&&host&&<section ref={floating} className="mc-xrd-floating" role="region" aria-label="XRD 分析画布" style={{left:"50%",top:"50%",translate:`${position.x}px ${position.y}px`,transform:`translate(-50%,-50%) scale(${zoom})`}}>
      <header onPointerDown={drag}><span className="mc-xrd-grip">⠿</span><strong>谱图与晶体结构</strong><div><button onClick={()=>setZoom(z=>Math.max(.65,z-.1))} aria-label="缩小画布">−</button><button onClick={()=>setZoom(1)}>{Math.round(zoom*100)}%</button><button onClick={()=>setZoom(z=>Math.min(1.3,z+.1))} aria-label="放大画布">＋</button><button onClick={()=>setCanvas('')} aria-label="关闭画布">×</button></div></header>
      <div className="mc-xrd-floating-body mc-xrd-combined">{['spectrum','structure'].map(type=><div className={'mc-xrd-combined-'+type} key={type}><FrameCanvas card={{id:`${owner}-${type}`,type:`xrd.${type}-canvas`,config:{source_node_id:owner},status:snapshot.card.status}} host={host} level="workspace" definition={{}}/></div>)}</div>
    </section>}
  </div>;
}
export function mountXrd(panel,options){const root=createRoot(panel);root.render(<Workbench {...options}/>);return()=>root.unmount();}
