import { NewWorkflowDialog } from './NewWorkflowDialog';
import { createPortal } from 'react-dom';
import {ScoreBars,ActionIcon} from './WorkflowVisuals';
import { useEffect, useId, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import type { PluginViewProps } from '../sdk';
import { StructureCanvas } from './CandidateStructure';
import { resolveMultiphaseSelection, selectMultiphasePhase, updateMultiphaseMatchRun, useMultiphaseCanvas, useMultiphaseCanvasPolling, type MultiphaseDisplay } from './MultiphaseCanvasState';
import './frames.css';
import { CanvasTransition, CanvasReady, useCanvasLoading } from './CanvasTransition';
import { readFrameInfo } from './frameInfo';

export type ScientificFrame = {candidate_id:string;label:string;iteration?:number;state:string;error?:string;quality?:number;
  cif?:{filename:string;source_base64:string};peaks?:{two_theta:number;intensity:number}[];calculated?:number[][];
  contributions?:{candidate_id:string;label:string;points:number[][];color?:string}[];joint?:boolean;
  metric?:{name:string;value:number|null;unit:string}};
export type FrameSet = {run_id:string;stage:string;observed:number[][];frames:ScientificFrame[]};
type Cursor = {run:string;index:number;follow:boolean};
const cursors = new Map<string, Cursor>();
const datasets = new Map<string,FrameSet>();
export function resetWorkflowCanvas(source:string){datasets.delete(source);cursors.delete(source);setParameterMode(source,true);listeners.forEach(fn=>fn());}
const listeners = new Set<()=>void>();
const parameterModes=new Map<string,boolean>();
export function setParameterMode(source:string,value:boolean){if(parameterModes.get(source)===value)return;parameterModes.set(source,value);listeners.forEach(fn=>fn());}
export function useParameterMode(source:string){return useSyncExternalStore(fn=>{listeners.add(fn);return()=>listeners.delete(fn);},()=>parameterModes.get(source)??true);}
const empty:Cursor = {run:'',index:0,follow:true};
const stateLabels:Record<string,string>={initial:'初始参考',best_evaluation:'改善状态',iteration:'迭代状态',final:'最终输出',fallback:'回退原始结构',failed:'失败'};
function current(id:string) { return cursors.get(id) ?? empty; }
function change(id:string, cursor:Cursor) {cursors.set(id,cursor); listeners.forEach(fn=>fn());}
export function useCursor(id:string) {return useSyncExternalStore(fn=>{listeners.add(fn);return()=>listeners.delete(fn);},()=>current(id));}
export function selectedFrame(id:string) {const data=datasets.get(id);return data?.frames[Math.min(current(id).index,data.frames.length-1)];}
export function selectFrameCandidate(id:string,candidate:string) {const data=datasets.get(id);const index=data?.frames.findIndex(f=>f.candidate_id===candidate)??-1;if(data&&index>=0)change(id,{run:data.run_id,index,follow:false});}
export function qualityColor(value?:number) {return value == null || !Number.isFinite(value) ? '#77766d' : `hsl(${Math.max(0,Math.min(1,value))*120} 65% 55%)`;}

export function FrameTimeline({source, data, toolbar, phases}:{source:string;data?:FrameSet;phases?:Record<string,string>;toolbar?:ReactNode;host:PluginViewProps['host']}) {
  const helpId=useId();
  const cursor=useCursor(source);const [playing,setPlaying]=useState(false);
  const loading=useCanvasLoading(source);

  const frames=data?.frames ?? []; const index=Math.min(Math.max(cursor.run===data?.run_id?cursor.index:0,0),Math.max(0,frames.length-1));
  const frame=frames[index];
  useEffect(()=>{if(data){datasets.set(source,data);const c=current(source);change(source,{run:data.run_id,index:c.run===data.run_id&&!c.follow?Math.min(c.index,Math.max(0,data.frames.length-1)):data.stage==='search'?0:Math.max(0,data.frames.length-1),follow:c.run===data.run_id?c.follow:data.stage!=='search'});}},[source,data?.run_id,frames.length]);
  useEffect(()=>{if(!playing||!data)return;const timer=setInterval(()=>{const c=current(source);if(c.index>=frames.length-1){setPlaying(false);return;}change(source,{run:data.run_id,index:c.index+1,follow:false});},700);return()=>clearInterval(timer);},[playing,data?.run_id,frames.length,source]);
  const move=(value:number)=>{if(data)change(source,{run:data.run_id,index:value,follow:false});};
  return <section className="xrd-frame-timeline" aria-label="同步帧时间轴">
    <div className="xrd-frame-heading"><div className="xrd-frame-summary">{frames.length?<><span className="xrd-frame-help" tabIndex={0} aria-label={`当前帧 ${index+1} / ${frames.length}，评分说明`} aria-describedby={helpId}><span>{index+1} / {frames.length}</span><span className="xrd-frame-help-bubble" role="tooltip" id={helpId}>
      {frame?.metric&&<span>{frame.metric.name}：{frame.metric.value?.toFixed(3)??'—'} {frame.metric.unit}</span>}
      <span>柱高和颜色使用固定 0–100 质量分；低分红色、中间黄色、高分绿色，空心表示未评分。质量分不是结构正确概率，阶段间不可直接比较。</span>
      {data?.stage==='fit'&&<span>质量分映射：100 × 1 / (1 + Rwp / 100)。</span>}{data?.stage==='preopt'&&<span>质量分映射：100 × 1 / (1 + √峰残差目标)。记录改善目标的评估状态；最终帧标记采用或回退。</span>}
    </span></span><small>{data?.stage==='search'?' · 匹配结果':<> · {frame.label} · {stateLabels[frame.state]??frame.state}{frame.iteration!=null?' '+frame.iteration:''}</>}</small></>:<small>运行后生成帧；旧结果需重新运行当前阶段以建立帧记录</small>}</div>{toolbar}</div>
    <div className="xrd-frame-controls"><button type="button" title={playing?'暂停回放':'播放帧'} aria-label={playing?'暂停回放':'播放帧'} disabled={frames.length<2} onClick={()=>{if(!playing&&index===frames.length-1)move(0);setPlaying(!playing);}}><ActionIcon name={playing?'pause':'play'}/></button><button type="button" title="上一帧" aria-label="上一帧" disabled={!frames.length||!index} onClick={()=>{setPlaying(false);move(index-1);}}><ActionIcon name="back"/></button>
    <ScoreBars loading={loading} phases={data?.stage==='search'?phases:undefined} frames={frames} index={index} onSelect={i=>{setPlaying(false);move(i);}}/>
    <button type="button" title="下一帧" aria-label="下一帧" disabled={!frames.length||index>=frames.length-1} onClick={()=>{setPlaying(false);move(index+1);}}><ActionIcon name="next"/></button><button type="button" title="跟随实时" aria-label="跟随实时" disabled={!frames.length} aria-pressed={cursor.follow} onClick={()=>{setPlaying(false);if(data)change(source,{run:data.run_id,index:frames.length-1,follow:true});}}><ActionIcon name="follow"/></button></div>
    {frame?.error&&<p role="alert">{frame.error}</p>}
  </section>;
}

/** Shared spectrum renderer: fixed experimental samples, candidate sticks or fitted profile. */
export function SpectrumCanvas({observed,frame}:{observed:number[][];frame?:ScientificFrame}) {
  const [range,setRange]=useState<[number,number]>();const [hover,setHover]=useState<number>();
  const [expanded,setExpanded]=useState(false);
  const [hidden,setHidden]=useState<Record<string,boolean>>({});
  const drag=useRef<{id:number;x:number;lo:number;hi:number;scale:number}>();
  const [panning,setPanning]=useState(false);
  const dialogRef=useRef<HTMLDialogElement>(null);
  const expandButton=useRef<HTMLButtonElement>(null);
  const hasExpanded=useRef(false);
  useEffect(()=>{if(expanded){hasExpanded.current=true;dialogRef.current?.showModal();}else if(hasExpanded.current)expandButton.current?.focus();},[expanded]);
  const svgRef=useRef<SVGSVGElement>(null);const [saveError,setSaveError]=useState('');const [saved,setSaved]=useState(false);
  const saveImage=async()=>{setSaveError('');let url='';try{
    const svg=svgRef.current;if(!svg)return;const copy=svg.cloneNode(true) as SVGSVGElement;
    copy.setAttribute('xmlns','http://www.w3.org/2000/svg');copy.setAttribute('width','1600');copy.setAttribute('height','640');copy.style.color=getComputedStyle(svg).color;
    const originals=svg.querySelectorAll('*');copy.querySelectorAll('*').forEach((element,i)=>{const style=getComputedStyle(originals[i]);for(const key of ['stroke','fill','color'])element.setAttribute(key,style.getPropertyValue(key));});
    url=URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(copy)],{type:'image/svg+xml;charset=utf-8'}));
    const image=new Image();image.src=url;await image.decode();
    const canvas=document.createElement('canvas');canvas.width=1600;canvas.height=640;const ctx=canvas.getContext('2d');if(!ctx)throw Error('无法生成图像');
    ctx.fillStyle=getComputedStyle(svg.closest('.xrd-unified-spectrum')!).backgroundColor;ctx.fillRect(0,0,1600,640);ctx.drawImage(image,0,0,1600,640);
    const blob=await new Promise<Blob>((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(Error('图像导出失败')),'image/png'));
    const download=URL.createObjectURL(blob);const link=document.createElement('a');link.href=download;link.download='XRD-spectrum.png';document.body.appendChild(link);link.click();link.remove();setSaved(true);setTimeout(()=>URL.revokeObjectURL(download),1000);
  }catch(e){setSaveError(String(e));}finally{if(url)URL.revokeObjectURL(url);}};
  if(observed.length<2)return <p>尚无实验谱</p>;
  const full:[number,number]=[observed[0][0],observed[observed.length-1][0]];
  const [lo,hi]=range??full;const max=(frame?.calculated??[]).reduce((m,p)=>Math.max(m,p[1]),observed.reduce((m,p)=>Math.max(m,p[1]),1));
  const x=(v:number)=>45+(v-lo)/(hi-lo)*710;const y=(v:number)=>270-v/max*225;
  const line=(points:number[][])=>points.filter(p=>p[0]>=lo&&p[0]<=hi).map(p=>`${x(p[0])},${y(p[1])}`).join(' ');
  const peaks=frame?.peaks??[];const peakMax=Math.max(1,...peaks.map(p=>p.intensity));
  const colors=['#95b39a','#c2a1d1','#dbba79','#89b8c8','#d69ca4','#a4b47d'];
  const near=hover==null?undefined:observed.reduce((a,b)=>Math.abs(a[0]-hover)<Math.abs(b[0]-hover)?a:b);
  const series=[{id:'observed',label:'实验谱',color:'var(--xrd-observed, #b9d8dd)'},...(frame?[{id:'calculated',label:frame.calculated?'联合计算谱':'参考峰',color:'var(--xrd-calculated, #e99b71)'}]:[]),...(frame?.contributions??[]).map((p,i)=>({id:p.candidate_id,label:p.label,color:p.color??colors[i%colors.length]}))];
  const content = <div className={`xrd-unified-spectrum nodrag nopan nowheel ${expanded?'is-expanded':''}`}><div className="xrd-spectrum-tools"><button type="button" onClick={()=>setRange(undefined)}>复位范围</button><span className="xrd-spectrum-tool-actions"><button type="button" aria-label="保存当前谱图" title={saved?'已生成 PNG · 再次保存当前谱图':'另存为当前谱图（PNG）'} onClick={()=>void saveImage()}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M4 3h13l4 4v14H3V3Z M7 3v7h10V3 M7 21v-7h10v7"/></svg></button><button ref={expandButton} type="button" aria-label={expanded?'关闭放大谱图':'放大谱图'} title={expanded?'关闭放大谱图':'放大谱图'} onClick={()=>setExpanded(!expanded)}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d={expanded?'M6 6l12 12M6 18L18 6':'M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5'}/></svg></button></span></div>{saveError&&<p role="alert">{saveError}</p>}<div className="xrd-spectrum-layout"><aside className="xrd-spectrum-switches" aria-label="谱线显示">{series.map(item=><label key={item.id}><i style={{background:item.color}}/><span>{item.label}</span><input type="checkbox" role="switch" aria-label={item.label} checked={!hidden[item.id]} onChange={event=>setHidden({...hidden,[item.id]:!event.target.checked})}/></label>)}</aside><svg style={{cursor:panning?'grabbing':'grab'}} ref={svgRef} viewBox="0 0 800 320" role="img" aria-label="同步 XRD 谱画布" onDoubleClick={()=>setRange(undefined)}
    onWheel={e=>{e.stopPropagation();const width=Math.min(full[1]-full[0],Math.max(.2,(hi-lo)*(e.deltaY>0?1.2:.8)));const rect=e.currentTarget.getBoundingClientRect();const fraction=Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width));const center=lo+(hi-lo)*fraction;const left=Math.max(full[0],Math.min(full[1]-width,center-width*fraction));setRange([left,left+width]);}}
    onPointerDown={e=>{if(e.button!==0)return;e.preventDefault();e.stopPropagation();const rect=e.currentTarget.getBoundingClientRect();drag.current={id:e.pointerId,x:e.clientX,lo,hi,scale:800/(rect.width*710)};e.currentTarget.setPointerCapture(e.pointerId);setPanning(true);}}
    onPointerUp={e=>{if(drag.current?.id!==e.pointerId)return;drag.current=undefined;setPanning(false);if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId);}}
    onPointerCancel={()=>{drag.current=undefined;setPanning(false);}}
    onLostPointerCapture={()=>{drag.current=undefined;setPanning(false);}}
    onPointerMove={e=>{const d=drag.current;if(d&&d.id===e.pointerId){const shift=(e.clientX-d.x)*d.scale*(d.hi-d.lo);setRange([d.lo-shift,d.hi-shift]);setHover(undefined);return;}const rect=e.currentTarget.getBoundingClientRect();setHover(lo+((e.clientX-rect.left)/rect.width*800-45)/710*(hi-lo));}} onPointerLeave={()=>setHover(undefined)}>

    {!hidden.observed&&<polyline points={line(observed)} fill="none" stroke="var(--xrd-observed, #b9d8dd)" strokeWidth="1.4"/>}
    {!hidden.calculated&&frame?.calculated&&<polyline points={line(frame.calculated)} fill="none" stroke="var(--xrd-calculated, #e99b71)" strokeWidth="1.3"/>}
    {frame?.contributions?.map((phase,i)=>!hidden[phase.candidate_id]&&<polyline key={phase.candidate_id} points={line(phase.points)} fill="none" stroke={phase.color??colors[i%colors.length]} strokeWidth="1.1" opacity=".9" data-phase-id={phase.candidate_id}><title>{phase.label} · 物相贡献</title></polyline>)}
    {!hidden.calculated&&peaks.filter(p=>p.two_theta>=lo&&p.two_theta<=hi).map((p,i)=><line key={i} x1={x(p.two_theta)} x2={x(p.two_theta)} y1={270} y2={270-p.intensity/peakMax*200} stroke="var(--xrd-calculated, #e99b71)"><title>{p.two_theta.toFixed(4)}° · {p.intensity}</title></line>)}
    {Array.from({length:6},(_,i)=>lo+(hi-lo)*i/5).map(v=><text key={v} x={x(v)} y="298" fill="currentColor" textAnchor="middle" fontSize="12">{v.toFixed(2)}</text>)}
    {near&&<><line x1={x(near[0])} x2={x(near[0])} y1="25" y2="270" stroke="#acb6b8" strokeDasharray="3 4"/><text x="50" y="20" fill="currentColor" fontSize="13">2θ {near[0].toFixed(4)}° · I {near[1].toFixed(2)}</text></>}
  </svg></div>{Boolean(frame?.contributions?.length)&&<div className="xrd-canvas-phase-legend">{frame!.contributions!.map((phase,i)=><span key={phase.candidate_id}><i style={{background:phase.color??colors[i%colors.length]}}/>{phase.label}</span>)}</div>}</div>;
  return expanded?createPortal(<dialog ref={dialogRef} className="xrd-spectrum-dialog nodrag nopan nowheel" aria-label="放大 XRD 谱图" onCancel={()=>setExpanded(false)} onClose={()=>setExpanded(false)}>{content}</dialog>,document.body):content;
}


