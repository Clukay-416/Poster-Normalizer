'use strict';
const $=id=>document.getElementById(id);
async function search(id='',type='movie'){
 const query=new URLSearchParams({provider:$('provider').value,q:$('query').value,media_id:id,media_type:type});
 const result=await request('/assets/search?'+query);$('results').replaceChildren();
 for(const row of result.media||[]){const card=node('article','','card panel');card.append(node('h3',row.title),node('p',`${row.year||'年份未知'} · ${row.type} · ID ${row.id}`),button('查看素材',()=>search(row.id,row.type)));$('results').append(card)}
 for(const row of result.assets||[]){const card=node('article','','card panel');card.append(node('h3',row.label),node('p',`${row.provider} · 来源及授权待人工核对`),button('下载到本机并预览',async()=>{await request(`/assets/${row.id}/download`,{});const im=document.createElement('img');im.src=`/api/assets/${row.id}/image`;im.alt=row.label;im.style.maxWidth='100%';const a=node('a','保存 PNG');a.href=im.src;a.download=row.id+'.png';card.append(im,a);}),button('查看来源记录',async()=>{const pre=node('pre',JSON.stringify(await request(`/assets/${row.id}/provenance`),null,2));card.append(pre)}));$('results').append(card)}
 if(!(result.media||result.assets||[]).length)$('results').append(node('p','没有找到候选素材。'));
}
bind('search',()=>search());bind('byId',()=>search($('mediaId').value.trim(),$('mediaType').value));
bind('save',async()=>{const body={network_enabled:$('network').checked};if($('tmdb').value)body.tmdb_token=$('tmdb').value;if($('fanart').value)body.fanart_key=$('fanart').value;await request('/assets/config',body,'PUT');$('tmdb').value='';$('fanart').value='';message('已保存');await config()});
async function config(){const c=await request('/assets/config');$('network').checked=c.network_enabled;$('configured').textContent=`TMDB：${c.tmdb_configured?'已配置':'未配置'}；Fanart：${c.fanart_configured?'已配置':'未配置'}`}
request('/me').then(async u=>{if(u.role==='admin'){$('configuration').hidden=false;await config()}}).catch(e=>message(e.message));
