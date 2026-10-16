import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { ELEMENTS } from './elements';
import './match-controls.css';

export const SEARCH_MODES = [{value:'qualx',label:'快速检索'}, {value:'native',label:'遍历检索'}];
export function Glyph({name}:{name:'close'|'down'|'elements'|'play'|'back'|'stop'}) {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{name==='close'?<path d="m6 6 12 12M6 18 18 6"/>:name==='down'?<path d="m6 9 6 6 6-6"/>:name==='play'?<path d="m7 4 14 8-14 8Z" fill="currentColor"/>:name==='back'?<path d="M20 12H4m7-7-7 7 7 7"/>:name==='stop'?<rect x="6" y="6" width="12" height="12"/>:<>{[5,12,19].flatMap(x=>[5,12,19].map(y=><circle key={`${x}-${y}`} cx={x} cy={y} r="1.2" fill="currentColor"/>))}</>}</svg>;
}

// Portal stays inside the transformed OAW window. All measurements are converted
// back to this window's local coordinates, including its current canvas zoom.
export function FocusLayer({anchor,kind,onClose,children,title="全谱拟合设置"}:{anchor:HTMLButtonElement;kind:'elements'|'engine'|'library'|'settings';onClose:()=>void;children:ReactNode|((dismiss:()=>void)=>ReactNode);title?:string}) {
  const root=anchor.closest<HTMLElement>('.legion-workspace-pane, .node-workspace-window, article.world-card')??anchor.closest<HTMLElement>('.xrd-match-ui')!;
  const layer=useRef<HTMLDivElement>(null), panel=useRef<HTMLDivElement>(null);
  const close=useRef(onClose);close.current=onClose;
  const closing=useRef(false);
  const position=useRef<{left:number;top:number}>();
  const exits=useRef<Animation[]>([]);
  const dismiss=()=>{
    if(closing.current)return;closing.current=true;
    const node=panel.current;
    if(!['library','elements'].includes(kind)||!node?.animate||window.matchMedia('(prefers-reduced-motion: reduce)').matches){onClose();return;}
    if(kind==='elements'){
      const origin=anchor.getBoundingClientRect(),bounds=root.getBoundingClientRect();
      const sx=bounds.width/(root.offsetWidth||1)||1,sy=bounds.height/(root.offsetHeight||1)||1;
      exits.current=Array.from(node.querySelectorAll<HTMLElement>('.xrd-element')).map(el=>{
        const dest=el.getBoundingClientRect();
        const dx=(origin.x+origin.width/2-dest.x-dest.width/2)/sx,dy=(origin.y+origin.height/2-dest.y-dest.height/2)/sy;
        return el.animate([{transform:'translate(0,0) scale(1)',opacity:1},{transform:`translate(${dx}px,${dy}px) scale(.3)`,opacity:0}],{duration:270,delay:120-Math.min(120,Math.hypot(dx,dy)*.13),easing:'cubic-bezier(.75,0,.8,.2)',fill:'forwards'});
      });
      Promise.allSettled(exits.current.map(a=>a.finished)).then(()=>close.current());return;
    }
    const a=anchor.getBoundingClientRect(),b=node.getBoundingClientRect();
    const sx=b.width/node.offsetWidth||1,sy=b.height/node.offsetHeight||1;
    node.animate([{transform:'translate(0,0) scale(1)',opacity:1},{transform:`translate(${(a.x+a.width/2-b.x-b.width/2)/sx}px,${(a.y+a.height/2-b.y-b.height/2)/sy}px) scale(.08)`,opacity:0}],{duration:220,easing:'cubic-bezier(.4,0,.8,.2)',fill:'forwards'}).finished.then(onClose,onClose);
  };
  const dismissRef=useRef(dismiss);dismissRef.current=dismiss;
  useLayoutEffect(()=>{
    const node=panel.current!;
    const measure=()=>{
      const r=root.getBoundingClientRect(), a=anchor.getBoundingClientRect();
      const sx=r.width/(root.offsetWidth||r.width||1), sy=r.height/(root.offsetHeight||r.height||1);
      const w=Math.min(kind==='elements'?1000:(kind==='library'||kind==='settings')?600:Math.max(300,a.width/sx),root.clientWidth-24);
      node.style.width=`${Math.max(180,w)}px`;node.style.maxHeight=`${Math.max(120,root.clientHeight-24)}px`;
      const h=node.offsetHeight;
      const left=Math.max(12,Math.min((a.left-r.left)/sx,root.clientWidth-w-12));
      const below=(a.bottom-r.top)/sy+8, above=(a.top-r.top)/sy-h-8;
      const top=Math.max(12,Math.min(below+h<=root.clientHeight-12?below:above,root.clientHeight-h-12));
      node.style.left=`${Math.max(12,Math.min(position.current?.left??left,root.clientWidth-w-12))}px`;node.style.top=`${Math.max(12,Math.min(position.current?.top??top,root.clientHeight-h-12))}px`;
    };
    measure();
    const header=kind==='elements'?node.querySelector('header'):null;
    let drag:{id:number;x:number;y:number;left:number;top:number;sx:number;sy:number}|undefined;
    const down=(e:PointerEvent)=>{
      if(e.button!==0||closing.current||(e.target as Element).closest('input,button'))return;
      const r=root.getBoundingClientRect();drag={id:e.pointerId,x:e.clientX,y:e.clientY,left:parseFloat(node.style.left),top:parseFloat(node.style.top),sx:r.width/(root.offsetWidth||1)||1,sy:r.height/(root.offsetHeight||1)||1};
      header!.setPointerCapture(e.pointerId);e.preventDefault();e.stopPropagation();
    };
    const move=(e:PointerEvent)=>{if(!drag||e.pointerId!==drag.id)return;position.current={left:Math.max(12,Math.min(drag.left+(e.clientX-drag.x)/drag.sx,root.clientWidth-node.offsetWidth-12)),top:Math.max(12,Math.min(drag.top+(e.clientY-drag.y)/drag.sy,root.clientHeight-node.offsetHeight-12))};measure();};
    const up=()=>{drag=undefined;};
    header?.addEventListener('pointerdown',down);header?.addEventListener('pointermove',move);header?.addEventListener('pointerup',up);header?.addEventListener('pointercancel',up);header?.addEventListener('lostpointercapture',up);
    const obs=new ResizeObserver(measure);obs.observe(root);obs.observe(node);
    root.addEventListener('scroll',measure,true);window.addEventListener('resize',measure);
    const focusable=()=>Array.from(node.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),[tabindex="0"]'));
    (node.querySelector<HTMLElement>('[data-autofocus]')??focusable()[0])?.focus();
    const siblings=Array.from(root.children).filter(el=>el!==layer.current) as HTMLElement[];
    const oldInert=siblings.map(el=>el.inert);siblings.forEach(el=>{el.inert=true;});
    const key=(e:KeyboardEvent)=>{
      if(e.key==='Escape'){e.preventDefault();e.stopPropagation();dismissRef.current();}
      if(e.key==='Tab'){const list=focusable();const first=list[0],last=list.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}
    };
    node.addEventListener('keydown',key);
    const animations:Animation[]=[];
    if(kind==='library'&&node.animate&&!window.matchMedia('(prefers-reduced-motion: reduce)').matches){
      const a=anchor.getBoundingClientRect(),b=node.getBoundingClientRect();const sx=b.width/node.offsetWidth||1,sy=b.height/node.offsetHeight||1;
      animations.push(node.animate([{transform:`translate(${(a.x+a.width/2-b.x-b.width/2)/sx}px,${(a.y+a.height/2-b.y-b.height/2)/sy}px) scale(.08)`,opacity:0},{transform:'translate(0,0) scale(1)',opacity:1}],{duration:300,easing:'cubic-bezier(.2,.8,.25,1)'}));
    }
    if(kind==='elements'&&!window.matchMedia('(prefers-reduced-motion: reduce)').matches){
      const origin=anchor.getBoundingClientRect(), bounds=root.getBoundingClientRect();
      const sx=bounds.width/root.offsetWidth,sy=bounds.height/root.offsetHeight;
      node.querySelectorAll<HTMLElement>('.xrd-element').forEach(el=>{
        const dest=el.getBoundingClientRect();
        const dx=(origin.x+origin.width/2-dest.x-dest.width/2)/(sx||1),dy=(origin.y+origin.height/2-dest.y-dest.height/2)/(sy||1);
        if(el.animate)animations.push(el.animate([{transform:`translate(${dx}px,${dy}px) scale(.3)`,opacity:0},{transform:'translate(0,0) scale(1)',opacity:1}],{duration:270,delay:Math.min(120,Math.hypot(dx,dy)*.13),easing:'cubic-bezier(.2,.8,.25,1)',fill:'backwards'}));
      });
    }
    return()=>{header?.removeEventListener('pointerdown',down);header?.removeEventListener('pointermove',move);header?.removeEventListener('pointerup',up);header?.removeEventListener('pointercancel',up);header?.removeEventListener('lostpointercapture',up);exits.current.forEach(a=>a.cancel());animations.forEach(a=>a.cancel());obs.disconnect();root.removeEventListener('scroll',measure,true);window.removeEventListener('resize',measure);node.removeEventListener('keydown',key);siblings.forEach((el,i)=>{el.inert=oldInert[i];});if(anchor.isConnected)anchor.focus();};
  },[anchor,root,kind]);
  return createPortal(<div ref={layer} className="xrd-focus-layer xrd-match-ui nodrag nopan nowheel" onPointerDown={e=>e.stopPropagation()}>
    <div className="xrd-focus-shade" onClick={dismiss}/>
    <div ref={panel} className={`xrd-focus-panel xrd-focus-${kind}`} role="dialog" aria-modal="true" aria-label={kind==='elements'?'选择元素':kind==='library'?'XRD数据库':kind==='settings'?title:'选择检索方式'}>{(kind==='library'||kind==='settings')&&<header><strong>{kind==='library'?'XRD数据库':title}</strong><button type="button" aria-label={kind==='library'?'关闭XRD数据库':'关闭'+title} onClick={dismiss}><Glyph name="close"/></button></header>}{typeof children==='function'?children(dismiss):children}</div>
  </div>,root);
}

