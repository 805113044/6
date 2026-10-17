(()=>{
  const dialog=document.getElementById('v3-quick-dialog');
  if(!dialog)return;
  const form=dialog.querySelector('#v3-quick-form');
  const selection=dialog.querySelector('#v3-book-room');
  const dateField=dialog.querySelector('#v3-book-date');
  const periodField=dialog.querySelector('#v3-book-period');
  const heading=dialog.querySelector('#v3-quick-context');
  const feedback=dialog.querySelector('#v3-quick-feedback');
  const submit=dialog.querySelector('#v3-quick-submit');
  const defaultLabel=submit.textContent;
  let busy=false;
  const report=(message)=>{
    feedback.hidden=false;
    feedback.textContent=message;
    feedback.scrollIntoView({block:'nearest',behavior:'smooth'});
  };
  const openCell=(cell)=>{
    if(busy)return;
    feedback.hidden=true;
    feedback.textContent='';
    selection.replaceChildren();
    const options=cell.querySelectorAll('[data-v3-room-id]');
    options.forEach(item=>{
      const opt=document.createElement('option');
      opt.value=item.dataset.v3RoomId;
      opt.textContent=item.textContent.trim();
      selection.append(opt);
    });
    dateField.value=cell.dataset.v3Date;
    periodField.value=cell.dataset.v3Period;
    heading.textContent=`${cell.dataset.v3Date} · ${cell.dataset.v3PeriodName} · 可借教室 ${options.length} 间`;
    if(!options.length){
      const opt=document.createElement('option');
      opt.value='';opt.textContent='该节暂无可借用教室';selection.append(opt);
    }
    if(typeof dialog.showModal==='function')dialog.showModal();
    else dialog.setAttribute('open','');
    if(options.length)selection.focus();
  };
  document.querySelectorAll('[data-v3-cell]').forEach(cell=>{
    cell.tabIndex=0;
    cell.setAttribute('role','button');
    cell.setAttribute('aria-label',`${cell.dataset.v3Date} ${cell.dataset.v3PeriodName} 选择可借教室`);
    cell.addEventListener('click',e=>{
      if(e.target.closest('a,button,input,select,textarea,form'))return;
      openCell(cell);
    });
    cell.addEventListener('keydown',e=>{
      if(e.target===cell&&(e.key==='Enter'||e.key===' ')){
        e.preventDefault();openCell(cell);
      }
    });
    cell.querySelectorAll('[data-v3-open]').forEach(button=>
      button.addEventListener('click',()=>openCell(cell)));
  });
  dialog.querySelector('[data-v3-close]').addEventListener('click',()=>{
    if(busy)return;
    if(typeof dialog.close==='function')dialog.close();
    else dialog.removeAttribute('open');
  });
  dialog.addEventListener('click',e=>{
    if(e.target===dialog&&!busy){
      if(typeof dialog.close==='function')dialog.close();
      else dialog.removeAttribute('open');
    }
  });
  form.addEventListener('submit',async e=>{
    if(!window.fetch||!window.FormData)return; // Normal POST is still supported.
    e.preventDefault();
    if(busy)return;
    if(!selection.value){report('这个日期和节次没有可借用教室，请更换时间。');return;}
    busy=true;
    submit.disabled=true;
    submit.textContent='正在提交预约…';
    feedback.hidden=true;
    try{
      const response=await fetch(form.action,{
        method:'POST', body:new FormData(form), credentials:'same-origin',
        headers:{'X-Requested-With':'XMLHttpRequest','Accept':'application/json'}
      });
      const data=(response.headers.get('content-type')||'').includes('application/json')
        ? await response.json() : null;
      if(!data){report('服务器没有返回预约结果，请刷新页面查看是否已预约，避免重复提交。');}
      else if(!response.ok||!data.ok){report(data.message||'预约没有成功，请核对所填信息。');}
      else if(data.redirect_url){
        submit.textContent='预约成功，正在打开凭证…';
        window.location.assign(data.redirect_url);
        return;
      }else report('服务器未返回凭证地址，请先到“查询我的借用”核对。');
    }catch(err){
      report('网络异常，暂时无法确认是否预约成功。请先查询借用记录，避免重复提交。');
    }finally{
      busy=false;
      submit.disabled=false;
      submit.textContent=defaultLabel;
    }
  });
})();

// R11 stock dropdown: show free text only when explicitly choosing Other.
(()=>{
  const form=document.querySelector('[data-furniture-form]');
  if(!form)return;
  const sync=row=>{
    const choice=row.querySelector('select[name="item_name"]');
    const wrap=row.querySelector('.v11-other-label');
    if(!choice||!wrap)return;
    const other=choice.value==='__OTHER__';
    wrap.hidden=!other;
    const input=wrap.querySelector('input[name="item_custom_name"]');
    input.required=other;
    if(!other)input.value='';
  };
  form.querySelectorAll('.furniture-item-row').forEach(sync);
  form.addEventListener('change',e=>{
    if(e.target.matches('select[name="item_name"]')) sync(e.target.closest('.furniture-item-row'));
  });
  // Old row cloning logic lives in app.js. Delegation also handles cloned rows.
  form.addEventListener('click',e=>{
    if(e.target.closest('[data-add-loan-item]')){
      form.querySelectorAll('.furniture-item-row').forEach(sync);
      const last=form.querySelector('[data-loan-items] .furniture-item-row:last-child select[name="item_name"]');
      if(last)last.focus();
    }
  });
})();
