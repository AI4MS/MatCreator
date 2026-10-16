import {dragOffset} from './dragGeometry.js';

let users=0, disposeShared;
export function installDialogDragging() {
  if(users++===0)disposeShared=installSharedDragging();
  return()=>{if(--users===0){disposeShared();disposeShared=null;}};
}
function installSharedDragging() {
  const root=document.body;
  const selector='dialog[class*="xrd-"], .xrd-focus-panel';
  const offset=panel=>({x:Number(panel.dataset.dragX||0),y:Number(panel.dataset.dragY||0)});
  const apply=(panel,value)=>{
    panel.dataset.dragX=String(value.x);panel.dataset.dragY=String(value.y);
    panel.style.translate=`${value.x}px ${value.y}px`;
  };
  const prepare=()=>root.querySelectorAll(selector).forEach(panel=>{
    if(panel.querySelector(':scope > .mc-xrd-dialog-grip'))return;
    const grip=document.createElement('button');grip.type='button';grip.className='mc-xrd-dialog-grip';
    grip.textContent='⠿';grip.title='拖动弹窗';grip.setAttribute('aria-label','拖动弹窗');
    grip.addEventListener('keydown',event=>{
      const delta={ArrowLeft:[-20,0],ArrowRight:[20,0],ArrowUp:[0,-20],ArrowDown:[0,20]}[event.key];
      if(!delta)return;event.preventDefault();
      const rect=panel.getBoundingClientRect();
      apply(panel,dragOffset(rect,{width:innerWidth,height:innerHeight},offset(panel),{x:delta[0],y:delta[1]},rect.width/panel.offsetWidth));
    });
    panel.prepend(grip);
  });
  let cleanupDrag=()=>{};
  const down=event=>{
    const panel=event.target.closest(selector);
    if(!panel||!root.contains(panel)||event.button!==0||!event.target.closest('.mc-xrd-dialog-grip'))return;
    cleanupDrag();event.preventDefault();event.stopPropagation();
    const grip=event.target.closest('.mc-xrd-dialog-grip');grip.setPointerCapture(event.pointerId);
    const rect=panel.getBoundingClientRect(),origin=offset(panel),scale=rect.width/panel.offsetWidth;
    const start={x:event.clientX,y:event.clientY};
    const move=e=>{if(e.pointerId===event.pointerId)apply(panel,dragOffset(rect,{width:innerWidth,height:innerHeight},origin,{x:e.clientX-start.x,y:e.clientY-start.y},scale));};
    const end=()=>{
      window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',end);window.removeEventListener('pointercancel',end);
      if(grip.hasPointerCapture(event.pointerId))grip.releasePointerCapture(event.pointerId);
    };
    cleanupDrag=end;
    window.addEventListener('pointermove',move);window.addEventListener('pointerup',end);window.addEventListener('pointercancel',end);
  };
  prepare();const observer=new MutationObserver(prepare);observer.observe(root,{childList:true,subtree:true});
  document.addEventListener('pointerdown',down,true);
  return()=>{observer.disconnect();document.removeEventListener('pointerdown',down,true);cleanupDrag();root.querySelectorAll('.mc-xrd-dialog-grip').forEach(el=>el.remove());};
}
