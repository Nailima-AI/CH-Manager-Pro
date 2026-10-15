'use strict';
const $ = id => document.getElementById(id);
let state=null, csrf='', activeTab='songs', selected=new Set(), reviewAction=null, settingsDirty=false, importing=false;
const tabs={songs:['Game library','The songs ready for your next session.'],archived:['Archived songs','Choose what’s in the game. Keep the rest in your archive.'],duplicates:['Duplicate review','Compare chart matches before deciding what to keep.'],files:['Archive files','Original downloads, kept for whenever you need them.'],trash:['Trash & restore','Recover songs and archives removed through this server.'],settings:['Server settings','Your folders, your workflow.'],activity:['Activity','Imports, scans, and changes to your collection.']};
function node(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;}
function message(text,error=false){const e=$(error?'error-box':'notice');e.textContent=text;e.hidden=!text;}
async function request(path,body,raw=false){const options=body===undefined?{}:{method:'POST',headers:{'X-CSRF-Token':csrf,...(raw?{}:{'Content-Type':'application/json'})},body:raw?body:JSON.stringify(body)};const response=await fetch(path,options);const result=await response.json();if(response.status===401&&path!=='/api/login'){showLogin();throw Error('Session expired. Sign in again.');}if(!response.ok)throw Error(result.error||'Request failed');return result;}
function showLogin(){$('workspace').hidden=true;$('login').hidden=false;csrf='';state=null;}
async function loadState(){try{const next=await request('/api/state');state=next;csrf=next.csrf;$('login').hidden=true;$('workspace').hidden=false;$('version').textContent=next.version;render();}catch(e){if(state)message(e.message,true);}}
function rows(){if(!state)return[];if(activeTab==='archived')return state.archived;if(activeTab==='files')return state.archives;if(activeTab==='trash')return state.trash;if(activeTab==='duplicates'){const ids=new Set(state.duplicates.flatMap(g=>g.ids));return state.songs.filter(s=>ids.has(s.id));}return state.songs;}
function visibleRows(){const q=$('search').value.toLocaleLowerCase().trim();const filter=$('installed-filter').value;const sort=$('sort').value;return rows().filter(r=>(!q||[r.name,r.artist,r.author,r.game,r.path,r.archive,r.original].filter(Boolean).join(' ').toLocaleLowerCase().includes(q))&&(activeTab!=='archived'||filter==='all'||(filter==='installed'?r.installed>0:!r.installed))).sort((a,b)=>String(a[sort]||a.path||a.original||'').localeCompare(String(b[sort]||b.path||b.original||'')));}
function busy(){return !!state?.jobs.some(j=>j.status==='queued'||j.status==='running')||importing;}
function render(){if(!state)return;$('metric-songs').textContent=state.songs.length.toLocaleString();$('metric-archived').textContent=state.archived.length.toLocaleString();$('metric-duplicates').textContent=state.duplicates.length;$('metric-monitor').textContent=state.config.monitoring?'Watching':'Paused';$('monitor-caption').textContent=state.config.auto_install?'Archive & install imports':'Archive imports only';$('nav-song-count').textContent=state.songs.length;$('nav-archive-count').textContent=state.archived.length;
 const jobs=state.jobs;const running=jobs.filter(j=>j.status==='queued'||j.status==='running');const last=jobs.at(-1);if(running.length)message(`${running.length} operation${running.length===1?'':'s'} in progress · ${running[0].action}`);else if(last)message(last.message,last.status==='error');
 const errors=state.errors||[];if(errors.length)message(`${errors.length} scan issue(s):\n${errors.join('\n')}`,true);else if(last?.status!=='error')message('',true);
 const simple=['settings','activity','duplicates'].includes(activeTab);$('duplicates-panel').hidden=activeTab!=='duplicates';if(activeTab==='duplicates')renderDuplicates();$('collection-panel').hidden=simple;$('settings-panel').hidden=activeTab!=='settings';$('activity-panel').hidden=activeTab!=='activity';$('installed-filter').hidden=activeTab!=='archived';$('organize-controls').hidden=!['songs','duplicates'].includes(activeTab);$('install-selected').hidden=activeTab!=='archived';$('restore-selected').hidden=activeTab!=='trash';$('remove-selected').hidden=activeTab==='trash';$('remove-selected').textContent=activeTab==='archived'?'Remove from game…':activeTab==='files'?'Archive files to Trash…':'Songs to Trash…';
 if(activeTab==='settings'&&!settingsDirty)fillSettings();renderJobs();if(!simple)renderRows();for(const id of ['refresh','preview-moves','install-selected','remove-selected','restore-selected','review-apply'])$(id).disabled=busy();$('open-import').disabled=busy();}
