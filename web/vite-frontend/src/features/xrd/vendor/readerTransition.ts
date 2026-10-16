// Coverage, filter radius and filtered-image mix are deliberately independent.
export const READER_TRANSITION={
  sources:[[.16,.22,1.08,.83],[.69,.16,.88,1.12],[.42,.62,1.18,.84],[.85,.73,1.06,.94],[.13,.88,.9,1.13],[.92,.35,1.02,.88],[.54,.4,.88,1.08]],
  spreadMs:440,fastMinimumMs:160,revealMs:460,feather:.72,blurPx:16,
  exitRevealMs:360,exitSpreadMs:320,
  opacityStart:.5,opacityEnd:.75,introMs:65,revealBlurPx:12,failureMs:45000,
} as const;
export const clamp=(n:number)=>Math.max(0,Math.min(1,n));
// Wider feather with zero slope at both ends avoids a visible elliptical rim.
// Precompute the ramp: no extra backdrop/filter pass or per-frame allocations here.
export function featherOpacity(distance:number){
  const t=clamp(distance);return 1-t*t*t*(t*(6*t-15)+10);
}
const featherStops=Array.from({length:13},(_,i)=>{
  const t=i/12,position=(1-READER_TRANSITION.feather+READER_TRANSITION.feather*t)*100;
  return `rgba(0,0,0,${featherOpacity(t).toFixed(4)}) ${position.toFixed(2)}%`;
}).join(",");
export function blurMix(elapsed:number){
  const c=READER_TRANSITION;
  if(elapsed<c.introMs)return c.opacityStart*clamp(elapsed/c.introMs);
  return c.opacityStart+(c.opacityEnd-c.opacityStart)*clamp((elapsed-c.introMs)/(c.spreadMs-c.introMs));
}
export function diffusionMask(progress:number,width:number,height:number,reveal=false,low=false){
  const p=clamp(progress),c=READER_TRANSITION;
  if(p>=1)return "linear-gradient(#000,#000)";
  const eased=p*p*(3-2*p);
  const radius=Math.max(width,height)*(.008+1.15*eased);
  return c.sources.slice(0,low?5:7).map(([x,y,rx,ry],i)=>{
    const r=radius*(1-i*.028);
    return `radial-gradient(ellipse ${r*rx}px ${r*ry}px at ${(reveal?(x*.65+.18):x)*100}% ${(reveal?(y*.72+.12):y)*100}%,${featherStops})`;
  }).join(",");
}