function Numeric({field,label,unit,config,schema,save,onInvalid}:{field:string;label:string;unit?:string;config:Record<string,unknown>;schema:Record<string,unknown>;save:(patch:Record<string,unknown>)=>void;onInvalid:(key:string,message:string)=>void}) {
  const prop=((schema.properties??{}) as Record<string,Record<string,unknown>>)[field]??{};
  const actual=config[field]??prop.default??'';
  const [draft,setDraft]=useState(String(actual));
  useEffect(()=>setDraft(String(actual)),[actual]);
  const commit=(text=draft)=>{
    const value=Number(text);
    const invalid=!text.trim()||!Number.isFinite(value)||(prop.type==='integer'&&!Number.isInteger(value))||(typeof prop.minimum==='number'&&value<prop.minimum)||(typeof prop.maximum==='number'&&value>prop.maximum)||(typeof prop.exclusiveMinimum==='number'&&value<=prop.exclusiveMinimum);
    if(invalid){onInvalid(field,`${label}：请输入合法范围内的数值`);return;}
    onInvalid(field,'');if(value!==Number(actual))save({[field]:value});
  };
  const adjust=(direction:number)=>{
    const step=prop.type==='integer'?1:({wavelength:.000001,tolerance_deg:.01,prominence_fraction:.01,reference_min_intensity:1} as Record<string,number>)[field]??.01;
    const base=Number(draft);if(!Number.isFinite(base))return;
    let next=Number((base+direction*step).toFixed(8));
    if(typeof prop.minimum==='number')next=Math.max(prop.minimum,next);
    if(typeof prop.maximum==='number')next=Math.min(prop.maximum,next);
    if(typeof prop.exclusiveMinimum==='number'&&next<=prop.exclusiveMinimum)return;
    setDraft(String(next));commit(String(next));
  };
  return <label className="xrd-number-field"><span title={field==='library_top_n'?'检索后保留的候选条目数量':undefined}>{label}</span><span className="xrd-number-control"><input aria-label={label} type="number" step={prop.type==='integer'?1:'any'} min={typeof prop.minimum==='number'?prop.minimum:undefined} max={typeof prop.maximum==='number'?prop.maximum:undefined} value={draft} onChange={e=>setDraft(e.target.value)} onBlur={()=>commit()} onKeyDown={e=>{if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();adjust(e.key==='ArrowUp'?1:-1);}}}/>{unit&&<span>{unit}</span>}<span className="xrd-number-steppers">{[1,-1].map(direction=><button key={direction} type="button" aria-label={`${direction===1?'增加':'减少'}${label}`} onPointerDown={e=>e.preventDefault()} onClick={()=>adjust(direction)}><svg viewBox="0 0 12 8" aria-hidden="true"><path d={direction===1?'M2 6 6 2 10 6':'M2 2 6 6 10 2'}/></svg></button>)}</span></span></label>;
}