function renderRows(){const list=$('song-list');list.replaceChildren();const items=visibleRows();const valid=new Set(rows().map(s=>s.id));selected=new Set([...selected].filter(id=>valid.has(id)));$('visible-count').textContent=`${items.length.toLocaleString()} ${activeTab==='files'?'archives':activeTab==='trash'?'items':'songs'}`;$('select-all').checked=items.length>0&&items.every(s=>selected.has(s.id));
 if(!items.length){const e=node('div','empty');e.append(node('strong','',activeTab==='duplicates'?'No duplicate matches.':'Nothing here yet.'),node('span','',activeTab==='archived'?'Import archives or check your server archive folder in Settings.':'Try another search, import an archive, or refresh your collection.'));list.append(e);}
 for(const item of items){const row=node('div','song-row');const check=node('input');check.type='checkbox';check.checked=selected.has(item.id);check.setAttribute('aria-label',`Select ${item.name||item.path||item.original}`);check.addEventListener('change',()=>{if(check.checked)selected.add(item.id);else selected.delete(item.id);$('selected-count').textContent=`${selected.size} selected`;});row.append(check,node('div','art',activeTab==='files'?'▤':activeTab==='trash'?'↺':'♫'));const title=node('div');title.append(node('div','song-title',item.name||item.path||item.original),node('div','song-artist',item.artist|| (activeTab==='files'?`${(item.bytes/1048576).toFixed(1)} MiB`:new Date(item.removed*1000).toLocaleString())));row.append(title);const detail=node('div','song-detail');if(item.author){detail.append(node('strong','',item.author),node('div','',item.author_evidence||'Chart author'),node('div','',item.game||'Unknown source'),node('div','',item.game_evidence||''));}else detail.append(node('div','',activeTab==='trash'?'Original server path':'Original archive download'));row.append(detail);const end=node('div','row-end');if(activeTab==='archived'){end.append(node('span',`badge ${item.installed?'':'off'}`,item.installed?`In game · ${item.installed} copies`:'Archived only'),node('div','path',`${item.archive} / ${item.archive_path}`));if(item.installed_paths.length)end.append(node('div','path',item.installed_paths.join(' · ')));}else if(activeTab==='duplicates'){const groups=state.duplicates.map((g,i)=>g.ids.includes(item.id)?`Group ${i+1}: ${g.kind}`:null).filter(Boolean);for(const group of groups)end.append(node('span','badge duplicate',group));end.append(node('div','path',item.path));}else end.append(node('span','badge',activeTab==='files'?'Archive':activeTab==='trash'?'Recoverable':'In game'),node('div','path',item.path||item.original));if(item.warnings?.length)end.append(node('div','path',item.warnings.join('; ')));row.append(end);list.append(row);}$('selected-count').textContent=`${selected.size} selected`;}
