'use strict';
const el=id=>document.getElementById(id);
function message(text){el('toast').textContent=text;el('toast').hidden=false;clearTimeout(message.timer);message.timer=setTimeout(()=>el('toast').hidden=true,8000)}
async function request(path,body,method){const options=body===undefined?{}:{method:method||'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};const response=await fetch('/api'+path,{...options,signal:AbortSignal.timeout(/self-test|refine-edges|rebuild/.test(path)?1800000:120000)});if(response.status===401){location.href='/';throw Error('请先登录')}if(!response.ok){let err;try{err=await response.json()}catch{err={detail:response.statusText}}throw Error(typeof err.detail==='string'?err.detail:JSON.stringify(err.detail))}return response.json()}
function bind(id,fn){el(id).addEventListener('click',async()=>{try{await fn()}catch(e){message(e.message)}})}
function node(tag,text,cls){const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n}
function size(bytes){return bytes?(bytes/1024/1024/1024>=1?(bytes/1024/1024/1024).toFixed(2)+' GB':(bytes/1024/1024).toFixed(1)+' MB'):'待解析'}
function button(text,fn){const b=node('button',text);b.onclick=async()=>{b.disabled=true;try{await fn()}catch(e){message(e.message)}finally{b.disabled=false}};return b}