function MultiphaseCanvasContent({source,structure,display,phaseId,stale,onReady}:{source:string;structure:boolean;display?:MultiphaseDisplay;phaseId?:string;stale:boolean;onReady():void}) {
  const phase = phaseId ?? (structure ? display?.candidate_ids[0] : undefined);
  const record = display?.structures?.find(item=>item.candidate_id===phase);
  const failed = display?.status==='failed' || display?.status==='error';
  const phaseColors=['#95b39a','#c2a1d1','#dbba79','#89b8c8','#d69ca4','#a4b47d'];
  const contributions=display?.plot?.contributions?.map((item,index)=>({...item,color:phaseColors[index%phaseColors.length]})).filter(item=>!phase||item.candidate_id===phase);
  return <CanvasReady ready={onReady} disabled={Boolean(structure&&!stale&&!failed&&record?.cif?.source_base64)}>
    <header className="xrd-multiphase-canvas-heading"><div><strong>{structure?'多相结构':'多相谱对比'}</strong></div>{display&&<label className="xrd-canvas-phase-picker"><span>查看物相</span><select aria-label={structure?'结构画布物相':'谱画布物相'} value={phase??'all'} onChange={event=>selectMultiphasePhase(source,event.target.value)}>{!structure&&<option value="all">全部物相 · 联合谱</option>}{display.candidate_ids.map((id,index)=><option key={id} value={id}>{display.labels?.[index]??id}</option>)}</select></label>}</header>
    {stale?<p>先前多相运行属于其他检索，当前画布等待本轮结果。</p>:!display?<p>组合完成一次拟合后，谱线和对应结构会在这里同步显示。</p>:failed?<p role="alert">本组合拟合失败：{display.error??'暂无有效拟合谱线和结构。'}</p>:structure?<>
      {record?.cif?.source_base64?<StructureCanvas structure={record.cif} onReady={onReady}/>:<p>{record?.error??'本次拟合尚未提供这个物相的 CIF。'}</p>}
    </>:display.plot?<>
      <SpectrumCanvas observed={display.plot.observed} frame={{candidate_id:display.trialId??`review-${display.reviewIndex}`,label:display.caption,state:display.status,calculated:display.plot.calculated,contributions,joint:true}}/>
      {phase&&!contributions?.length&&<small>该次拟合未提供所选物相的独立谱线；当前显示实验谱与联合计算谱。</small>}
    </>:<p>本组合尚无拟合谱线。</p>}
  </CanvasReady>;
}

