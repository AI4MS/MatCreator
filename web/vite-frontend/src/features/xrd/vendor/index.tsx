import { LibraryView } from './LibraryView';
import { useEffect, useState, useRef, useId } from "react";
import type { FrontendPlugin, PluginViewProps } from "../sdk";
import { CandidateStructure, StructureCanvas, codCandidateId } from "./CandidateStructure";
import "./style.css";
import { MatchControls, Glyph } from "./MatchControls";
import { PipelineWorkflow } from "./PipelineView";
import { FrameCanvas, SpectrumCanvas, useCursor, selectedFrame, selectFrameCandidate } from './FrameCanvas';
type Peak = { two_theta: number; intensity: number; hkl?: number[] };
type Match = { observed_index:number; observed: number; reference: number; delta: number; hkl: number[] };
type Candidate = { node_id: string; filename: string; metadata: Record<string,string>; peaks: Peak[]; matches: Match[]; reference_count: number; mean_abs_delta: number|null; score?:number; cifs: {filename:string}[] };
type LibrarySearch = { enabled:boolean; engine?:'qualx'|'native'; scanned:number; invalid_records:number; elapsed_seconds:number; top_n:number; allowed_elements?:string[]; excluded_by_elements?:number; total_records?:number; screened_candidates?:number; scored_records?:number; retrieval_seconds?:number; ranking_method?:string; engine_runs?:{settings?:Record<string,unknown>}[] };
export type Result = { mode?:string; solver?:unknown; converged?:boolean; pattern?:{filename:string;points:number[][]}; candidates?:Candidate[]; observed_peaks?:Peak[]; unexplained_peaks?:Peak[]; interpretation?:string; parameters?:Record<string,number>; library_search?:LibrarySearch };
type Input = { kind:string; filename:string; source_base64:string; sha256:string; metadata:Record<string,string>; points:number[][]; peaks:Peak[]; reference_node_id:string };

