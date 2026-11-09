'use strict';
const locale = document.documentElement.dataset.locale || 'en-US';
const timeZone = document.documentElement.dataset.timeZone || 'UTC';
const $ = id => document.getElementById(id);
const number = new Intl.NumberFormat(locale);
const fmtTime = ts => new Date(ts * 1000).toLocaleString(locale, {timeZone, month:'short', day:'numeric', hour:'numeric', minute:'2-digit'});
const duration = seconds => seconds < 60 ? `${Math.round(seconds)}s` : seconds < 3600 ? `${Math.floor(seconds/60)}m ${Math.round(seconds%60)}s` : `${Math.floor(seconds/3600)}h ${Math.floor(seconds%3600/60)}m`;
function text(id, value) { $(id).textContent = value; }
function element(tag, cls, content) { const el=document.createElement(tag); if(cls)el.className=cls; if(content!==undefined)el.textContent=content; return el; }
function toggle(button, target) { $(button).addEventListener('click', () => { const open=$(button).getAttribute('aria-expanded')!=='true'; $(button).setAttribute('aria-expanded',String(open)); $(target).hidden=!open; }); }
toggle('sessions-toggle','sessions');toggle('method-toggle','method');toggle('portal-toggle','portal-details');
toggle('survival-method-toggle','survival-method');toggle('combat-method-toggle','combat-method');
const leaderboardTabs=[...document.querySelectorAll('.leaderboard-tabs [role="tab"]')];
let runeAnimation=null,runeTarget=0;
function turnLeaderboardRune(index,animate=true){
 const rune=$('leaderboard-rune'),orientation=index*360/leaderboardTabs.length;
 const target=orientation+Math.round((runeTarget-orientation)/360)*360;
 if(target===runeTarget)return;
 // Read the in-flight position before cancelling, so a quick reversal never snaps.
 const matrix=new DOMMatrixReadOnly(getComputedStyle(rune).transform);
 let angle=Math.atan2(matrix.b,matrix.a)*180/Math.PI;
 angle+=Math.round((runeTarget-angle)/360)*360;
 if(runeAnimation)runeAnimation.cancel();
 runeAnimation=null;runeTarget=target;
 rune.style.transform=`rotate(${target}deg)`;
 if(!animate)return;
 const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
 // Keep this small orientation cue visible with a shorter, glow-free turn.
 const frames=reduced?[{transform:`rotate(${angle}deg)`},{transform:`rotate(${target}deg)`}]:[
  {transform:`rotate(${angle}deg)`,filter:'drop-shadow(0 0 0px transparent)'},
  {transform:`rotate(${(angle+target)/2}deg)`,filter:'drop-shadow(0 0 5px rgba(213,183,123,.45))',offset:.5},
  {transform:`rotate(${target}deg)`,filter:'drop-shadow(0 0 0px transparent)'}
 ];
 const animation=rune.animate(frames,{duration:reduced?300:900,easing:'cubic-bezier(.45,0,.2,1)'});
 runeAnimation=animation;
 animation.onfinish=()=>{if(runeAnimation===animation)runeAnimation=null;};
}
function selectLeaderboardTab(tab,{focus=false,updateHash=false,animateRune=true}={}){
 for(const button of leaderboardTabs){
  const selected=button===tab;
  button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;
  $(button.getAttribute('aria-controls')).hidden=!selected;
 }
 const survivalSelected=tab.id==='tab-survival',buildingSelected=tab.id==='tab-builders',combatSelected=tab.id==='tab-combat';
 turnLeaderboardRune(leaderboardTabs.indexOf(tab),animateRune);
 $('survival-coverage').hidden=!survivalSelected;$('total-pieces').hidden=!buildingSelected;
 $('telemetry-coverage').hidden=survivalSelected||buildingSelected||combatSelected;
 $('combat-coverage').hidden=!combatSelected;
 if(focus){tab.focus();tab.scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});}
 if(updateHash)history.replaceState(null,'','#'+tab.getAttribute('aria-controls'));
}
for(const tab of leaderboardTabs){
 tab.addEventListener('click',()=>selectLeaderboardTab(tab,{updateHash:true}));
 tab.addEventListener('keydown',event=>{
  let index=leaderboardTabs.indexOf(tab);
  if(event.key==='ArrowRight')index=(index+1)%leaderboardTabs.length;
  else if(event.key==='ArrowLeft')index=(index-1+leaderboardTabs.length)%leaderboardTabs.length;
  else if(event.key==='Home')index=0;
  else if(event.key==='End')index=leaderboardTabs.length-1;
  else return;
  event.preventDefault();selectLeaderboardTab(leaderboardTabs[index],{focus:true,updateHash:true});
 });
}
function selectLinkedLeaderboardTab(){
 const tab=leaderboardTabs.find(button=>'#'+button.getAttribute('aria-controls')===location.hash);
 if(tab)selectLeaderboardTab(tab,{animateRune:false});
}
window.addEventListener('hashchange',selectLinkedLeaderboardTab);selectLinkedLeaderboardTab();
function svg(tag, attributes, parent) { const el=document.createElementNS('http://www.w3.org/2000/svg',tag); Object.entries(attributes).forEach(([k,v])=>el.setAttribute(k,v)); if(parent)parent.append(el);return el; }
function drawMap(w) {
 const root=$('map-content');root.replaceChildren();
 const all=w.map.zones.map(([x,z])=>[x*64,z*64]).concat(w.map.buildings,w.portals.map(p=>[p.x,p.z]),[[0,0]]);
 const minX=Math.min(...all.map(p=>p[0]))-100,maxX=Math.max(...all.map(p=>p[0]))+100;
 const minZ=Math.min(...all.map(p=>p[1]))-100,maxZ=Math.max(...all.map(p=>p[1]))+100;
 const scale=Math.min(510/(maxX-minX),270/(maxZ-minZ)),cx=(minX+maxX)/2,cz=(minZ+maxZ)/2;
 const x=v=>300+(v-cx)*scale,z=v=>170-(v-cz)*scale;
 for(const [a,b] of w.map.zones)svg('rect',{x:x(a*64-32),y:z(b*64+32),width:64*scale,height:64*scale,class:'map-zone'},root);
 for(const pair of w.portal_pairs)svg('line',{x1:x(pair.a.x),y1:z(pair.a.z),x2:x(pair.b.x),y2:z(pair.b.z),class:'map-line'},root);
 for(const [a,b] of w.map.buildings)svg('circle',{cx:x(a),cy:z(b),r:1.1,class:'map-building'},root);
 for(const p of w.outposts){const mark=svg('circle',{cx:x(p.x),cy:z(p.z),r:5,class:'map-outpost'},root);svg('title',{},mark).textContent=`Build site: ${p.pieces} pieces · ${number.format(p.distance)} m from origin`;}
 for(const p of w.portals){const mark=svg('circle',{cx:x(p.x),cy:z(p.z),r:3,class:'map-portal'},root);svg('title',{},mark).textContent=p.tag||'Unnamed portal';}
 svg('path',{d:`M${x(0)-3} ${z(0)}h6M${x(0)} ${z(0)-3}v6`,stroke:'#e4dec8','stroke-width':1},root);
 svg('text',{x:x(0)+7,y:z(0)+12,class:'map-origin-label'},root).textContent='WORLD ORIGIN';
}

