import {useEffect,useMemo,useRef,useState} from 'react';
import type {PluginViewProps} from '../sdk';
import {FocusLayer} from './MatchControls';
import {LibraryView} from './LibraryView';
type Input={id:string;name:string;kind:string;ready:boolean;detail:string};
function InputStatus({loading,ready}:{loading:boolean;ready:boolean}) {
  return <i className={`xrd-input-status ${loading?'is-loading':ready?'is-ready':'is-empty'}`} aria-label={loading?'正在加载':ready?'已载入':'未就绪'}>
    {loading?<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/></svg>:ready?<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4 10-10" pathLength="1"/></svg>:<span aria-hidden="true">·</span>}
  </i>;
}
export function InputDock({props,inputs,disabled}:{props:PluginViewProps;inputs?:Input[];disabled:boolean}) {
  const [popup,setPopup]=useState<{anchor:HTMLButtonElement;id:string}>();
  const [error,setError]=useState('');const [opening,setOpening]=useState(false);
  const fileInput=useRef<HTMLInputElement>(null);const target=useRef<string>();const importing=useRef(false);
  const [uploading,setUploading]=useState(false);
  const [libraryLoading,setLibraryLoading]=useState(false);
  const [loaded,setLoaded]=useState<Record<string,boolean>>({});
  const choosing=useRef(false);const releaseTimer=useRef<ReturnType<typeof setTimeout>>();
  const finishChoosing=()=>{clearTimeout(releaseTimer.current);releaseTimer.current=setTimeout(()=>{choosing.current=false;},0);};
  useEffect(()=>{
    const cancel=(event:Event)=>{event.stopPropagation();finishChoosing();};
    const escape=(event:KeyboardEvent)=>{if(choosing.current&&event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();}};
    const input=fileInput.current;input?.addEventListener('cancel',cancel);
    window.addEventListener('keydown',escape,true);
    return()=>{input?.removeEventListener('cancel',cancel);window.removeEventListener('keydown',escape,true);clearTimeout(releaseTimer.current);};
  },[]);
  const upload=async(file:File,id?:string)=>{
    if(disabled||importing.current)return;target.current=id;importing.current=true;setUploading(true);setError('');
    try{if(file.size>8*1024*1024)throw Error('文件最大 8 MiB');
      const input=id??await props.host.ensureXrdInput?.('pattern');if(!input)throw Error('无法创建实验谱输入');
      const current=await props.host.readDocument(input);const bytes=new Uint8Array(await file.arrayBuffer());let binary='';
      for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
      await props.host.documentAction('import',{filename:file.name,source_base64:btoa(binary)},current.revision,input);
      setLoaded(values=>({...values,[id??'pattern']:true,[input]:true}));
    }catch(e){setError(String(e));}finally{importing.current=false;setUploading(false);}
  };
  const importProps=(id?:string)=>({'data-import-hint':'请拖拽谱到谱画布，或点击打开选择文件',onClick:(e:React.MouseEvent)=>{e.stopPropagation();target.current=id;choosing.current=true;fileInput.current?.click();},onDragOver:(e:React.DragEvent)=>{e.preventDefault();e.stopPropagation();e.dataTransfer.dropEffect=disabled?'none':'copy';},onDrop:(e:React.DragEvent)=>{e.preventDefault();e.stopPropagation();if(e.dataTransfer.files.length!==1){setError('每次请拖入一个文件');return;}void upload(e.dataTransfer.files[0],id);}});
  const host=useMemo(()=>({...props.host,
    readDocument:async()=>{setLibraryLoading(true);try{return await props.host.readDocument(popup?.id);}finally{setLibraryLoading(false);}},
    documentAction:async(action:string,args:Record<string,unknown>,revision?:number)=>{setLibraryLoading(true);try{const result=await props.host.documentAction(action,args,revision,popup?.id);if(popup)setLoaded(values=>({...values,[popup.id]:true}));return result;}finally{setLibraryLoading(false);}},
  }),[props.host,popup?.id]);
  const open=async(anchor:HTMLButtonElement,item?:Input)=>{
    if(opening)return;setOpening(true);setError('');
    try{
      const id=item?.id??await props.host.ensureXrdInput?.('library');
      if(!id)throw Error('无法打开谱库');setPopup({anchor,id});
    }catch(e){setError(String(e));}finally{setOpening(false);}
  };
  return <><input ref={fileInput} hidden type="file" aria-label="导入原始谱数据" accept=".txt,.csv,.ras" disabled={disabled||uploading} onClick={e=>e.stopPropagation()} onKeyDown={e=>e.stopPropagation()} onChange={e=>{finishChoosing();const file=e.target.files?.[0];e.target.value='';if(file)void upload(file,target.current);}}/><div className="xrd-input-list">{inputs?.map(item=>item.kind==='pattern'||item.kind==='library'?<button key={item.id} type="button" disabled={disabled||opening||uploading} {...(item.kind==='pattern'?importProps(item.id):{onClick:(e:React.MouseEvent<HTMLButtonElement>)=>void open(e.currentTarget,item)})}><span>{item.name}<small> · {item.detail}</small></span><InputStatus loading={item.kind==='pattern'?uploading&&(target.current===item.id||!target.current):(opening||libraryLoading)&&(!popup||popup.id===item.id)} ready={item.ready||!!loaded[item.id]}/></button>:<div key={item.id}><span>{item.name}<small> · {item.detail}</small></span><InputStatus loading={false} ready={item.ready}/></div>)}
    {!inputs?.some(i=>i.kind==='pattern')&&<button type="button" disabled={disabled||uploading} {...importProps()}><span>实验谱 · {loaded.pattern?'已导入':'尚未导入'}</span><InputStatus loading={uploading} ready={!!loaded.pattern}/></button>}
    {!inputs?.some(i=>i.kind==='library')&&<button type="button" disabled={disabled||opening} onClick={e=>void open(e.currentTarget)}><span>挂载谱库 ＋</span><InputStatus loading={opening||libraryLoading} ready={false}/></button>}</div>
    {popup&&<FocusLayer anchor={popup.anchor} kind="library" onClose={()=>setPopup(undefined)}><LibraryView {...props} level="workspace" host={host}/></FocusLayer>}
    {error&&<p role="alert">{error}</p>}</>;
}