type ExperimentalDocument={filename?:string;points?:number[][]};
function ExperimentalSpectrum({host,source,raw,data,frame}:{host:PluginViewProps['host'];source:string;raw:boolean;data?:FrameSet;frame?:ScientificFrame}){

  const [doc,setDoc]=useState<ExperimentalDocument>();const [inputId,setInputId]=useState('');
  const [error,setError]=useState('');
  useEffect(()=>{let active=true;let timer:ReturnType<typeof setTimeout>;
    const load=async()=>{try{const inputs=await host.getInputs?.(source);const patterns=inputs?.filter(i=>i.kind==='pattern')??[];
      if(patterns.length>1)throw Error('连接了多个实验谱，请先保留一个');
      const id=patterns[0]?.id;const value=id?(await host.readDocument(id)).value as ExperimentalDocument:undefined;
      if(active){setInputId(id??'');setDoc(value);}
    }catch(e){if(active)setError(String(e));}finally{if(active)timer=setTimeout(load,2500);}};
    if(source)void load();return()=>{active=false;clearTimeout(timer);};
  },[host,source]);
  return <div className="xrd-spectrum-input">
    {raw&&doc?.filename&&<small>{doc.filename}</small>}
    <SpectrumCanvas observed={raw?doc?.points??[]:data?.observed??[]} frame={raw?undefined:frame}/>
  </div>;
}

