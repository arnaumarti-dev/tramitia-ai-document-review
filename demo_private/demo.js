'use strict';

// This client contains only hardcoded synthetic examples; no external API calls.
const DEMO_DATA = Object.freeze([
  {id:'DEMO-001',filename:'solicitud-ayuda-ficticia-001.pdf',date:'07/11/2026',
   score:72,status:'REVISAR',type:'Solicitud de subvención',
   summary:'La solicitud inventada parece contener datos básicos, pero presenta campos que requieren revisión humana.',
   findings:['No se identifica claramente la fecha de firma.','No se ha encontrado una referencia a un justificante adjunto.']},
  {id:'DEMO-002',filename:'solicitud-ejemplo-completa.pdf',date:'06/11/2026',
   score:94,status:'OK',type:'Solicitud de subvención',
   summary:'En este ejemplo inventado, las reglas básicas no han detectado incidencias.',
   findings:[]},
  {id:'DEMO-003',filename:'solicitud-ejemplo-incompleta.pdf',date:'05/11/2026',
   score:43,status:'RIESGO',type:'Solicitud de subvención',
   summary:'Faltan referencias textuales importantes en el ejemplo, por lo que debería revisarlo una persona.',
   findings:['No aparece un número de teléfono de contacto.','No se ha encontrado una referencia a la firma.','El texto parece omitir la identificación del solicitante.']}
]);
let reports = DEMO_DATA.map(item => ({...item,findings:[...item.findings]}));
let selectedId = 'DEMO-001';
let activity = [
  'Se ha abierto una vista de ejemplo (solo navegador).',
  'Entorno de demostración cargado; no se han enviado archivos.'
];
const byId = (id) => document.getElementById(id);
function node(tag, text, className) {
  const el = document.createElement(tag);
  if(text !== undefined) el.textContent = String(text);
  if(className) el.className = className;
  return el;
}
function pill(status){
  return status === 'OK' ? 'pill green' : status === 'REVISAR' ? 'pill orange' : 'pill red';
}
function logAction(action) {
  activity.unshift(action);
  if(activity.length > 6) activity = activity.slice(0,6);
  const list = byId('eventList');
  list.replaceChildren(...activity.map(item => node('li',item)));
}
function renderMetrics() {
  byId('countTotal').textContent = reports.length;
  byId('countReview').textContent = reports.filter(x=>x.status!=='OK').length;
  byId('countOk').textContent = reports.filter(x=>x.status==='OK').length;
}
function renderTable() {
  const target=byId('reportRows');
  const search=byId('filterReports').value.trim().toLocaleLowerCase('es');
  const filtered=reports.filter(r=>r.filename.toLocaleLowerCase('es').includes(search));
  target.replaceChildren();
  for(const r of filtered){
    const tr=node('tr');
    const title=node('td');
    const label=node('div',undefined,'file-cell');
    label.append(node('span','▤','file-symbol'),node('strong',r.filename));
    title.append(label);
    const date=node('td',r.date);
    const score=node('td');
    score.append(node('strong',r.score+'/100'));
    const status=node('td');
    status.append(node('span',r.status,pill(r.status)));
    const action=node('td');
    const btn=node('button','Ver informe','btn small outline');
    btn.type='button';
    btn.addEventListener('click',()=>selectReport(r.id));
    action.append(btn);
    tr.append(title,date,score,status,action);
    target.append(tr);
  }
  if(filtered.length===0){
    const tr=node('tr'),td=node('td','No hay informes de ejemplo con ese filtro.');
    td.colSpan=5;tr.append(td);target.append(tr);
  }
  renderMetrics();
}
function selectReport(id){
  if(!reports.some(r=>r.id===id))return;
  selectedId=id;
  logAction('Informe ficticio '+id+' consultado (simulación).');
  renderDetail();
}
function renderDetail(){
  const target=byId('reportDetail');
  target.replaceChildren();
  const r=reports.find(x=>x.id===selectedId);
  if(!r){target.append(node('p','Selecciona otro informe ficticio.','muted'));return;}
  target.append(node('h3',r.filename,'result-title'));
  const score=node('div',undefined,'score-line');
  score.append(node('strong',r.score),node('span','/ 100 · Puntuación orientativa'));
  target.append(score);
  const bar=node('div',undefined,'progress'),fill=node('div',undefined,'progress-bar');
  fill.style.width=r.score+'%';bar.append(fill);target.append(bar);
  target.append(node('p',r.summary,'muted'));
  target.append(node('h3','Observaciones simuladas','result-title'));
  if(r.findings.length===0){target.append(node('p','No se han detectado alertas con las reglas simuladas. Revisión humana necesaria.','muted'));}
  for(const finding of r.findings){const div=node('div',undefined,'finding');div.append(node('strong','ATENCIÓN'),node('span',finding));target.append(div);}
  const actions=node('div',undefined,'detail-actions');
  const exportButton=node('button','↓ Exportar JSON ficticio','btn primary');
  exportButton.addEventListener('click',()=>{
    const safeReport={...r,nota:'Datos 100 % ficticios. Ningún PDF ha sido procesado.'};
    const blob=new Blob([JSON.stringify(safeReport,null,2)],{type:'application/json'});
    const url=URL.createObjectURL(blob);
    const link=document.createElement('a');
    link.href=url;link.download=r.id.toLowerCase()+'-ficticio.json';
    document.body.append(link);link.click();link.remove();URL.revokeObjectURL(url);
    logAction('Exportación JSON ficticia solicitada para '+r.id+'.');
  });
  const removeButton=node('button','Simular eliminación','btn outline');
  removeButton.addEventListener('click',()=>{
    if(!window.confirm('¿Quitar este ejemplo de la demo? Solo se modifica esta página; recargar restaura los datos.'))return;
    reports=reports.filter(item=>item.id!==r.id);
    selectedId=reports.length?reports[0].id:null;
    logAction('Informe '+r.id+' quitado del navegador (sin borrar datos de servidor).');
    renderTable();renderDetail();
  });
  actions.append(exportButton,removeButton);target.append(actions);
}
byId('filterReports').addEventListener('input',renderTable);
byId('resetDemo').addEventListener('click',()=>{
  reports=DEMO_DATA.map(item=>({...item,findings:[...item.findings]}));
  selectedId='DEMO-001';
  byId('filterReports').value='';
  logAction('Se han restaurado los datos ficticios.');
  renderTable();renderDetail();
});
renderTable();renderDetail();logAction('Demo preparada con tres solicitudes inventadas.');