function Plot({points=[],peaks=[],unexplained=[],interactive=false}:{points?:number[][];peaks?:Peak[];unexplained?:Peak[];interactive?:boolean}) {
  const [hover,setHover]=useState<number|null>(null);
  const [peakHover,setPeakHover]=useState<{peak:Peak;label:string}|null>(null);
  const angles=points.length?[points[0][0],points[points.length-1][0]]:peaks.map(p=>p.two_theta);
  const lo=Math.min(...angles),hi=Math.max(...angles);
  if(!Number.isFinite(lo)||hi<=lo)return null;
  const max=points.reduce((m,p)=>Math.max(m,p[1]),1),peakMax=peaks.reduce((m,p)=>Math.max(m,p.intensity),1);
  const x=(v:number)=>42+(v-lo)/(hi-lo)*620;
  const selected=interactive&&hover!==null?points[hover]:undefined;
  const pointer=(event:React.PointerEvent<SVGSVGElement>)=>{
    if(!interactive||!points.length||peakHover)return;
    const matrix=event.currentTarget.getScreenCTM();if(!matrix)return;
    const local=new DOMPoint(event.clientX,event.clientY).matrixTransform(matrix.inverse());
    if(local.x<42||local.x>662||local.y<15||local.y>200){setHover(null);return;}
    const angle=lo+(local.x-42)/620*(hi-lo);
    let left=0,right=points.length-1;
    while(left<right){const mid=(left+right)>>1;if(points[mid][0]<angle)left=mid+1;else right=mid;}
    const index=left>0&&angle-points[left-1][0]<points[left][0]-angle?left-1:left;
    setHover(index);
  };
  return <svg onPointerMove={pointer} onPointerLeave={()=>{setHover(null);setPeakHover(null);}} className={`xrd-plot ${interactive?'is-interactive':''}`} viewBox="0 0 690 250" role="img" aria-label="实验谱与标准峰叠加图">
    <line x1="42" x2="662" y1="192" y2="192" stroke="currentColor" opacity=".4"/>
    {!!points.length&&<polyline fill="none" stroke="var(--xrd-observed, #b9d8dd)" strokeWidth="1.3" points={points.map(p=>`${x(p[0]).toFixed(2)},${192-p[1]/max*165}`).join(' ')}/>}
    {peaks.filter(p=>p.two_theta>=lo&&p.two_theta<=hi).map((p,i)=><line key={i} x1={x(p.two_theta)} x2={x(p.two_theta)} y1="192" y2={192-p.intensity/peakMax*120} stroke="#e5aa76" strokeWidth="1.5" onPointerEnter={()=>{if(interactive){setHover(null);setPeakHover({peak:p,label:"标准峰"});}}} onPointerLeave={()=>setPeakHover(null)} style={{pointerEvents:"stroke",cursor:interactive?"crosshair":undefined}}><title>{p.two_theta.toFixed(3)}° · ({p.hkl?.join(' ')})</title></line>)}
    {unexplained.map((p,i)=><circle key={i} cx={x(p.two_theta)} cy="205" r="3" fill="#ea8c91" onPointerEnter={()=>{if(interactive){setHover(null);setPeakHover({peak:p,label:"未解释峰"});}}} onPointerLeave={()=>setPeakHover(null)}><title>未解释峰 {p.two_theta.toFixed(3)}°</title></circle>)}
    {Array.from({length:6},(_,i)=>lo+(hi-lo)*i/5).map(v=><text key={v} x={x(v)} y="225" textAnchor="middle" fill="currentColor" fontSize="12">{v.toFixed(1)}</text>)}
    {peakHover&&<g role="tooltip" pointerEvents="none" transform={`translate(${Math.min(452,Math.max(44,x(peakHover.peak.two_theta)+10))},30)`}>
      <rect width="210" height="78" rx="6" fill="#182127" stroke="#c19875"/>
      <text x="10" y="19" fill="#f1eee8" fontSize="12">{peakHover.label} · 2θ {peakHover.peak.two_theta.toFixed(4)}°</text>
      <text x="10" y="41" fill="#f1eee8" fontSize="12">强度：{peakHover.peak.intensity}</text>
      <text x="10" y="63" fill="#f1eee8" fontSize="12">h k l：{peakHover.peak.hkl?.length?peakHover.peak.hkl.join(' '):'未提供'}</text>
    </g>}
    {selected&&<g pointerEvents="none">
      <line x1={x(selected[0])} x2={x(selected[0])} y1="20" y2="192" stroke="#e5aa76" strokeDasharray="3 3" opacity=".7"/>
      <circle cx={x(selected[0])} cy={192-selected[1]/max*165} r="3.5" fill="#e5aa76"/>
      <g role="tooltip" transform={`translate(${Math.min(478,Math.max(44,x(selected[0])+12))},${Math.max(8,192-selected[1]/max*165-62)})`}>
        <rect width="182" height="54" rx="6" fill="#182127" stroke="#c19875"/>
        <text x="10" y="21" fill="#f1eee8" fontSize="12">2θ：{selected[0].toFixed(4)}°</text>
        <text x="10" y="42" fill="#f1eee8" fontSize="12">原始强度：{Number(selected[1].toPrecision(8))}</text>
      </g>
    </g>}
    <text x="350" y="246" textAnchor="middle" fill="currentColor" fontSize="12">2θ / ° · 强度分别归一化</text>
  </svg>;
}
async function encoded(file:File){
  if(file.size>8*1024*1024)throw new Error('文件最大 8 MiB');
  const bytes=new Uint8Array(await file.arrayBuffer());let binary='';
  for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
  return btoa(binary);
}
function InputView({card,host,level}:PluginViewProps){
  const [doc,setDoc]=useState<Input>();const [revision,setRevision]=useState(0);const [error,setError]=useState('');const [busy,setBusy]=useState(false);
  const fileInput=useRef<HTMLInputElement>(null);const importing=useRef(false);const tooltipId=useId();const [dragging,setDragging]=useState(false);
  const [refs,setRefs]=useState<{id:string;name:string}[]>([]);
  useEffect(()=>{let active=true;void host.readDocument().then(d=>{if(active){setDoc(d.value as Input);setRevision(d.revision);}}).catch(e=>{if(active)setError(String(e));});
    if(card.type==='xrd.cif')void host.listCards(['xrd.reference']).then(r=>{if(active)setRefs(r);}).catch(e=>{if(active)setError(String(e));});
    return()=>{active=false;};},[host,card.type]);
  const action=async(name:string,args:Record<string,unknown>)=>{setBusy(true);setError('');try{const d=await host.documentAction(name,args,revision);setDoc(d.value as Input);setRevision(d.revision);}catch(e){setError(String(e));const d=await host.readDocument();setDoc(d.value as Input);setRevision(d.revision);}finally{setBusy(false);}};
  const importFile=async(file:File)=>{
    if(importing.current||busy||!doc)return;
    importing.current=true;setBusy(true);setError('');
    try{await action('import',{filename:file.name,source_base64:await encoded(file)});}
    catch(err){setError(String(err));}finally{importing.current=false;setBusy(false);}
  };
  const dropProps={
    onDragOver:(e:React.DragEvent<HTMLElement>)=>{if(!Array.from(e.dataTransfer.types).includes('Files'))return;e.preventDefault();e.stopPropagation();e.dataTransfer.dropEffect=busy?'none':'copy';setDragging(true);},
    onDragLeave:(e:React.DragEvent<HTMLElement>)=>{if(!e.currentTarget.contains(e.relatedTarget as Node|null))setDragging(false);},
    onDrop:(e:React.DragEvent<HTMLElement>)=>{if(!Array.from(e.dataTransfer.types).includes('Files'))return;e.preventDefault();e.stopPropagation();setDragging(false);if(e.dataTransfer.files.length!==1){setError('每次请拖入一个文件。');return;}void importFile(e.dataTransfer.files[0]);}
  };
  const help=(card.type==='xrd.pattern'?'支持两列 CSV / TXT 或 SmartLab 文本导出谱。':card.type==='xrd.reference'?'支持含 2-Theta、d、I、(h k l) 峰表的文本标准卡片；全谱拟合还需要 CIF。':'导入真实候选结构 CIF，并关联对应的标准卡片。')+' 可从资源管理器拖入一个文件，最大 8 MiB。从算法卡片连接到此对象，选择「XRD 输入」。';
  if(level==='preview')return <section {...dropProps} className={`xrd-panel xrd-drop-panel ${dragging?'is-dragging':''}`}><strong>{doc?.filename||'尚未导入'}</strong><small>{doc?.kind==='pattern'?`${doc.points.length} 个实验点`:doc?.kind==='reference'?`${doc.peaks.length} 条标准峰 · ${doc.metadata.formula||''}`:doc?.reference_node_id?'已关联标准卡片':'待关联标准卡片'}</small><small>{busy?'正在导入…':'拖入文件，或点击查看详情'}</small>{error&&<p role="alert">{error}</p>}</section>;
  return <section {...dropProps} className={`xrd-panel xrd-drop-panel nodrag nopan ${dragging?'is-dragging':''}`} aria-busy={busy}>
    {level!=='workspace'&&<div className="xrd-import-row">
      <span className="xrd-import-control">
        <button type="button" disabled={busy||!doc} aria-describedby={tooltipId} onClick={()=>fileInput.current?.click()}>{busy?'正在导入…':'选择文件'}</button>
        <span className="xrd-import-tooltip" role="tooltip" id={tooltipId}>{help}</span>
      </span>
      <small>{dragging?'松开以导入':'或将文件拖到这里'}</small>
      <input ref={fileInput} type="file" hidden aria-label="导入文件" disabled={busy||!doc} accept={card.type==='xrd.cif'?'.cif':'.txt,.csv,.ras'} onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)void importFile(file);}}/>
    </div>}
    {doc?.filename&&<><strong className="xrd-source-filename">{doc.filename}</strong>
      <div className="xrd-source-row">
        <small>{doc.kind==='pattern'?`${doc.points.length} 个点 · ${doc.metadata.format} · Kα₁ ${doc.metadata.HW_XG_WAVE_LENGTH_ALPHA1||'未提供'} Å`:doc.kind==='reference'?`${doc.metadata.name} · ${doc.metadata.formula} · ${doc.peaks.length} 条标准峰`:'CIF 结构文件'}</small>
        <a className="xrd-source-download" href={host.documentDownloadUrl('source')} download aria-label="下载原始输入" title="下载原始输入">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
        </a>
      </div>
      {doc.kind==='pattern'&&<SpectrumCanvas observed={doc.points}/>}
      {doc.kind==='reference'&&<Plot peaks={doc.peaks}/>}
      {doc.kind==='cif'&&<StructureCanvas structure={doc}/>}
    </>}
    {doc?.kind==='cif'&&<label className="field-label">对应的标准卡片<select disabled={busy} value={doc.reference_node_id} onChange={e=>void action('associate',{reference_node_id:e.target.value}).catch(err=>setError(String(err)))}><option value="">尚未关联</option>{refs.map(r=><option key={r.id} value={r.id}>{r.name}</option>)}</select><small>关联表达候选结构来源，不代表已验证物相身份。算法仍需分别连接标准卡片与 CIF。</small></label>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
function MatchResult({result,host,sourceId=''}:{result:Result;host:PluginViewProps['host'];sourceId?:string}){
  useCursor(sourceId);
  const [selected,setSelected]=useState('');const active=selectedFrame(sourceId)?.candidate_id;
  const c=result.candidates?.find(c=>c.node_id===(active??selected))??result.candidates?.[0];
  const codId=c?codCandidateId(c.metadata):undefined;
  const library=result.library_search;const full=library?.enabled;const qualx=library?.engine==='qualx';
  const matched=new Set(c?.matches.map(m=>m.observed_index));const residual=full?result.observed_peaks?.filter((_,i)=>!matched.has(i)):result.unexplained_peaks;
  return <div className="xrd-panel xrd-match-tables"><header className="xrd-match-result-heading"><strong>标准卡片匹配结果</strong>{c&&<div className="xrd-match-current">{codId?<CandidateStructure key={codId} codId={codId} host={host}/>:c.metadata.url&&<a href={c.metadata.url} target="_blank" rel="noreferrer">查看结构与文献</a>}</div>}</header>
    <div className="xrd-table"><table aria-label="检索概况"><tbody>
      <tr><th scope="row">实验谱</th><td colSpan={3}>{result.pattern?.filename??'—'}</td></tr>
      <tr><th scope="row">检出峰数</th><td>{result.observed_peaks?.length??0}</td><th scope="row">{full?'当前候选':'所有候选合计'}未解释峰</th><td>{residual?.length??0}</td></tr>
      {full&&library&&<>
        <tr><th scope="row">检索方式</th><td>{qualx?'快速检索':'遍历检索'}</td><th scope="row">耗时</th><td>{library.elapsed_seconds.toFixed(2)} 秒</td></tr>
        <tr><th scope="row">谱库条目</th><td>{library.total_records?.toLocaleString()??'—'}</td><th scope="row">{qualx?'初筛候选':'已遍历'}</th><td>{qualx?library.screened_candidates?.toLocaleString()??'—':library.scanned.toLocaleString()}</td></tr>
        <tr><th scope="row">已比对</th><td>{(library.scored_records??library.scanned).toLocaleString()}</td><th scope="row">无效记录</th><td>{library.invalid_records}</td></tr>
        <tr><th scope="row">元素条件排除</th><td>{library.excluded_by_elements?.toLocaleString()??'—'}</td><th scope="row">返回候选</th><td>{result.candidates?.length??0}</td></tr>
      </>}
    </tbody></table></div>
    {qualx&&library?.screened_candidates===0&&c&&<p role="status">本次未召回谱库候选，以下为手动标准卡片的比对结果。</p>}
    {c?<details className="xrd-candidate-comparison" open><summary>候选结构</summary><div className="xrd-table"><table aria-label="候选匹配比较"><thead><tr><th>候选结构</th><th>匹配分数 / 100</th><th>标准峰匹配</th><th>平均 |Δ2θ| / °</th><th>来源</th></tr></thead><tbody>{result.candidates?.map(candidate=><tr key={candidate.node_id} aria-selected={candidate.node_id===c.node_id}><td><button type="button" className="xrd-candidate-row-button" aria-pressed={candidate.node_id===c.node_id} onClick={()=>{setSelected(candidate.node_id);selectFrameCandidate(sourceId,candidate.node_id);}}>{candidate.filename}<small>{candidate.metadata.formula}</small></button></td><td title="匹配分数不是概率">{candidate.score==null?'—':(candidate.score*100).toFixed(1)}</td><td>{candidate.matches.length} / {candidate.reference_count}</td><td>{candidate.mean_abs_delta?.toFixed(4)??'—'}</td><td>{candidate.metadata.source||'用户标准卡片'}</td></tr>)}</tbody></table></div></details>:<p role="status">未找到可比对候选。{qualx?'可调整元素范围，或切换 遍历检索。':'请检查已连接的标准卡片及谱库筛选条件。'}</p>}
    <Plot key={c?.node_id} interactive points={result.pattern?.points} peaks={c?.peaks} unexplained={residual}/>
    {c&&<>
      <details><summary>匹配峰与偏差（{c.matches.length}）</summary><div className="xrd-table"><table><thead><tr><th>实验 / °</th><th>标准 / °</th><th>Δ2θ / °</th><th>h k l</th></tr></thead><tbody>{c.matches.map((m,i)=><tr key={i}><td>{m.observed.toFixed(3)}</td><td>{m.reference.toFixed(3)}</td><td>{m.delta.toFixed(4)}</td><td>{m.hkl.join(' ')}</td></tr>)}</tbody></table></div></details></>}
    <details><summary>未解释峰</summary><p>{residual?.map(p=>`${p.two_theta.toFixed(3)}°`).join('、')||'当前检测阈值下没有未解释峰'}</p></details>
  </div>;
}

function RunParameters({result}:{result:Result}) {
  const labels:Record<string,string>={wavelength:'波长 / Å',tolerance_deg:'峰位容差 ± / °',prominence_fraction:'峰突出度 / 最强峰',reference_min_intensity:'标准峰最低相对强度',smoothing_deg:'平滑宽度 / °',min_peak_distance:'最小峰间距 / °',strongest_peaks:'QualX 强峰数',min_fom:'QualX 最低 FOM',max_entries:'QualX 候选上限',peak_tolerance:'QualX 峰位容差',isolated:'独立配置',oaw_ranking:'OAW 独立排序'};
  const entries:[string,unknown][] = Object.entries(result.parameters??{});
  const lib=result.library_search;
  if(lib?.enabled) entries.push(['检索引擎',lib.engine??'native'],['全库返回候选数',lib.top_n],['允许元素',lib.allowed_elements?.join(' ')||'不限']);
  return <details className="xrd-run-parameters"><summary>上次运行参数（结果快照）</summary>
    <dl>{entries.map(([key,value])=><div key={key}><dt>{labels[key]??key}</dt><dd>{String(value)}</dd></div>)}</dl>
    {lib?.engine_runs?.map((run,i)=><dl key={i} aria-label={`谱库 ${i+1} 引擎参数`}>{Object.entries(run.settings??{}).map(([key,value])=><div key={key}><dt>{labels[key]??key}</dt><dd>{String(value)}</dd></div>)}</dl>)}
    {!entries.length&&<small>此次历史运行未记录参数。</small>}
  </details>;
}

function Settings(props:PluginViewProps) {
  return props.level==='workspace'&&props.card.type==='xrd.match'
    ? <PipelineWorkflow key={String(props.card.config.workflow_started_at_ms??0)} {...props} renderResult={result=><MatchResult result={result} host={props.host} sourceId={props.card.id}/>} renderParameters={result=><RunParameters result={result}/>}/>
    : <LegacySettings {...props}/>;
}
function LegacySettings({card,host,level,definition}:PluginViewProps){
  const [invalid,setInvalid]=useState<Record<string,string>>({});
  const invalidRef=useRef<Record<string,string>>({});
  const [inputs,setInputs]=useState<Awaited<ReturnType<NonNullable<PluginViewProps["host"]["getInputs"]>>>>();
  const [inputError,setInputError]=useState('');
  const [lastStatus,setLastStatus]=useState('');
  const [configOpen,setConfigOpen]=useState(true);
  const resultStamp=useRef<string>();
  const [starting,setStarting]=useState(false);
  const [progress,setProgress]=useState<{percent:number;stage:string;indeterminate?:boolean;completed?:number;total?:number}>();
  const pendingSaves=useRef<Promise<unknown>[]>([]);
  const saveError=useRef<Record<string,string>>({});
  const saveQueue=useRef(Promise.resolve());
  const stepId=useId();
  const running=card.status==='running'||card.status==='waiting';
  const outputFirst=level==='workspace'&&card.type==='xrd.match';
  const [error,setError]=useState('');const [result,setResult]=useState<Result>();const [archive,setArchive]=useState('');const [cifs,setCifs]=useState<{id:string;name:string}[]>([]);const matching=card.config.mode==='match';
  useEffect(()=>{let active=true;void host.getAgentInfo().then(info=>{if(active){const next=info.details?.result as Result|undefined; const stamp=JSON.stringify([info.details?.last_run,next]); if(resultStamp.current!==stamp){setConfigOpen(!next);resultStamp.current=stamp;} if(next)setResult(next);setLastStatus(String((info.details?.last_run as {status?:string}|undefined)?.status??''));setArchive(String(info.details?.archive_url??info.details?.archive??''));}}).catch(e=>{if(active)setError(String(e));});void host.listCards(['xrd.cif']).then(r=>{if(active)setCifs(r);}).catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[host,card.status]);
  useEffect(()=>{
    if(!running)return;
    let active=true;let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{try{const info=await host.getAgentInfo();if(active)setProgress(info.details?.progress as typeof progress);}catch{/* Keep the last reported progress. */}finally{if(active)timer=setTimeout(poll,300);}};
    void poll();return()=>{active=false;clearTimeout(timer);};
  },[running,host]);
  const start=async()=>{setStarting(true);setError('');setProgress(undefined);try{await Promise.all(pendingSaves.current);if(Object.values(saveError.current).some(Boolean)||Object.values(invalidRef.current).some(Boolean))throw new Error('参数有误，请修正后重试');if(!host.runAnalysis)throw new Error('请刷新页面');await host.runAnalysis();}catch(e){setError(String(e));}finally{setStarting(false);}};
  useEffect(()=>{if(card.config.demo)void host.updateConfig({demo:false}).catch(e=>setError(String(e)));},[host,card.config.demo]);
  const save=(patch:Record<string,unknown>)=>{const key=Object.keys(patch).join(',');const task=saveQueue.current.then(()=>host.updateConfig(patch)).then(()=>{delete saveError.current[key];setError(Object.values(saveError.current).filter(Boolean).join('；'));}).catch(e=>{saveError.current[key]=String(e);setError(String(e));});saveQueue.current=task;pendingSaves.current.push(task);void task.finally(()=>{pendingSaves.current=pendingSaves.current.filter(p=>p!==task);});};
  useEffect(()=>{if(!host.getInputs||!matching)return;let active=true;let timer:ReturnType<typeof setTimeout>;const load=async()=>{try{const items=await host.getInputs!();if(active){setInputs(items);setInputError('');}}catch(e){if(active)setInputError(String(e));}finally{if(active)timer=setTimeout(load,4000);}};void load();return()=>{active=false;clearTimeout(timer);};},[host,matching]);
  const missing=inputs?(inputs.filter(i=>i.kind==='pattern').length!==1?'请连接一个实验谱':!inputs.some(i=>i.ready&&i.kind==='pattern')?'实验谱尚未导入':!inputs.some(i=>i.ready&&(i.kind==='reference'||i.kind==='library'))?'请连接已导入的标准卡片或谱库':inputs.some(i=>!i.ready&&i.kind==='reference')?'有标准卡片尚未导入峰表':''):host.getInputs?'正在检查输入':'';
  const invalidMessage=Object.values(invalid).filter(Boolean).join('；');

  const numbers:[string,string][]=matching?[['wavelength','匹配波长 / Å'],['tolerance_deg','峰位容差 ± / °'],['prominence_fraction','峰突出度 / 最强峰（0–1）'],['reference_min_intensity','标准峰最低相对强度']]:[['iterations','最大迭代'],['wavelength','波长 / Å'],['low_angle','晶胞更新峰区间下限 / °'],['high_angle','晶胞更新峰区间上限 / °']];
  return <section className={`xrd-panel nodrag nopan ${matching?'xrd-match-ui':''}`}>
    {outputFirst&&<nav className={`xrd-workflow ${result?'has-result':''} ${running||starting?'is-running':''}`} aria-label="检索步骤">
      <button type="button" aria-current={configOpen?'step':undefined} aria-controls={stepId} onClick={()=>setConfigOpen(true)}><span className="xrd-step-number">01</span><span>参数设置</span></button>
      <span className={`xrd-step-track ${(running||starting)&&(!progress||progress.indeterminate)?'is-indeterminate':''}`} role="progressbar" aria-label="检索阶段进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={(running||starting)&&(!progress||progress.indeterminate)?undefined:running||starting?progress?.percent??0:configOpen?0:100} aria-valuetext={running?progress?.stage??'准备检索':configOpen?'参数设置':'匹配结果'}><i style={{width:`${running||starting?progress?.percent??0:configOpen?0:100}%`}}/></span>
      <button type="button" disabled={!result||running||starting} aria-current={!configOpen?'step':undefined} aria-controls={stepId} onClick={()=>setConfigOpen(false)}><span className="xrd-step-number">02</span><span>{running?'检索中…':'匹配结果'}</span></button>
    </nav>}
    {outputFirst&&(running||starting)&&<small role="status">{progress?.stage??'准备检索'}{progress?.total?` · ${progress.completed??0} / ${progress.total}`:''}</small>}
    <div id={stepId} className="xrd-step-content">
    {(!outputFirst||configOpen)&&<>
    {matching?<MatchControls config={card.config} schema={definition.config_schema??{}} save={save} disabled={running||starting} onInvalid={(key,message)=>{invalidRef.current={...invalidRef.current,[key]:message};setInvalid(invalidRef.current);}} advanced={result?<RunParameters result={result}/>:<small>尚无上次运行参数</small>}/>:<>
    <p>OAW_XRDfit · 给定候选 CIF 的全谱拟合。运行完成不等于收敛或物相确认。</p>
    <fieldset className="xrd-parameter-group" disabled={running||starting}><legend>参数配置</legend>
    {matching&&<label className="field-label">谱库检索方式<select value={String(card.config.library_engine??'qualx')} title="QualX3 先筛选谱库候选，以下比对参数用于 OAW 排序；手动标准卡片直接比对。" onChange={e=>save({library_engine:e.target.value})}><option value="qualx">快速检索</option><option value="native">遍历检索</option></select></label>}
    {matching&&<label className="field-label">全库返回候选数<input type="number" min="1" max="100" defaultValue={Number(card.config.library_top_n??20)} onBlur={e=>{if(Number.isInteger(e.target.valueAsNumber))save({library_top_n:e.target.valueAsNumber});}}/></label>}
    {matching&&<label className="field-label">谱库允许元素（留空不限）<input key={String(card.config.library_elements??'')} defaultValue={String(card.config.library_elements??'')} placeholder="例如 Li Ti P O" onBlur={e=>save({library_elements:e.target.value.trim()})}/><small>仅保留元素均在此范围内的谱库条目；应依据样品组成设置，避免漏掉杂相。</small></label>}
    {!matching&&<><label><input type="checkbox" checked={Boolean(card.config.connected_inputs)} onChange={e=>save({connected_inputs:e.target.checked,...(e.target.checked?{demo:false}:{})})}/>从连接的 XRD 对象读取输入</label>
      {card.config.connected_inputs?<label className="field-label">本次候选 CIF<select value={String(card.config.cif_node_id||'')} onChange={e=>save({cif_node_id:e.target.value})}><option value="">自动选择唯一已连接 CIF</option>{cifs.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select><small>分别连接实验谱、标准卡片、CIF，并在 CIF 对象中设置对应标准卡片。</small></label>:<>
        {([['intensity_csv','实验谱：无表头两列 CSV','.csv'],['cif','候选结构 CIF','.cif']] as const).map(([key,label,accept])=><label className="field-label" key={key}>{label}<input type="file" accept={accept} onChange={async e=>{try{const f=e.target.files?.[0];if(f){if(f.size>8*1024*1024)throw new Error('文件最大 8 MiB');save({demo:false,[key]:await f.text()});}}catch(err){setError(String(err));}}}/><small>{card.config[key]?'已载入':'尚未载入'}</small></label>)}</>}
    </>}
    <div className="xrd-fields">{numbers.map(([key,label])=><label className="field-label" key={key}>{label}<input type="number" step="any" key={`${key}:${card.config[key]}`} defaultValue={Number(card.config[key])} onBlur={e=>{const v=e.target.valueAsNumber;if(Number.isFinite(v))save({[key]:v});else setError('请输入有效数值');}}/></label>)}</div>
    {matching?<details><summary>检峰设置</summary>{[['smoothing_deg','平滑宽度 / °'],['min_peak_distance','最小峰间距 / °']].map(([key,label])=><label className="field-label" key={key}>{label}<input type="number" step="0.01" defaultValue={Number(card.config[key])} onBlur={e=>{if(Number.isFinite(e.target.valueAsNumber))save({[key]:e.target.valueAsNumber});}}/></label>)}<small>以约 1° 局部低值背景扣除后检峰；波长需与实验条件一致。默认阈值只是筛选起点。</small></details>:<><label><input type="checkbox" checked={Boolean(card.config.preoptimize)} onChange={e=>save({preoptimize:e.target.checked})}/>先运行结构预优化</label><small>晶胞更新峰区间不是全谱裁剪范围。达到迭代上限不等于收敛。</small></>}
    {result&&<RunParameters result={result}/>}
    </fieldset></>}</>}
    {outputFirst&&!configOpen&&!result&&<p role="status">尚无检索结果，请通过设置确认参数后运行检索。</p>}
    {(!outputFirst||!configOpen)&&result&&<>{result.mode==='match'?<MatchResult result={result} host={host}/>:<pre>{JSON.stringify(result.solver,null,2)}{result.converged?'\n达到收敛判据':'\n尚未达到收敛判据'}</pre>}{archive?<a download="xrd-results.zip" href={archive.startsWith('/api/')?archive:`data:application/zip;base64,${archive}`}>下载结果及输入快照</a>:<small>请从运行日志中的本地目录读取结果。</small>}</>}
    {invalidMessage&&<p role="alert">{invalidMessage}</p>}
    {error&&<p role="alert">{error}</p>}
    {!running&&lastStatus==='failed'&&<p role="alert">上次检索失败。请检查参数及输入后重试；详情保留在 OAW 运行记录中。</p>}
    {!running&&lastStatus==='cancelled'&&<small role="status">上次检索已取消，参数已保留。</small>}
    </div>
    {outputFirst&&<section className="xrd-inputs"><h3>有效输入</h3><div className="xrd-input-list">{inputs?.map(i=><div key={i.id} title={i.detail}><span>{i.name}<small> · {i.detail}</small></span><i aria-label={i.ready?'已载入':'未就绪'}>{i.ready?'✓':'!'}</i></div>)}</div>{(missing||inputError)&&<small>{inputError||missing}</small>}</section>}
    {outputFirst&&<div className="xrd-execute"><span role="status">{running||starting?'正在执行检索，请稍候…':invalidMessage||error?'请检查参数':missing||inputError||'输入已连接 · 参数就绪'}</span><div className="action-row">
      <button className="primary-button" type="button" disabled={starting||running||(configOpen&&Boolean(missing||inputError||invalidMessage))} aria-label={configOpen?'开始检索':'返回参数设置'} title={configOpen?'按当前参数开始检索':'返回参数设置'} onClick={()=>configOpen?void start():setConfigOpen(true)}><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">{configOpen?<path d="m7 4 14 8-14 8Z" fill="currentColor"/>:<path d="M20 12H4m7-7-7 7 7 7"/>}</svg>{configOpen?(running||starting?'检索中…':'开始检索'):'返回参数设置'}</button>
      {running&&<button type="button" aria-label="停止检索" title="停止检索" onClick={()=>void host.stopAnalysis?.().catch(e=>setError(String(e)))}><svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>停止</button>}
    </div></div>}
  </section>;
}
export default {apiVersion:1,views:{settings:Settings,input:InputView,library:LibraryView,'frame-canvas':FrameCanvas}} satisfies FrontendPlugin;

import "./light.css";