function renderJobs(){const list=$('job-list');list.replaceChildren();for(const job of [...state.jobs].reverse()){const item=node('div','job');item.append(node('span',`badge ${job.status==='error'?'duplicate':''}`,job.status),node('strong','',` ${job.action}`),node('small','',job.message));list.append(item);}if(!state.jobs.length)list.append(node('p','muted','No operations yet.'));}
function fillSettings(){const form=$('settings-form');for(const [key,value]of Object.entries(state.config)){const input=form.elements.namedItem(key);if(!input)continue;if(input.type==='checkbox')input.checked=value;else input.value=value;}}
function switchTab(tab){activeTab=tab;selected.clear();$('search').value='';$('page-title').textContent=tabs[tab][0];$('page-description').textContent=tabs[tab][1];document.querySelectorAll('[data-tab]').forEach(e=>e.classList.toggle('active',e.dataset.tab===tab));render();}
async function action(name,payload={}){const result=await request('/api/action',{action:name,...payload});message('Operation queued. Follow progress in Activity.');await loadState();return result;}
function review(title,description,details,callback){$('review-title').textContent=title;$('review-description').textContent=description;$('review-details').textContent=details;reviewAction=callback;$('review-dialog').showModal();}
function selectedItems(){return rows().filter(r=>selected.has(r.id));}
function guarded(fn){return async e=>{try{await fn(e);}catch(error){message(error.message,true);}};}
$('login-form').addEventListener('submit',async e=>{e.preventDefault();try{await request('/api/login',{token:$('token').value});$('token').value='';$('login-error').textContent='';await loadState();}catch(error){$('login-error').textContent=error.message;}});
$('logout').addEventListener('click',guarded(async()=>{await request('/api/logout',{});showLogin();}));
document.querySelectorAll('[data-tab]').forEach(e=>e.addEventListener('click',()=>switchTab(e.dataset.tab)));
for(const id of ['search','sort','installed-filter'])$(id).addEventListener(id==='search'?'input':'change',()=>renderRows());
$('select-all').addEventListener('change',()=>{for(const row of visibleRows()){if($('select-all').checked)selected.add(row.id);else selected.delete(row.id);}renderRows();});
$('refresh').addEventListener('click',guarded(()=>action('refresh',{archives:['archived','files','duplicates'].includes(activeTab)})));
$('move-mode').addEventListener('change',()=>{$('custom-folder').hidden=$('move-mode').value!=='custom';});
$('preview-moves').addEventListener('click',guarded(async()=>{if(!selected.size)throw Error('Select songs first');const plan=await request('/api/preview',{ids:[...selected],mode:$('move-mode').value,custom:$('custom-folder').value});if(!plan.moves.length)return message('Songs are already in those folders.');review(`Move ${plan.moves.length} songs?`,'Review the complete plan. Collisions get a numbered suffix. Original archives remain unchanged.',plan.moves.map(m=>`${m.source}\n  → ${m.target}`).join('\n\n'),()=>action('apply_moves',{plan:plan.plan,confirm:true}));}));
$('install-selected').addEventListener('click',guarded(async()=>{if(!selected.size)throw Error('Select archived songs first');await action('install',{ids:[...selected]});selected.clear();}));
$('remove-selected').addEventListener('click',guarded(async()=>{const items=selectedItems();if(!items.length)throw Error('Select items first');const paths=activeTab==='archived'?[...new Set(items.flatMap(i=>i.installed_paths))]:items.map(i=>i.path);if(!paths.length)throw Error('No matching installed copies selected');const name=activeTab==='archived'?'remove_archived':activeTab==='files'?'remove_archives':'remove_songs';const ids=items.map(i=>i.id);review(`Move ${paths.length} ${activeTab==='files'?'archives':'songs'} to Trash?`,activeTab==='files'?'This removes original archive files from the archive folder. Installed game songs stay unchanged. You can restore these files from Trash.':'This removes selected game copies. Original downloaded archives stay available. You can restore removed folders from Trash.',paths.join('\n'),()=>action(name,{ids,confirm:true,expected_paths:paths}));}));
$('restore-selected').addEventListener('click',guarded(async()=>{if(!selected.size)throw Error('Select trash items first');await action('restore',{ids:[...selected]});selected.clear();}));
$('review-apply').addEventListener('click',guarded(async()=>{if(busy())return;if(reviewAction){$('review-dialog').close();await reviewAction();selected.clear();}}));
document.querySelectorAll('[data-close]').forEach(e=>e.addEventListener('click',()=>$(e.dataset.close).close()));
$('settings-form').addEventListener('input',()=>{settingsDirty=true;});
$('settings-form').addEventListener('submit',guarded(async e=>{e.preventDefault();const form=e.target;const values={};for(const key of ['songs_dir','backup_dir','downloads_dir','organization'])values[key]=form.elements.namedItem(key).value;for(const key of ['monitoring','auto_install'])values[key]=form.elements.namedItem(key).checked;settingsDirty=false;await action('settings',values);}));
$('open-import').addEventListener('click',()=>{$('install-on-import').checked=state.config.auto_install;$('upload-progress').textContent='';$('import-dialog').showModal();});
$('import-form').addEventListener('submit',guarded(async e=>{e.preventDefault();const files=[...$('uploads').files];if(!files.length)throw Error('Choose archives first');importing=true;render();const button=e.target.querySelector('button[type=submit]');button.disabled=true;try{for(const [i,file]of files.entries()){$('upload-progress').textContent=`Uploading ${i+1}/${files.length}: ${file.name}`;const uploaded=await request('/api/upload?name='+encodeURIComponent(file.name),file,true);await action('import',{upload:uploaded.upload,install:$('install-on-import').checked});}$('import-dialog').close();$('uploads').value='';}catch(error){$('upload-progress').textContent=error.message;throw error;}finally{importing=false;button.disabled=false;render();}}));