export function MatchControls({config,schema,save,disabled,advanced,onInvalid}:{config:Record<string,unknown>;schema:Record<string,unknown>;save:(patch:Record<string,unknown>)=>void;disabled:boolean;advanced:ReactNode;onInvalid:(key:string,message:string)=>void}) {
  const [popup,setPopup]=useState<{kind:'engine'|'elements';anchor:HTMLButtonElement}>();
  const [query,setQuery]=useState('');
  const value=String(config.library_elements??'');
  const [selected,setSelected]=useState<string[]>(value.split(/[\s,;]+/).filter(Boolean));
  const expectedSelection=useRef<string|null>(null);
  useEffect(()=>{if(expectedSelection.current!==null&&value!==expectedSelection.current)return;expectedSelection.current=null;setSelected(value.split(/[\s,;]+/).filter(Boolean));},[value]);
  useEffect(()=>{if(disabled)setPopup(undefined);},[disabled]);
  const update=(next:string[])=>{expectedSelection.current=next.join(' ');setSelected(next);save({library_elements:expectedSelection.current});};
  const toggle=(symbol:string)=>update(selected.includes(symbol)?selected.filter(e=>e!==symbol):[...selected,symbol]);
  const open=(kind:'engine'|'elements',anchor:HTMLButtonElement)=>{setQuery('');setPopup({kind,anchor});};
  const mode=SEARCH_MODES.find(m=>m.value===(config.library_engine??'qualx'));
  const numeric=(field:string,label:string,unit?:string)=><Numeric {...{field,label,unit,config,schema,save,onInvalid}}/>;
  const matched=(e:typeof ELEMENTS[number])=>!query||`${e.number} ${e.symbol} ${e.name}`.toLowerCase().includes(query.toLowerCase());
  return <fieldset className="xrd-parameter-grid" disabled={disabled} aria-label="参数配置">
    <section className="xrd-zone xrd-zone-strategy"><h3><span>01</span>检索策略</h3><div className="xrd-zone-body">
      <label className="xrd-engine-field"><span>检索方式</span><button className="xrd-engine-trigger" type="button" role="combobox" aria-label="谱库检索方式" aria-expanded={popup?.kind==='engine'} aria-haspopup="dialog" onClick={e=>open('engine',e.currentTarget)}>{mode?.label??String(config.library_engine)}<Glyph name="down"/></button></label>
      {numeric('library_top_n','候选数')}

    </div></section>
    <section className="xrd-zone xrd-zone-elements"><h3><span>02</span>元素约束</h3><div className="xrd-zone-body"><small>仅允许所选元素；留空则不限制。</small>
      <div className="xrd-selected-elements">{selected.map(symbol=><button key={symbol} className="xrd-element-chip" type="button" aria-label={`移除元素 ${symbol}`} onClick={()=>toggle(symbol)}><small>{ELEMENTS.find(e=>e.symbol===symbol)?.number??'?'}</small><strong>{symbol}</strong><Glyph name="close"/></button>)}<button className="xrd-element-trigger" type="button" onClick={e=>open('elements',e.currentTarget)} aria-expanded={popup?.kind==='elements'}><Glyph name="elements"/>选择元素</button></div>
      <div className="xrd-element-summary"><small>已选择 {selected.length} 个元素</small>{selected.length>0&&<button type="button" onClick={()=>update([])}>清空</button>}</div>
    </div></section>
    <section className="xrd-zone xrd-zone-peaks"><h3><span>03</span>峰匹配</h3><div className="xrd-zone-body xrd-peaks-grid">
      {numeric('wavelength','匹配波长 / Å')}{numeric('tolerance_deg','峰位容差 ± / °')}{numeric('prominence_fraction','峰突出度 / 最强峰')}{numeric('reference_min_intensity','标准峰最低相对强度')}
    </div></section>
    <section className="xrd-zone xrd-zone-advanced"><h3><span>04</span>高级设置</h3><div className="xrd-zone-body">
      <details><summary>检峰设置 <Glyph name="down"/></summary><div className="xrd-advanced-fields">{numeric('smoothing_deg','平滑宽度 / °','°')}{numeric('min_peak_distance','最小峰间距 / °','°')}<small>以约 1° 局部低值背景扣除后检峰。</small></div></details>{advanced}
    </div></section>
    {popup&&<FocusLayer anchor={popup.anchor} kind={popup.kind} onClose={()=>setPopup(undefined)}>{dismiss=><>
      {popup.kind==='engine'?<><header><strong>检索方式</strong><button type="button" aria-label="关闭检索方式" onClick={()=>setPopup(undefined)}><Glyph name="close"/></button></header><div className="xrd-mode-list" role="listbox" aria-label="检索方式" onKeyDown={e=>{if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();const items=Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>('button'));const index=items.indexOf(document.activeElement as HTMLButtonElement);items[e.key==='Home'?0:e.key==='End'?items.length-1:(index+(e.key==='ArrowDown'?1:items.length-1))%items.length]?.focus();}}}>
        {SEARCH_MODES.map(m=><button data-autofocus={mode===m?true:undefined} type="button" role="option" aria-selected={mode===m} key={m.value} onClick={()=>{save({library_engine:m.value});setPopup(undefined);}}><span><strong>{m.label}</strong></span><span>{mode===m?'✓':''}</span></button>)}
      </div></>:<><header><div><strong>选择元素</strong></div><input data-autofocus aria-label="搜索元素" placeholder="符号、英文名称、原子序数" value={query} onChange={e=>setQuery(e.target.value)}/><button type="button" aria-label="关闭元素周期表" onClick={dismiss}><Glyph name="close"/></button></header>
        <div className="xrd-periodic-scroll"><div className="xrd-periodic-grid">
          {Array.from({length:18},(_,i)=><span className="xrd-group-label" key={`g${i}`} style={{gridColumn:i+1,gridRow:1}}>{i+1}</span>)}
          <span className="xrd-series-placeholder" style={{gridColumn:3,gridRow:7}}>57–71</span><span className="xrd-series-placeholder" style={{gridColumn:3,gridRow:8}}>89–103</span>
          <span className="xrd-series-name" style={{gridColumn:'1 / 4',gridRow:10}}>镧系</span><span className="xrd-series-name" style={{gridColumn:'1 / 4',gridRow:11}}>锕系</span>
          {ELEMENTS.map(e=><button className={`xrd-element ${matched(e)?'':'is-dimmed'}`} type="button" key={e.number} style={{gridColumn:e.col,gridRow:e.row}} aria-label={`${e.number} ${e.symbol} ${e.name}`} aria-pressed={selected.includes(e.symbol)} title={`${e.number} · ${e.name}`} onClick={()=>toggle(e.symbol)}><small>{e.number}</small><strong>{e.symbol}</strong>{selected.includes(e.symbol)&&<i>✓</i>}</button>)}
        </div></div><footer><button className="primary-button" type="button" onClick={dismiss}>完成</button></footer>
      </>}
    </>}</FocusLayer>}
  </fieldset>;
}
