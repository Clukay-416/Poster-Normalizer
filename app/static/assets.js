'use strict';
const $=id=>document.getElementById(id);
function candidate(asset){
 const card=node('article','','card panel');const image=document.createElement('img');image.src=asset.preview_url;image.alt=asset.title||asset.label||'海报候选';image.loading='lazy';image.referrerPolicy='no-referrer';
 const meta=[asset.provider.toUpperCase(),asset.title||'作品名称未知',asset.orientation,asset.width&&asset.height?`${asset.width}×${asset.height}`:'尺寸待下载确认',asset.language||''].filter(Boolean).join(' · ');
 card.append(image,node('h3',asset.title||asset.label||'海报候选'),node('p',meta,'subtle'),button('下载并进入编辑',async()=>{const result=await request(`/assets/${asset.id}/import`,{profile:$('profile').value});message(`已导入${result.orientation}海报，使用 ${result.profile_key} 规格`);location.href='/?job='+encodeURIComponent(result.job_id)}));return card;
}
async function search(){
 const q=$('query').value.trim();if(!q)throw Error('请输入影视名称');$('results').replaceChildren(node('p','正在查询两个来源并整理候选…','subtle'));
 const data=await request('/assets/discover?'+new URLSearchParams({q}));$('sourceStatus').textContent=data.sources.map(s=>`${s.provider}：${s.note}`).join('　');$('results').replaceChildren();
 for(const asset of data.assets)$('results').append(candidate(asset));
 if(!data.assets.length)$('results').append(node('p','没有找到可用海报候选；可检查名称、网络或 TMDB 配置。'));
}
bind('search',search);$('query').addEventListener('keydown',e=>{if(e.key==='Enter')search().catch(e=>message(e.message))});
bind('save',async()=>{const body={network_enabled:$('network').checked};if($('tmdb').value)body.tmdb_token=$('tmdb').value;if($('fanart').value)body.fanart_key=$('fanart').value;await request('/assets/config',body,'PUT');$('tmdb').value='';$('fanart').value='';message('已保存');await setup()});
async function setup(){const [config,settings]=await Promise.all([request('/assets/config'),request('/settings')]);$('network').checked=config.network_enabled;$('configured').textContent=`TMDB：${config.tmdb_configured?'已配置':'未配置'}；Fanart：${config.fanart_configured?'已配置':'未配置'}`;for(const [key,value] of Object.entries(settings.profiles)){const option=new Option(value.name,key);$('profile').append(option)}}
request('/me').then(async user=>{if(user.role==='admin'){$('configuration').hidden=false;await setup()}else{const settings=await request('/settings');for(const [key,value] of Object.entries(settings.profiles))$('profile').append(new Option(value.name,key))}}).catch(e=>message(e.message));