let playtimeByCreator=new Map(),expandedRecords=new Set(),onlinePlayers=[];
const kilometers=meters=>(meters/1000).toLocaleString(locale,{minimumFractionDigits:2,maximumFractionDigits:2})+' km';
const rankedMetrics={
 building:{pieces:{label:'Pieces standing',value:p=>p.pieces,format:v=>[number.format(v),'pieces']}},
 survival:{
  current:{label:'Current streak',value:p=>p.current_days,format:v=>[v.toFixed(1),'days']},
  best:{label:'Longest streak',value:p=>p.longest_days,format:v=>[v.toFixed(1),'days']},
  deaths:{label:'Deaths',value:p=>p.deaths,format:v=>[number.format(v)],lower:true}
 },
 combat:{
  kills:{label:'Kills',value:p=>p.kills,format:v=>[number.format(v)]},
  participation:{label:'Participation',value:p=>p.participation,format:v=>[number.format(v)]},
  skill:{label:'Highest observed skill',value:p=>p.highest_skill?.level,format:(v,p)=>['Lv. '+number.format(v),p.highest_skill.name],empty:p=>(p.skills||[]).some(s=>s.skill==='Bows'&&!Number.isFinite(s.level))?['—','Bows · Unknown']:['—','Not observed']}
 },
 exploration:{
  foot:{label:'On foot',value:p=>p.foot,format:v=>[kilometers(v)]},
  boat:{label:'By boat',value:p=>p.boat,format:v=>[kilometers(v)]},
  farthest:{label:'Farthest from origin',value:p=>(p.tracked_seconds||p.farthest)?p.farthest:null,format:v=>[kilometers(v)]}
 },
 agri:{
  planted:{label:'Planted',value:p=>p.planted,format:v=>[number.format(v)]},
  harvested:{label:'Harvested',value:p=>p.harvested,format:v=>[number.format(v)]}
 }
};
const rankedBodies={building:'builder-body',survival:'survival-body',combat:'combat-body',exploration:'explore-body',agri:'agri-body'};
const rankedSources={},rankedSorts={building:'pieces',survival:'current',combat:'kills',exploration:'foot',agri:'planted'};
for(const button of document.querySelectorAll('[data-sort-tab]'))button.addEventListener('click',()=>{
 const type=button.dataset.sortTab,key=button.dataset.sort;
 if(!rankedMetrics[type]?.[key])return;
 rankedSorts[type]=key;
 renderRanked(type,rankedSources[type]||[]);
});
function metricValue(definition,player){const value=definition.value(player);return Number.isFinite(value)?value:null;}
function renderRanked(type,source){
 rankedSources[type]=source;
 const definitions=rankedMetrics[type],selected=rankedSorts[type],order=[selected,...Object.keys(definitions).filter(k=>k!==selected)];
 const players=[...source].sort((a,b)=>{
  for(const key of order){
   const definition=definitions[key],av=metricValue(definition,a),bv=metricValue(definition,b);
   if(av===bv)continue;
   if(av===null)return 1;if(bv===null)return -1;
   return definition.lower?av-bv:bv-av;
  }
  return (a.name||a.creator_id).localeCompare(b.name||b.creator_id)||a.creator_id.localeCompare(b.creator_id);
 });
 const root=$(rankedBodies[type]),head=root.closest('table').tHead.rows[0];
 const headers=new Map([...head.querySelectorAll('[data-metric]')].map(th=>[th.dataset.metric,th]));
 for(const [i,key] of order.entries()){
  const th=headers.get(key);th.classList.toggle('primary-metric',key===selected);
  if(key===selected)th.setAttribute('aria-sort',definitions[key].lower?'ascending':'descending');else th.removeAttribute('aria-sort');
  // Keep header buttons mounted: their disclosure state, listeners and focus survive refresh.
  if(head.children[i+2]!==th)head.insertBefore(th,head.children[i+2]||null);
 }
 for(const button of document.querySelectorAll('[data-sort-tab="'+type+'"]'))button.setAttribute('aria-pressed',String(button.dataset.sort===selected));
 const note=$(type+'-sort-note');
 if(note)note.textContent=(definitions[selected].lower?'FEWEST FIRST · ':'HIGHEST FIRST · ')+definitions[selected].label.toUpperCase();
 const maximum=Math.max(0,...players.map(p=>metricValue(definitions[selected],p)||0));
 root.replaceChildren();
 players.forEach((p,index)=>{
  const values=order.map(key=>{
   const definition=definitions[key],value=metricValue(definition,p);
   const cell=valueCell(...(value===null?(definition.empty?.(p)||['—','Not observed']):definition.format(value,p)));cell.dataset.metric=key;
   if(key===selected){
    cell.classList.add('primary-metric');
    const graph=element('div','metric-graph'),track=element('span','metric-track'),fill=element('span','metric-fill'),amount=element('span','metric-amount');
    track.setAttribute('aria-hidden','true');fill.style.width=(maximum>0&&value!==null?Math.max(0,Math.min(100,value/maximum*100)):0)+'%';
    track.append(fill);amount.append(...cell.childNodes);graph.append(track,amount);cell.append(graph);
   }
   return cell;
  });
  addRankedRow(root,p,type,index,values,order.length+2);
 });
 emptyTable(root,order.length+2,type==='building'?'The first builder has yet to leave a mark.':'The next chapter begins with your next visit.');
}
function restorePlayerFocus(key){if(key)[...document.querySelectorAll('[data-record]')].find(b=>b.dataset.record===key)?.focus({preventScroll:true});}
function rankCell(index){const cell=element('td','rank-cell');cell.append(element('span','builder-rank'+(index===0?' rank-first':''),String(index+1).padStart(2,'0')));return cell;}
function playerCell(p,type){
 const cell=element('td','ranked-player'),name=element('button','player-drilldown',p.name||`Creator ${p.creator_id}`),key=type+':'+p.creator_id;
 name.type='button';name.dataset.record=key;name.setAttribute('aria-expanded',String(expandedRecords.has(key)));name.setAttribute('aria-controls','detail-'+type+'-'+p.creator_id);name.append(element('span','drilldown-arrow','↗'));
 name.addEventListener('click',()=>{const open=!expandedRecords.has(key);if(open)expandedRecords.add(key);else expandedRecords.delete(key);name.setAttribute('aria-expanded',String(open));$('detail-'+type+'-'+p.creator_id).hidden=!open;});
 cell.append(name);
 const online=onlinePlayers.some(o=>o.creator_id===p.creator_id||(!o.creator_id&&o.name===p.name));
 if(online)cell.append(element('span','player-presence','Online'));
 cell.append(element('small','player-creator','CREATOR '+p.creator_id));return cell;
}
function valueCell(value,unit=''){const cell=element('td','ranked-value',value);if(unit)cell.append(element('small','',unit));return cell;}
function detailsRow(p,type,columns){
 const row=element('tr','player-detail');row.id='detail-'+type+'-'+p.creator_id;row.hidden=!expandedRecords.has(type+':'+p.creator_id);
 const cell=element('td');cell.colSpan=columns;const box=element('div','drilldown-content'),t=playtimeByCreator.get(p.creator_id);
 box.append(element('span','eyebrow','TIME IN THE REALM'));
 if(t){
  const grid=element('div','player-time-grid');
  for(const [label,value] of [['Total playtime',duration(t.seconds)],['Recorded sessions',number.format(t.sessions)],['Longest session',duration(t.longest_seconds)]]){
   const item=element('div');item.append(element('span','',label),element('strong','',value));grid.append(item);
  }
  box.append(grid);
  if(t.first_seen)box.append(element('p','detail-note','First recorded visit · '+fmtTime(t.first_seen)));
 }else box.append(element('p','detail-note','No matched session history yet. Playtime will appear when this creator is associated with a player.'));
 if(type==='survival')box.append(element('p','detail-note',p.last_death?'Last fall · '+fmtTime(p.last_death):'No recorded deaths.'));
 if(type==='combat'){
  detailTable(box,'ENEMIES SLAIN BY TYPE',['Enemy','Kills','Participation'],
   (p.enemies||[]).map(e=>[e.enemy,number.format(e.kills),number.format(e.participation)]),'No confirmed combat outcomes recorded yet.');
  detailTable(box,'OBSERVED COMBAT SKILLS',['Skill','Highest observed level'],
   (p.skills||[]).map(s=>[s.name,Number.isFinite(s.level)?number.format(s.level):'Unknown']),'No combat skill levels observed yet.');
  box.append(element('p','detail-note','Skill records come from this Viking’s observed combat attacks. Unreported levels are shown as Unknown.'));
 }
 if(type==='exploration'){
  box.append(element('p','detail-note',`${number.format(p.visited_cells)} distinct 64 m cells visited.`));
  if(p.farthest_x!==undefined&&p.farthest_x!==null)box.append(element('p','detail-note',`Farthest position · X ${Math.round(p.farthest_x)}, Z ${Math.round(p.farthest_z)}`));
 }
 if(type==='agri'){
  box.append(element('span','eyebrow detail-subheading','PLANTS BY TYPE'));
  if(!p.plants.length)box.append(element('p','detail-note','No plant records observed yet.'));
  else{
   const table=element('table','drilldown-table'),caption=element('caption','sr-only',`${p.name}: plants by type`),head=element('thead'),hr=element('tr');
   for(const h of ['Plant / type','Planted','Harvested']){const th=element('th','',h);th.scope='col';hr.append(th);}head.append(hr);const body=element('tbody');
   for(const plant of p.plants){const tr=element('tr');for(const v of [`${plant.plant} · ${plant.category}`,number.format(plant.planted),number.format(plant.harvested)])tr.append(element('td','',v));body.append(tr);}table.append(caption,head,body);box.append(table);
  }
 }
 cell.append(box);row.append(cell);return row;
}
function addRankedRow(root,p,type,index,values,columns){const row=element('tr','ranked-row'+(index===0?' ranked-first':''));row.append(rankCell(index),playerCell(p,type),...values);root.append(row,detailsRow(p,type,columns));}
function detailTable(box,title,headers,rows,empty){
 box.append(element('span','eyebrow detail-subheading',title));
 if(!rows.length){box.append(element('p','detail-note',empty));return;}
 const table=element('table','drilldown-table'),head=element('thead'),hr=element('tr'),body=element('tbody');
 table.append(element('caption','sr-only',title));
 for(const h of headers){const th=element('th','',h);th.scope='col';hr.append(th);}head.append(hr);
 for(const values of rows){const row=element('tr');for(const value of values)row.append(element('td','',value));body.append(row);}
 table.append(head,body);box.append(table);
}
function emptyTable(root,columns,message){if(root.children.length)return;const row=element('tr'),cell=element('td','ranked-empty',message);cell.colSpan=columns;row.append(cell);root.append(row);}
function renderBuilding(w){
 text('building-total',number.format(w.building_pieces));text('building-creators',number.format(w.leaderboard.length));
 renderRanked('building',w.leaderboard);
}
function renderSurvival(data){
 if(!data)return;
 text('survival-total',number.format(data.total_deaths));
 const record=[...data.players].sort((a,b)=>b.longest_days-a.longest_days)[0];
 $('survival-record').replaceChildren(document.createTextNode(record?record.longest_days.toFixed(1)+' ':'— '),element('small','','days'));
 text('survival-record-holder',record?record.name:'No survival records yet');
 if(data.coverage_start)text('survival-coverage','Recorded since '+new Date(data.coverage_start*1000).toLocaleDateString(locale,{timeZone,month:'short',day:'numeric'}));
 renderRanked('survival',data.players);
}
function renderTelemetry(data){
 if(!data)return;
 renderCombat(data.combat,data.healthy);
 text('telemetry-coverage',data.started?(data.healthy?'Tracking since ':'Last signal · ')+new Date((data.healthy?data.started:data.last_seen)*1000).toLocaleDateString(locale,{timeZone,month:'short',day:'numeric'}):'Waiting for the new chapter');
 const players=data.players,counts=data.counts||{};
 text('explore-foot',kilometers(players.reduce((n,p)=>n+p.foot,0)));text('explore-boat',kilometers(players.reduce((n,p)=>n+p.boat,0)));
 renderRanked('exploration',players);
 text('explore-cells',`${number.format(data.visited_cells||0)} distinct 64 m cells visited · ${((data.visited_cells||0)*4096/1e6).toFixed(2)} km² of cell coverage. This differs from generated terrain and minimap visibility.`);
 text('agri-planted',number.format(counts['agriculture.planted']||0));text('agri-harvested',number.format(counts['agriculture.harvested']||0));
 renderRanked('agri',players);
}
function renderCombat(data,healthy){
 text('combat-kills',number.format(data?.kills||0));text('combat-participations',number.format(data?.participation||0));
 text('combat-coverage',data?.started?(data.active&&healthy?'Tracking since ':'Recorded since ')+new Date(data.started*1000).toLocaleDateString(locale,{timeZone,month:'short',day:'numeric'}):'Combat capture waiting');
 text('combat-unknown',data?.unattributed_deaths?`${number.format(data.unattributed_deaths)} confirmed creature deaths have an unknown killer. Select a Viking for enemies by type, observed skills, and playtime.`:'Select a Viking for playtime, enemies slain by type, and observed combat skills.');
 renderRanked('combat',data?.players||[]);
}
function render(data) {
 const a=data.activity,w=data.world;
 const focusedRecord=document.activeElement?.dataset.record;
 playtimeByCreator=new Map((data.playtime?.players||[]).map(p=>[p.creator_id,p]));
 onlinePlayers=a.online;
 renderTelemetry(data.telemetry);
 renderSurvival(data.survival);
 text('backend-status',data.crossplay===true?'PLAYFAB RELAY · CROSSPLAY ON':data.crossplay===false?'STEAM · CROSSPLAY OFF':'CONNECTION MODE UNKNOWN');
 text('online',a.online_count);text('online-names',a.online.length?a.online.map(p=>p.name).join(', '):'The fire is waiting');
 text('visitors',a.unique_24h);text('hours',a.player_hours_24h.toFixed(1));text('peak',`Peak ${a.peak} · recorded history`);
 const bars=$('activity-bars');bars.replaceChildren();const max=Math.max(1,...a.hourly.map(h=>h.player_minutes));
 for(const h of a.hourly){const bar=element('div','bar'+(h.player_minutes?' has-activity':''));bar.style.height=`${Math.max(4,h.player_minutes/max*100)}%`;bar.title=`${fmtTime(h.ts)}: ${h.player_minutes} player-minutes`;bars.append(bar);}
 const rows=$('sessions-body');rows.replaceChildren();
 for(const s of a.sessions){const row=element('tr');for(const value of [s.name,fmtTime(s.started),s.ended?fmtTime(s.ended):'Still in the realm',duration((s.ended||data.updated_at)-s.started)])row.append(element('td','',value));rows.append(row);}
 if(!a.sessions.length){const row=element('tr'),cell=element('td','','No player sessions recorded in the last 24 hours.');cell.colSpan=4;row.append(cell);rows.append(row);}
 text('server-status',data.identity_observer?'Realm online':'Observer awaiting heartbeat');$('status-dot').classList.toggle('offline',!data.identity_observer);
 text('identity-status',data.identity_observer?'Login identities tracked automatically':'Identity observer awaiting heartbeat');
 text('updated',`Updated ${new Date(data.updated_at*1000).toLocaleTimeString(locale,{timeZone,hour:'numeric',minute:'2-digit'})} ${timeZone} · refreshes every 15s`);
 $('notice').hidden=!data.error;text('notice',data.error?'World data is temporarily delayed. Showing the last successful snapshot.':'');
 if(!w)return;
 text('world-day',`Day ${number.format(w.day)}`);$('world-day').title=w.day_source;
 $('terrain').replaceChildren(document.createTextNode(w.generated_km2.toFixed(2)+' '),element('small','','km²'));
 text('zones',number.format(w.generated_zones));text('outposts',w.outposts.length);
 const defeated=w.bosses.filter(b=>b.defeated).length;text('boss-count',`${defeated} / ${w.bosses.length}`);
 const bosses=$('boss-list');bosses.replaceChildren();const symbols=['♜','♧','◈','⋈','♛','◇','♜','❄'];
 w.bosses.forEach((b,i)=>{const row=element('div','boss-row'+(b.defeated?' defeated':''));row.append(element('span','boss-symbol',b.defeated?'✓':symbols[i]));const name=element('span','boss-name',b.name);name.append(element('small','',b.biome));row.append(name,element('span','boss-state',b.defeated?'Defeated':'Unconquered'));bosses.append(row);});
 text('portal-pairs',w.portal_pairs.length);text('portal-description',w.portals.length?`${w.portals.length} portals · ${w.unpaired_portals} without a unique partner`:'No portals yet. The long way is still the only way.');
 const details=$('portal-details');details.replaceChildren(element('h3','','Paths between places'));
 if(!w.portals.length)details.append(element('p','','Portal names and paired connections will appear here after they are built and the world is saved.'));
 else{const list=element('ul');for(const p of w.portals)list.append(element('li','',`${p.tag||'Unnamed'} · (${Math.round(p.x)}, ${Math.round(p.z)})`));details.append(list,element('p','','Exactly two portals with the same tag count as a linked pair. More than two are marked ambiguous. Portal item restrictions are not modeled.'));}
 const outpost=w.farthest_outpost;text('outpost-distance',outpost?`${(outpost.distance/1000).toFixed(2)} km`:'—');text('outpost-description',outpost?`${outpost.pieces} pieces · X ${Math.round(outpost.x)}, Z ${Math.round(outpost.z)}`:'No clusters of 5 or more built pieces found.');
 text('total-pieces',`${number.format(w.building_pieces)} pieces standing`);
 renderBuilding(w);
 const mapKey=`${w.generation}:${w.parser_version}:${w.plant_filter}`;if($('world-map').dataset.generation!==mapKey){drawMap(w);$('world-map').dataset.generation=mapKey;}
 $('total-pieces').title=`Save ${w.generation} · ${fmtTime(w.saved_at)}`;
 restorePlayerFocus(focusedRecord);
}
async function refresh(){try{const response=await fetch('api/dashboard',{cache:'no-store'});if(!response.ok)throw Error('Metrics unavailable');render(await response.json());}catch(e){$('notice').hidden=false;text('notice','The chronicle is temporarily unreachable. Retrying automatically…');text('server-status','Connection interrupted');$('status-dot').classList.add('offline');}}
refresh();setInterval(refresh,15000);
