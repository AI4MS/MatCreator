import { useEffect, useState, useId } from 'react';
import type { PluginViewProps } from '../sdk';
type Library = {path:string;filename:string;count:number;metadata:Record<string,string>;slots?:(Library|null)[]};
export function LibraryView({host,level}:PluginViewProps){
  const [doc,setDoc]=useState<Library>();const [path,setPath]=useState('');const [revision,setRevision]=useState(0);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const [editing,setEditing]=useState(false);const [inserted,setInserted]=useState(0);const [slot,setSlot]=useState(0);const [animatedSlot,setAnimatedSlot]=useState(-1);
  const pathId=useId();
  useEffect(()=>{let active=true;void host.readDocument().then(d=>{if(active){const value=d.value as Library;setDoc(value);setPath(value.path||'');setRevision(d.revision);}}).catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[host]);
  const configure=async()=>{setBusy(true);setError('');try{const d=await host.documentAction('configure',{path:path.trim(),slot},revision);setDoc(d.value as Library);setRevision(d.revision);setInserted(n=>n+1);setAnimatedSlot(slot);setEditing(false);}catch(e){setError(String(e));try{const d=await host.readDocument();setRevision(d.revision);}catch{/* Preserve the original configuration error. */}}finally{setBusy(false);}};
  const slots=doc?.slots?.length?doc.slots:(doc?.count?[doc]:[]);const mounted=slots.some(Boolean);const preview=level==='preview';
  if(preview){
    const drives=slots.filter((drive):drive is Library=>Boolean(drive));
    return <section className="xrd-library-readout" aria-label="已挂载数据库概览">
      <span className="xrd-readout-count" aria-label="挂载谱总数">{drives.reduce((sum,drive)=>sum+drive.count,0).toLocaleString('en-US')}</span>
      <div className="xrd-readout-names">{drives.length?drives.map((drive,i)=><span key={i} title={drive.path}>{drive.filename}</span>):<span>未挂载数据库</span>}</div>
      {error&&<span role="alert">{error}</span>}
    </section>;
  }
  return <section className="xrd-panel xrd-library-panel nodrag nopan" aria-busy={busy}>
    <div className="xrd-nas">
      <div className="xrd-nas-header"><span>REFERENCE STORAGE</span><span className={mounted?'xrd-led is-online':'xrd-led'}>{busy?'校验中':mounted?'已挂载':'待挂载'}</span></div>
      <div className="xrd-drive-bays">
        {Array.from({length:6},(_,i)=>{const drive=slots[i];return <button key={i} type="button" className={`xrd-drive-bay ${drive?'is-loaded':'is-empty'}`} disabled={busy||preview} aria-label={drive?`配置槽位 ${i+1}：${drive.filename}`:`插入谱库到槽位 ${i+1}`} aria-expanded={editing&&slot===i} onClick={()=>{setSlot(i);setPath(drive?.path||'');setError('');setEditing(true);}}>
          {drive?<div key={animatedSlot===i?inserted:0} className={`xrd-drive ${animatedSlot===i&&inserted?'is-inserting':''}`}>
            <div className="xrd-drive-top"><span>{String(i+1).padStart(2,'0')} / COD</span><i aria-hidden="true"/></div>
            <div className="xrd-drive-display"><span className="xrd-digital-count" aria-label={`${drive.count.toLocaleString()} 条参考谱`}>{drive.count.toLocaleString('en-US')}</span><strong className="xrd-drive-filename">{drive.filename}</strong></div>
            <div className="xrd-drive-handle" aria-hidden="true"/>
          </div>:<div className="xrd-empty-drive"><span className="xrd-slot-plus">+</span><small>SLOT {String(i+1).padStart(2,'0')}</small></div>}
        </button>;})}
      </div>
      <div className="xrd-nas-footer"><span>● {mounted?'READ ONLY':'STANDBY'}</span></div>
    </div>
    {!preview&&<>
    {editing&&<form className="xrd-mount-form" onSubmit={e=>{e.preventDefault();void configure();}}><label htmlFor={pathId}>槽位 {slot+1} · 数据库路径（.sq）</label><input id={pathId} autoFocus value={path} placeholder="解压后的 .sq 主文件路径" onChange={e=>setPath(e.target.value)} disabled={busy}/><div className="xrd-mount-actions"><button type="button" disabled={busy} onClick={()=>setEditing(false)}>取消</button><button type="submit" disabled={!path.trim()||busy}>{busy?'正在校验…':slots[slot]?'确认挂载':'插入并挂载'}</button></div><small>支持 QualX / POW_COD SQLite 库；暂不支持 ICDD 专有格式。</small></form>}
    <span className="xrd-mount-status" role="status">{busy?'正在读取并校验数据库…':inserted?'挂载成功，可以连接匹配卡片检索。':''}</span></>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