export function FrameCanvas(props:PluginViewProps) {
  if(props.level==='preview'||props.level==='node')return <section className="xrd-panel"><strong>{props.card.type==='xrd.spectrum-canvas'?'实验谱与拟合谱':'晶体结构'}</strong><small>打开工作区查看</small></section>;
  return <ActiveFrameCanvas {...props}/>;
}

function ActiveFrameCanvas({card,host}:PluginViewProps) {
  const [pendingFile,setPendingFile]=useState<File>();const filePicker=useRef<HTMLInputElement>(null);
  const [importError,setImportError]=useState('');const importing=useRef(false);const [uploading,setUploading]=useState(false);const [dragging,setDragging]=useState(false);
  const importSpectrum=async(file:File)=>{
    if(importing.current)return;
    importing.current=true;setUploading(true);setImportError('');
    try{
      if(!/\.(txt|csv|ras)$/i.test(file.name))throw Error('请拖入 TXT、CSV 或 RAS 实验谱文件');
      if(file.size>8*1024*1024)throw Error('实验谱文件最大 8 MiB');
      if(host.startXrdWorkflow){await host.startXrdWorkflow(file);setPendingFile(undefined);if(source)setParameterMode(source,true);return;}
      const id=await host.ensureXrdInput?.('pattern');if(!id)throw Error('无法创建实验谱输入');
      const doc=await host.readDocument(id);const bytes=new Uint8Array(await file.arrayBuffer());let binary='';
      for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
      await host.documentAction('import',{filename:file.name,source_base64:btoa(binary)},doc.revision,id);
      if(source)setParameterMode(source,true);setPendingFile(undefined);
    }catch(e){setImportError(String(e));}finally{importing.current=false;setUploading(false);}
  };
  const source=String(card.config.source_node_id??'');const cursor=useCursor(source);
  const raw=useParameterMode(source);
  const multi=useMultiphaseCanvas(source);
  useMultiphaseCanvasPolling(source,host.getMultiphase);
  const multiActive=multi.active&&!raw;
  const multiStale=Boolean(multi.currentMatchRunId&&multi.state?.source_match_run_id&&multi.currentMatchRunId!==multi.state.source_match_run_id);
  const selectedDisplay=multiActive&&!multiStale?resolveMultiphaseSelection(multi.state,multi.selection):undefined;
  const detailKey=JSON.stringify([multi.state?.run_id,selectedDisplay?.lane,selectedDisplay?.trialId,selectedDisplay?.reviewIndex,selectedDisplay?.iteration,selectedDisplay?.status]);
  const [detail,setDetail]=useState<{key:string;value:{plot?:MultiphaseDisplay['plot'];structures?:MultiphaseDisplay['structures']}}>();
  useEffect(()=>{
    if(!multiActive||!selectedDisplay||selectedDisplay.plot||!host.getMultiphaseFrame||!multi.state?.run_id)return;
    let cancelled=false;
    void host.getMultiphaseFrame({run_id:multi.state.run_id,lane:selectedDisplay.lane,trial_id:selectedDisplay.trialId,review_index:selectedDisplay.reviewIndex})
      .then(value=>{if(!cancelled)setDetail({key:detailKey,value});}).catch(reason=>{if(!cancelled)setImportError(String(reason));});
    return()=>{cancelled=true;};
  },[detailKey,host,multiActive]);
  const multiDisplay=selectedDisplay&&detail?.key===detailKey?{...selectedDisplay,...detail.value}:selectedDisplay;
  const [sets,setSets]=useState<Record<string,FrameSet|null>>({});const [latest,setLatest]=useState<FrameSet>();const [error,setError]=useState('');
  useEffect(()=>{if(!source||multiActive)return;let active=true;let timer:ReturnType<typeof setTimeout>;let retry=true;const load=async()=>{try{const info=await readFrameInfo(source,()=>host.getAgentInfo(source));retry=['running','waiting','starting'].includes(String((info.details?.last_run as {status?:string}|undefined)?.status??''));if(active){setSets((info.details?.frame_sets??{}) as Record<string,FrameSet>);setLatest(info.details?.frames as FrameSet);updateMultiphaseMatchRun(source,(info.details?.workflow as {match_run_id?:string}|undefined)?.match_run_id);setError('');}}catch(e){if(active)setError(String(e));}finally{if(active&&retry)timer=setTimeout(load,3000);}};void load();return()=>{active=false;clearTimeout(timer);};},[host,source,multiActive,cursor.run]);
  const data=Object.values(sets).find(s=>s?.run_id===cursor.run)??datasets.get(source)??(cursor.run?undefined:latest);
  const index=cursor.run===data?.run_id&&!cursor.follow?cursor.index:Math.max(0,(data?.frames.length??1)-1);const frame=data?.frames[index];
  return <section className={`xrd-independent-canvas nodrag nopan ${dragging?'is-dragging':''}`} aria-busy={uploading}
    onDragOver={e=>{if(card.type!=='xrd.spectrum-canvas'||!Array.from(e.dataTransfer.types).includes('Files'))return;e.preventDefault();e.stopPropagation();e.dataTransfer.dropEffect=uploading?'none':'copy';setDragging(true);}}
    onDragLeave={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node|null))setDragging(false);}}
    onDrop={e=>{if(card.type!=='xrd.spectrum-canvas')return;e.preventDefault();e.stopPropagation();setDragging(false);if(e.dataTransfer.files.length!==1){setError('每次请拖入一个实验谱文件');return;}const file=e.dataTransfer.files[0];if(!/\.(txt|csv|ras)$/i.test(file.name)||file.size>8*1024*1024){setImportError('请选择不超过 8 MiB 的 TXT、CSV 或 RAS 实验谱');return;}if(!uploading)setPendingFile(file);}}>{!multiActive&&<header><strong>{raw&&card.type==='xrd.spectrum-canvas'?'实验谱':raw?'结构画布':frame?.label??'同步画布'}</strong><small>{raw?'参数设置':`${data?.stage??''} · ${data?.frames.length?`${index+1} / ${data.frames.length}`:'等待帧'}`}</small></header>}
    {card.type==='xrd.spectrum-canvas'&&<input ref={filePicker} type="file" hidden accept=".txt,.csv,.ras" onChange={event=>{const file=event.target.files?.[0];event.target.value='';if(file&&!uploading)setPendingFile(file);}}/>}
    {pendingFile&&<NewWorkflowDialog file={pendingFile} busy={uploading} error={importError} onCancel={()=>{if(!uploading){setPendingFile(undefined);setImportError('');}}} onConfirm={()=>void importSpectrum(pendingFile)}/>}
    {importError&&!pendingFile&&<p role="alert">{importError}</p>}{card.type==='xrd.spectrum-canvas'&&(!multiActive||uploading)&&<small role="status">{uploading?'正在导入实验谱…':<button type="button" className="xrd-spectrum-import" onClick={()=>filePicker.current?.click()}>拖拽或选择 TXT / CSV / RAS 实验谱</button>}</small>}
    {multiActive&&multi.connectionError&&<small role="alert">{multi.connectionError}</small>}
    <CanvasTransition source={source} identity={multiActive?JSON.stringify([source,'multi',multiDisplay?.lane,multiDisplay?.trialId,multiDisplay?.reviewIndex,multiDisplay?.iteration,multiDisplay?.status,multi.phaseId]):`${source}:${raw}:${data?.run_id}:${index}:${frame?.cif?.source_base64??''}`}>
      {ready => !source?<CanvasReady ready={ready}><p>从检索与比对节点打开同步画布。</p></CanvasReady>:multiActive?<MultiphaseCanvasContent source={source} structure={card.type==='xrd.structure-canvas'} display={multiDisplay} phaseId={multi.phaseId} stale={multiStale} onReady={ready}/>:card.type==='xrd.structure-canvas'&&!raw&&frame?.cif?<StructureCanvas structure={frame.cif} onReady={ready}/>:<CanvasReady ready={ready}>{card.type==='xrd.structure-canvas'?<p>{raw?'检索后显示当前候选的结构。':frame?.error??'本帧尚无结构；不会显示其他候选的 CIF。'}</p>:<ExperimentalSpectrum host={host} source={source} raw={raw} data={data} frame={frame}/>}</CanvasReady>}
    </CanvasTransition>
    {!multiActive&&!raw&&frame?.state==='fallback'&&<p>预优化未被接受：当前显示原始 CIF。</p>}{!multiActive&&!raw&&frame?.error&&<p role="alert">{frame.error}</p>}{error&&<p role="alert">{error}</p>}
  </section>;
}
