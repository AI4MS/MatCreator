const ELEMENTS = {
  H:["#ffffff",0.37,0.8], He:["#d9ffff",0.32,2], Li:["#cc80ff",1.34,1.82], Be:["#c2ff00",0.9,2],
  B:["#ffb5b5",0.82,2], C:["#909090",0.77,1.7], N:["#3050f8",0.75,1.55], O:["#ff0d0d",0.73,1.52],
  F:["#90e050",0.71,1.47], Ne:["#b3e3f5",0.69,2], Na:["#ab5cf2",1.54,2.27], Mg:["#8aff00",1.3,1.73],
  Al:["#bfa6a6",1.18,1.84], Si:["#f0c8a0",1.11,2.1], P:["#ff8000",1.06,1.8], S:["#ffff30",1.02,1.8],
  Cl:["#1ff01f",0.99,1.75], K:["#8f40d4",1.96,2.75], Ca:["#3dff00",1.74,2.31], Sc:["#e6e6e6",1.44,2],
  Ti:["#bfc2c7",1.36,2.11], V:["#a6a6ab",1.25,2.07], Cr:["#8a99c7",1.27,2.06], Mn:["#9c7ac7",1.39,2.05],
  Fe:["#e06633",1.25,2.04], Co:["#f090a0",1.26,2], Ni:["#50d050",1.21,1.63], Cu:["#c88033",1.38,1.4],
  Zn:["#7d80b0",1.31,1.39], Ga:["#c28f8f",1.26,1.87], Ge:["#668f8f",1.22,2.11], Ag:["#c0c0c0",1.53,1.72],
  Pt:["#d0d0e0",1.28,1.75], Au:["#ffd123",1.44,1.66], Pb:["#575961",1.75,2.02], I:["#940094",1.33,2]
};

const fallback = ["#8aa0ff", 1, 2];

export const atomData = (symbol) => ELEMENTS[symbol] || fallback;

export const normalizeElement = (symbol) => {
  const clean = String(symbol || "X").replace(/[^A-Za-z]/g, "");
  const candidate = clean.slice(0, 1).toUpperCase() + clean.slice(1, 2).toLowerCase();
  return ELEMENTS[candidate] ? candidate : "X";
};
