/* Layout detection affects presentation only; all data and rules remain shared. */
(() => {
  const root=document.documentElement,query=new URLSearchParams(location.search);
  const media=matchMedia('(max-width:820px)');
  const phone=/iPhone|iPod|Android.*Mobile|Windows Phone|Mobile/i.test(navigator.userAgent)||(navigator.userAgentData?.mobile===true);
  let mode=query.get('view');
  if(!['auto','mobile','desktop'].includes(mode)){
    try{mode=localStorage.getItem('lims.displayMode')}catch{}
  }
  if(!['auto','mobile','desktop'].includes(mode))mode='auto';
  function apply(){
    root.dataset.layout=mode==='auto'?(phone||media.matches?'mobile':'desktop'):mode;
    root.dataset.displayMode=mode;
    document.querySelectorAll('.slicer-drawer,.collection-drawer,.filter-drawer,.explanation-drawer').forEach(d=>d.open=root.dataset.layout==='desktop');
    const sheet=document.getElementById('pointSheet');if(sheet)sheet.hidden=true;
    const hint=document.querySelector('.slicers small');
    if(hint){hint.dataset.desktopText??=hint.textContent;hint.textContent=root.dataset.layout==='mobile'?'可点选多个项目；不选表示全部，选择后自动更新。':hint.dataset.desktopText}
    window.dispatchEvent(new Event('lims-layout-change'));
  }
  window.LimsDevice={isMobile:()=>root.dataset.layout==='mobile',showPoint:text=>{
    if(root.dataset.layout!=='mobile')return;
    document.getElementById('pointText').textContent=text;
    document.getElementById('pointSheet').hidden=false;
  }};
  apply();media.addEventListener('change',()=>{if(mode==='auto')apply()});
  document.addEventListener('DOMContentLoaded',()=>{
    const nav=document.createElement('div');nav.className='device-controls';
    const label=document.createElement('label');label.textContent='显示模式';
    const select=document.createElement('select');select.setAttribute('aria-label','显示模式');
    for(const[value,text]of[['auto','自动识别'],['mobile','手机版'],['desktop','桌面版']])select.append(new Option(text,value));
    select.value=mode;select.onchange=()=>{mode=select.value;try{localStorage.setItem('lims.displayMode',mode)}catch{}const url=new URL(location.href);if(mode==='auto')url.searchParams.delete('view');else url.searchParams.set('view',mode);history.replaceState(null,'',url);apply()};label.append(select);nav.append(label);
    document.querySelector('header').after(nav);
    for(const[section,title,cls]of[[document.querySelector('.slicers'),'筛选装置 / 样品 / 指标','slicer-drawer'],[document.getElementById('sync')?.closest('section'),'采集设置与状态','collection-drawer'],[document.getElementById('filter')?.closest('.row'),'筛选异常样品','filter-drawer']]){
      if(!section)continue;const details=document.createElement('details');details.className=cls;const summary=document.createElement('summary');summary.textContent=title;section.before(details);details.append(summary,section);
    }
    const overview=document.getElementById('filter')?.closest('section');
    if(overview){const details=document.createElement('details');details.className='explanation-drawer';const summary=document.createElement('summary');summary.textContent='判定与排序说明';details.append(summary);const paragraphs=[...overview.querySelectorAll(':scope > p'),...document.querySelectorAll('main > p')];overview.append(details);paragraphs.forEach(p=>details.append(p))}
    const bottom=document.createElement('nav');bottom.className='mobile-nav';bottom.setAttribute('aria-label','手机导航');
    for(const[href,text]of[['/lims','趋势查询'],['/lims/anomalies','异常样品']]){
      const a=document.createElement('a');a.href=href;a.textContent=text;if(location.pathname===href)a.setAttribute('aria-current','page');bottom.append(a);
    }
    document.body.append(bottom);
    const sheet=document.createElement('section');sheet.id='pointSheet';sheet.hidden=true;sheet.setAttribute('role','dialog');sheet.setAttribute('aria-label','测点详情');
    const close=document.createElement('button');close.textContent='关闭测点详情';close.onclick=()=>sheet.hidden=true;
    const text=document.createElement('div');text.id='pointText';sheet.append(close,text);document.body.append(sheet);
    document.addEventListener('keydown',e=>{if(e.key==='Escape')sheet.hidden=true});
    function labelRows(){document.querySelectorAll('table').forEach(table=>{
      const headers=[...table.querySelectorAll('thead th')].map(n=>n.textContent);
      table.querySelectorAll('tbody tr').forEach(tr=>[...tr.children].forEach((td,i)=>{if(!td.hasAttribute('data-label'))td.setAttribute('data-label',headers[i]||'')}));
    })}
    new MutationObserver(labelRows).observe(document.querySelector('main'),{childList:true,subtree:true});labelRows();apply();
  });
})();