function renderDuplicates(){
 const container=$('duplicate-groups');container.replaceChildren();
 const query=$('duplicate-search').value.toLocaleLowerCase().trim();
 const scope=$('duplicate-scope').value,kind=$('duplicate-kind').value;
 const lookup=new Map((state.duplicate_items||[]).map(item=>[item.id,item]));
 let count=0;
 for(const group of state.duplicates){
  const items=group.ids.map(id=>lookup.get(id)).filter(Boolean);
  const installed=items.filter(i=>i.scope==='installed').length,archived=items.length-installed;
  if(kind!=='all'&&group.category!==kind)continue;
  if(scope==='installed'&&installed<2||scope==='archived'&&archived<2||scope==='cross'&&(!installed||!archived))continue;
  if(query&&!items.some(i=>[i.name,i.artist,i.author,i.game,i.path,i.archive,i.archive_path].filter(Boolean).join(' ').toLocaleLowerCase().includes(query)))continue;
  count++;
  const card=node('article','duplicate-group');
  card.append(node('span','badge duplicate',group.kind),node('h3','',items[0]?.name||'Song comparison'),node('p','caption',`${installed} game copies · ${archived} archived songs`));
  for(const item of items){
   const entry=node('div','duplicate-entry'),info=node('div','duplicate-info');
   info.append(node('strong','',item.name),node('div','song-artist',`${item.artist} · Charter: ${item.author}`),node('div','song-detail',`${item.game} · ${item.game_evidence}`));
   const location=item.scope==='installed'?item.path:`${item.archive} / ${item.archive_path}`;
   info.append(node('span',`badge ${item.scope==='archived'?'off':''}`,`${item.scope==='installed'?'In game':'Archived'} · ${item.format}`),node('div','path',location));
   info.append(node('div','fingerprint',`Chart SHA-256: ${item.chart_hash||'Unavailable'}`),node('div','fingerprint',`Content SHA-256: ${item.content_hash||'Unavailable'}`));
   const controls=node('div','duplicate-actions');
   if(item.scope==='installed'){
    const remove=node('button','danger','Move game copy to Trash…');remove.disabled=busy();
    remove.addEventListener('click',guarded(()=>review('Remove this game copy?','Only this game song is moved to recoverable app Trash. Other versions and all original archives stay available.',location,()=>action('remove_songs',{ids:[item.id],confirm:true}))));controls.append(remove);
   }else{
    const install=node('button','primary',item.installed?'Already installed':'Install this version');install.disabled=busy()||!!item.installed;
    install.addEventListener('click',guarded(()=>action('install',{ids:[item.id]})));controls.append(install);
    const archive=node('button','danger','Review source archive…');archive.disabled=busy();
    archive.addEventListener('click',guarded(()=>{
     const related=state.archived.filter(e=>e.archive===item.archive);
     review('Move the entire source archive to Trash?',`This affects all ${related.length} songs inside this source file, not just the selected duplicate. Installed game copies stay unchanged. Restore the original file from Trash if needed.`,item.archive+'\n\n'+related.map(e=>e.name+' — '+e.artist).join('\n'),()=>action('remove_archives',{ids:[item.archive_id],confirm:true}));
    }));controls.append(archive);
   }
   entry.append(info,controls);card.append(entry);
  }
  container.append(card);
 }
 $('duplicate-count').textContent=`${count} matching groups · A song can appear in more than one comparison.`;
 if(!count){const empty=node('div','empty');empty.append(node('strong','','No duplicate groups match.'),node('span','','Try another filter or refresh to rescan game songs and archives.'));container.append(empty);}
}
for(const id of ['duplicate-search','duplicate-scope','duplicate-kind'])$(id).addEventListener(id==='duplicate-search'?'input':'change',()=>{if(state)renderDuplicates();});

loadState();setInterval(()=>{if(state&&!document.hidden)loadState();},2500);
