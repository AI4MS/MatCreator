// Runtime-free compatibility surface for the preserved OAW XRD UI.
export type PluginViewProps = any;
export type FrontendPlugin = any;
export type XrdMultiphaseState = any;
export type XrdMultiphaseOptions = any;
export type XrdMultiphaseTrial = any;
export type XrdMultiphaseStructure = any;
const glyph = (path: string) => ({ size=18, ...props }: any) => <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}><path d={path}/></svg>;
export const ArrowLeft=glyph('M20 12H4m7-7-7 7 7 7');
export const ArrowRight=glyph('M4 12h16m-7-7 7 7-7 7');
export const Play=glyph('m7 4 14 8-14 8Z');
export const Pause=glyph('M8 4v16M16 4v16');
export const RotateCcw=glyph('M3 10a9 9 0 1 1 2.64 8.36M3 4v6h6');
export const Square=glyph('M5 5h14v14H5Z');
export const Link2=glyph('m10 14 4-4m-6 6-2 2a4 4 0 0 1-6-6l4-4m12 0 2-2a4 4 0 0 1 6 6l-4 4');
export const Settings2=glyph('M4 6h16M4 18h16M8 3v6m8 6v6');
