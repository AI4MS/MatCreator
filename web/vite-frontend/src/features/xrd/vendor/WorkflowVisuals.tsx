import {useEffect,useRef,type CSSProperties} from 'react';
import {ArrowLeft,ArrowRight,Play,Pause,RotateCcw,Square,Link2} from '../sdk';
import type {ScientificFrame} from './FrameCanvas';
export const actionIcons={back:ArrowLeft,next:ArrowRight,play:Play,pause:Pause,restart:RotateCcw,stop:Square,follow:Link2};
export function ActionIcon({name}:{name:keyof typeof actionIcons}){const Icon=actionIcons[name];return <Icon size={18} strokeWidth={1.7} aria-hidden="true"/>;}
export function frameScore(frame:ScientificFrame){return typeof frame.quality==='number'&&Number.isFinite(frame.quality)&&frame.quality>=0&&frame.quality<=1?frame.quality:undefined;}
export function frameDescription(frame:ScientificFrame,index:number){const q=frameScore(frame);return `第 ${index+1} 帧 · ${q===undefined?'未评分':`质量分 ${(q*100).toFixed(3)} / 100`}${frame.metric&&frame.metric.value!=null?` · ${frame.metric.name} ${frame.metric.value} ${frame.metric.unit}`:''} · 标准卡片：${frame.label || frame.candidate_id}`;}
export function ScoreBars({frames,index,onSelect,phases,loading=false}:{loading?:boolean;frames:ScientificFrame[];phases?:Record<string,string>;index:number;onSelect:(index:number)=>void}){
 const root=useRef<HTMLDivElement>(null);
 useEffect(()=>{const reveal=()=>{const el=root.current?.querySelector<HTMLElement>('[aria-pressed="true"]');if(el&&root.current){const parent=root.current;const left=el.offsetLeft;if(left<parent.scrollLeft)parent.scrollLeft=left;else if(left+el.offsetWidth>parent.scrollLeft+parent.clientWidth)parent.scrollLeft=left+el.offsetWidth-parent.clientWidth;}};reveal();if(typeof ResizeObserver==='undefined'||!root.current)return;const observer=new ResizeObserver(reveal);observer.observe(root.current);return()=>observer.disconnect();},[index,frames.length]);
 return <div ref={root} className="xrd-score-scroll nowheel" role="group" aria-label="按帧质量分切换">{!frames.length?<span className="xrd-score-empty">尚无帧</span>:frames.map((f,i)=>{const q=frameScore(f);const description=frameDescription(f,i);return <button key={i} type="button" className={`xrd-score-hit ${q===undefined?'is-unrated':q===0?'is-zero':''} ${loading&&i===index?'is-loading':''}`} aria-busy={loading&&i===index} aria-label={description} aria-pressed={index===i} title={description} onClick={()=>onSelect(i)} onKeyDown={e=>{let next:number|undefined;if(e.key==='ArrowLeft')next=Math.max(0,i-1);if(e.key==='ArrowRight')next=Math.min(frames.length-1,i+1);if(e.key==='Home')next=0;if(e.key==='End')next=frames.length-1;if(next!==undefined){e.preventDefault();onSelect(next);(root.current?.children[next] as HTMLElement)?.focus();}}}>
   {phases&&<span className="xrd-score-phase">{phases[f.candidate_id]||'未提供物相'}</span>}<span className="xrd-score-well"><span className="xrd-score-column" style={{height:q===undefined?'28px':`${q*116}px`,'--xrd-score-hue':q===undefined?undefined:q*120} as CSSProperties}/></span><span className="xrd-score-number">{String(i+1).padStart(2,'0')}</span><span className="xrd-score-tooltip" role="tooltip">{description}</span>
 </button>;})}</div>;
}
export function wavePath(time:number,amplitude:number){return Array.from({length:81},(_,i)=>{const x=i/80;const envelope=Math.sin(Math.PI*x)**2;const wave=Math.sin(x*(8+.6*Math.sin(time*.45))*Math.PI-time*3)+.25*Math.sin(x*19*Math.PI+time*1.7);return `${i?'L':'M'}${(x*240).toFixed(2)},${(16+amplitude*envelope*wave).toFixed(3)}`;}).join(' ');}
export function ActivityLine({active,status,label}:{active:boolean;status:string;label:string}){
 const path=useRef<SVGPathElement>(null);const amp=useRef(0);
 useEffect(()=>{const reduced=window.matchMedia('(prefers-reduced-motion: reduce)');let raf=0;let start:number|undefined;let initial=amp.current;
   const tick=(now:number)=>{start??=now;const p=Math.min(1,(now-start)/320),ease=p*p*(3-2*p);amp.current=initial+((active?7:0)-initial)*ease;
     path.current?.setAttribute('d',wavePath(now/1000,amp.current));if(active||p<1)raf=requestAnimationFrame(tick);
   };
   const reset=()=>{cancelAnimationFrame(raf);start=undefined;initial=amp.current;if(reduced.matches){amp.current=0;path.current?.setAttribute('d',wavePath(0,0));}else raf=requestAnimationFrame(tick);};
   reset();reduced.addEventListener('change',reset);return()=>{cancelAnimationFrame(raf);reduced.removeEventListener('change',reset);};
 },[active]);
 const text=active?'运行中':status==='failed'?'失败':status==='cancelled'?'已取消':status==='completed'||status==='succeeded'?'已完成':'待运行';
 return <span className={`xrd-activity-line ${active?'is-active':''}`} data-state={active?'running':status} role="status" aria-label={`${label}：${text}（任务活动指示）`} title={`${label}：${text}`}><svg viewBox="0 0 240 32" preserveAspectRatio="none" aria-hidden="true"><path ref={path} d={wavePath(0,0)}/></svg>{(active||status==='failed'||status==='cancelled'||status==='completed'||status==='succeeded')&&<small>{text}</small>}</span>;
}
