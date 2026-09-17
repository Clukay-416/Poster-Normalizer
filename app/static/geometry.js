'use strict';
function backgroundPlacement(size,output,t={}){
 const fit=['cover','free'].includes(t.mode)?Math.max:Math.min;
 const scale=fit(output[0]/size[0],output[1]/size[1])*(t.zoom??1);
 const w=Math.max(1,Math.floor(size[0]*scale+.5)),h=Math.max(1,Math.floor(size[1]*scale+.5));
 return {x:Math.floor((output[0]-w)/2+(t.x??0)+.5),y:Math.floor((output[1]-h)/2+(t.y??0)+.5),w,h};
}
if(typeof module!=='undefined')module.exports={backgroundPlacement};
