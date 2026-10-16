import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { PluginViewProps } from '../sdk';
import './history.css';

type Run = {run_id:string; workflow_stage:string; status:string; created_at_ns:number; finished_at_ns?:number};
type Detail = {manifest:Run; parameters:Record<string,unknown>; files:{name:string;size_bytes:number;sha256?:string}[];record_counts?:Record<string,number>;storage?:string;schema_version?:number};
type RecordItem = {record_id:string;kind:string;actor_id:string;recorded_at_ns:number;payload:Record<string,unknown>};
const stages:Record<string,string>={search:'谱库检索',preopt:'结构预优化',fit:'单相全谱拟合',legacy_fit:'全谱拟合',multiphase:'多相筛选与联合复核'};
const statuses:Record<string,string>={completed:'已完成',running:'运行中',failed:'失败',cancelled:'已停止',interrupted:'已中断',inconclusive:'尚无定论',supported:'有证据支持'};
const kinds:Record<string,string>={evaluation:'评估结果',review:'联合复核',decision:'Jev 决策',agent_report:'分析报告',status:'运行状态',state:'执行进度',result:'结果摘要'};
const date=(ns:number)=>ns?new Date(ns/1e6).toLocaleString():'—';
const labels:Record<string,string>={status:'状态',score:'筛选得分',metrics:'拟合指标',converged:'达到收敛判据',candidate_ids:'候选编号',label:'物相',labels:'物相',summary:'分析结论',conclusion:'结论状态',limitations:'限制与不确定性',evidence:'证据文件',rwp:'Rwp',rp:'Rp',rwp_percent:'Rwp / %',rp_percent:'Rp / %',review_started_at_ns:'复核开始时间',full:'完整组合',removals:'移除检验',interpretation:'结果解释',error:'错误',reason:'选择理由',validation:'验证',provenance:'来源',fit:'拟合',construction_path:'构造路径',trial_id:'评估编号',iteration:'迭代',workflow_stage:'阶段',run_id:'运行编号',selection_token:'决策令牌',draft_candidate_ids:'当前草案',cloud_request_count:'请求次数',created_at_ns:'开始时间',finished_at_ns:'结束时间',agent_id:'执行者',source_owner_node_id:'工作台',auto_submit:'自动提交',evaluation_index:'评估序号'};
const fieldOrder:Record<string,number>={summary:0,conclusion:1,status:2,score:3,metrics:4,limitations:5,evidence:6};
function Value({value}:{value:unknown}) {
  if(value===null||value===undefined)return <span className="xrd-history-muted">—</span>;
  if(typeof value==='boolean')return <span>{value?'是':'否'}</span>;
  if(Array.isArray(value))return value.length?<ul className="xrd-history-values">{value.map((v,i)=><li key={i}><Value value={v}/></li>)}</ul>:<span>—</span>;
  if(typeof value==='object')return <dl className="xrd-history-fields">{Object.entries(value as Record<string,unknown>).sort(([a],[b])=>(fieldOrder[a]??100)-(fieldOrder[b]??100)).map(([k,v])=><div key={k}><dt>{labels[k]??k}</dt><dd><Value value={k.endsWith('_at_ns')&&typeof v==='number'?date(v):v}/></dd></div>)}</dl>;
  return <span>{typeof value==='number'?Number(value.toPrecision(7)):statuses[String(value)]??String(value)}</span>;
}
export function RunHistory({host}:{host:PluginViewProps['host']}) {
  const [open,setOpen]=useState(false);
  return <><button type="button" className="xrd-run-history" onClick={()=>setOpen(true)} aria-label="运行历史" title="运行历史"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 11a9 9 0 1 1 2.6 7M3 4v7h7"/><path d="M12 7v5l3 2"/></svg></button>
    {open&&createPortal(<HistoryDialog host={host} close={()=>setOpen(false)}/>,document.body)}</>;
}
function HistoryDialog({host,close}:{host:PluginViewProps['host'];close:()=>void}) {
  const dialog=useRef<HTMLDialogElement>(null),request=useRef(0);
  const [items,setItems]=useState<Run[]>([]),[offset,setOffset]=useState(0),[total,setTotal]=useState(0);
  const [detail,setDetail]=useState<Detail>(),[preview,setPreview]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [tab,setTab]=useState('overview'),[kind,setKind]=useState('evaluation'),[recordOffset,setRecordOffset]=useState(0);
  const [records,setRecords]=useState<RecordItem[]>([]),[recordTotal,setRecordTotal]=useState(0),[recordsBusy,setRecordsBusy]=useState(false);
  useEffect(()=>{dialog.current?.showModal();return()=>{request.current++;};},[]);
  useEffect(()=>{let alive=true;setBusy(true);setError('');
    host.resourceAction('history_list',{offset}).then(value=>{if(alive){setItems(value.items as Run[]);setTotal(Number(value.total));}})
      .catch(e=>{if(alive)setError(String(e));}).finally(()=>{if(alive)setBusy(false);});
    return()=>{alive=false;};
  },[host,offset]);
  useEffect(()=>{if(tab!=='records'||!detail)return;let alive=true;setRecordsBusy(true);setRecords([]);setError('');
    host.resourceAction('history_records',{run_id:detail.manifest.run_id,kind,offset:recordOffset}).then(v=>{if(alive){setRecords(v.items as RecordItem[]);setRecordTotal(Number(v.total));}}).catch(e=>{if(alive)setError(String(e));}).finally(()=>{if(alive)setRecordsBusy(false);});
    return()=>{alive=false;};
  },[host,detail,tab,kind,recordOffset]);
  const inspect=async(run:Run)=>{
    const id=++request.current;setBusy(true);setError('');setDetail(undefined);setPreview('');setRecordOffset(0);
    try{const value=await host.resourceAction('history_inspect',{run_id:run.run_id});if(id===request.current)setDetail(value as unknown as Detail);}
    catch(e){if(id===request.current)setError(String(e));}finally{if(id===request.current)setBusy(false);}
  };
  const file=async(name:string,download=false)=>{
    if(!detail)return;const id=++request.current;setBusy(true);setError('');setPreview('');
    try{
      const value=await host.resourceAction('history_file',{run_id:detail.manifest.run_id,name});if(id!==request.current)return;
      const bytes=Uint8Array.from(atob(String(value.data)),c=>c.charCodeAt(0));
      if(download){const url=URL.createObjectURL(new Blob([bytes]));const link=document.createElement('a');link.href=url;link.download=name.split('/').pop()!;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
      else{let text=new TextDecoder().decode(bytes);if(name.endsWith('.json')){try{text=JSON.stringify(JSON.parse(text),null,2);}catch{/* Preserve malformed source. */}}setPreview(text.length>150000?text.slice(0,150000)+'\n…请下载查看完整内容。':text);}
    }catch(e){if(id===request.current)setError(String(e));}finally{if(id===request.current)setBusy(false);}
  };
  return <dialog ref={dialog} className="xrd-history-dialog nodrag" aria-label="XRD 运行历史" onCancel={e=>{e.preventDefault();close();}}>
    <header><div><small>XRD / 工作台档案</small><h2>运行历史</h2></div><span className="xrd-history-badge">{total} 次运行</span><button onClick={close} aria-label="关闭运行历史">×</button></header>
    <div className="xrd-history-layout"><aside aria-label="历史运行列表">
      {!busy&&!items.length&&<p className="xrd-history-empty">尚无运行记录</p>}
      {items.map(run=><button key={run.run_id} className={`xrd-history-run ${detail?.manifest.run_id===run.run_id?'is-selected':''}`} onClick={()=>void inspect(run)} aria-pressed={detail?.manifest.run_id===run.run_id}>
        <strong>{stages[run.workflow_stage]??run.workflow_stage}</strong><span className="xrd-history-status" data-status={run.status}>{statuses[run.status]??run.status}</span>
        <small>{date(run.created_at_ns)}</small><code>{run.run_id.slice(0,8)}</code>
      </button>)}
      <nav><button disabled={busy||offset===0} onClick={()=>setOffset(Math.max(0,offset-50))}>上一页</button><span>{Math.floor(offset/50)+1} / {Math.max(1,Math.ceil(total/50))}</span><button disabled={busy||offset+50>=total} onClick={()=>setOffset(offset+50)}>下一页</button></nav>
    </aside><section aria-label="运行详情">
      {busy&&<p role="status">正在读取…</p>}{error&&<p className="xrd-history-error" role="alert">{error}</p>}
      {detail?<><div className="xrd-history-detail-heading"><h3>{stages[detail.manifest.workflow_stage]??detail.manifest.workflow_stage}</h3><span className="xrd-history-status" data-status={detail.manifest.status}>{statuses[detail.manifest.status]??detail.manifest.status}</span></div>
        <div className="xrd-history-meta"><code>{detail.manifest.run_id}</code><span>{detail.storage==='sqlite'?`SQL database · v${detail.schema_version}`:'本地归档'}</span></div>
        <nav className="xrd-history-tabs" aria-label="历史详情分类">{Object.entries({overview:'运行概览',records:'结果与记录',files:'归档文件'}).map(([key,label])=><button key={key} aria-pressed={tab===key} className={tab===key?'is-selected':''} onClick={()=>setTab(key)}>{label}</button>)}</nav>
        {tab==='overview'&&<><div className="xrd-history-stats"><div><small>开始时间</small><strong>{date(detail.manifest.created_at_ns)}</strong></div><div><small>结束时间</small><strong>{date(detail.manifest.finished_at_ns??0)}</strong></div><div><small>归档文件</small><strong>{detail.files.length}</strong></div></div>
          <div className="xrd-history-counts">{Object.entries(kinds).map(([key,label])=><button key={key} onClick={()=>{setKind(key);setRecordOffset(0);setTab('records');}}><span>{label}</span><strong>{detail.record_counts?.[key]??0}</strong></button>)}</div>
          <details className="xrd-history-panel"><summary>运行参数</summary><Value value={detail.parameters}/></details><details className="xrd-history-panel"><summary>来源与关联运行</summary><Value value={detail.manifest}/></details></>}
        {tab==='records'&&<><div className="xrd-history-record-filter"><label>记录类型 <select value={kind} onChange={e=>{setKind(e.target.value);setRecordOffset(0);}}>{Object.entries(kinds).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label><span>{recordTotal} 条</span></div>
          {recordsBusy?<p role="status">正在读取记录…</p>:!records.length?<p className="xrd-history-empty">此运行暂无{ kinds[kind] }记录</p>:records.map(item=><article className="xrd-history-panel" key={item.record_id}><header><strong>{kinds[item.kind]}{item.actor_id==='BO_baseline'?' · BO 对照':''}</strong><small>{date(item.recorded_at_ns)}</small></header><Value value={item.payload}/><details><summary>记录来源</summary><code>{item.actor_id}<br/>{item.record_id}</code></details></article>)}
          <nav><button disabled={recordsBusy||recordOffset===0} onClick={()=>setRecordOffset(Math.max(0,recordOffset-20))}>上一页</button><button disabled={recordsBusy||recordOffset+20>=recordTotal} onClick={()=>setRecordOffset(recordOffset+20)}>下一页</button></nav></>}
        {tab==='files'&&<><ul className="xrd-history-files">{detail.files.map(item=><li key={item.name}><button disabled={busy} onClick={()=>void file(item.name)} title={item.sha256}>{item.name}</button><small>{(item.size_bytes/1024).toFixed(1)} KiB</small><button disabled={busy} onClick={()=>void file(item.name,true)}>下载</button></li>)}</ul>{preview&&<pre aria-label="历史文件内容">{preview}</pre>}</>}
      </>:!busy&&<div className="xrd-history-empty"><h3>每一次分析，都有据可查</h3><p>从左侧选择一次运行，查看结果、执行记录与输入来源。</p></div>}
    </section></div>
  </dialog>;
}
