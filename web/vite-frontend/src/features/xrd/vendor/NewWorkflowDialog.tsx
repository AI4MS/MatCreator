import { useEffect, useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import './new-workflow.css';
export function NewWorkflowDialog({file,busy,error,onCancel,onConfirm}:{file:File;busy:boolean;error:string;onCancel:()=>void;onConfirm:()=>void}){
  const ref=useRef<HTMLDialogElement>(null);const title=useId(),description=useId();
  useEffect(()=>{const previous=document.activeElement as HTMLElement|null;ref.current?.showModal();return()=>{ref.current?.close();previous?.focus();};},[]);
  return createPortal(<dialog ref={ref} className="xrd-new-workflow-dialog nodrag nopan" aria-labelledby={title} aria-describedby={description} onCancel={event=>{event.preventDefault();if(!busy)onCancel();}}>
    <div className="xrd-new-workflow-icon" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M4 25h24M5 22l5-3 3-12 3 15 4-6 3 6h4M23 5v8M19 9h8"/></svg></div>
    <small>XRD · 新的分析</small><h2 id={title}>是否开启新的流程？</h2>
    <p id={description}>导入新的实验谱，回到参数设置。保留当前谱库与分析参数，先前的运行结果仍可在历史记录中查看。</p>
    <div className="xrd-new-workflow-file"><span>实验谱文件</span><strong>{file.name}</strong><small>{(file.size/1024).toFixed(1)} KB</small></div>
    {error&&<p role="alert">{error}</p>}
    <footer><button type="button" disabled={busy} onClick={onCancel} autoFocus>否</button><button type="button" className="xrd-confirm-primary" disabled={busy} onClick={onConfirm}>{busy?'正在开启…':'是，开启新流程'}</button></footer>
  </dialog>,document.body);
}
