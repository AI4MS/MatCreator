// Work in viewport pixels, then convert only the displacement to local CSS units.
// Never replace a centered/scaled element's left/top with getBoundingClientRect().
export function dragOffset(rect, viewport, origin, delta, scale=1) {
  const left=Math.max(0,Math.min(Math.max(0,viewport.width-rect.width),rect.left+delta.x));
  const top=Math.max(0,Math.min(Math.max(0,viewport.height-rect.height),rect.top+delta.y));
  return {x:origin.x+(left-rect.left)/scale,y:origin.y+(top-rect.top)/scale};
}
