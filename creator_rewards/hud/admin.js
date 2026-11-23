/* Jarvis HUD — "Blogerlar" bo'limi (Creator Rewards boshqaruvi).
   Jarvis HUD'ning umumiy yordamchilaridan foydalanadi: api, esc, fmt, $, haptic, DEMO. */
(function(){
  const box = () => document.getElementById('creatorsBox');
  const som = n => fmt(n) + " so'm";
  const day = iso => iso ? new Date(iso).toLocaleDateString('ru-RU', {timeZone:'Asia/Tashkent'}) : '—';
  const RS = {monitoring:'Kuzatuvda', awaiting_final:'Yakuniy skrinshot', awaiting_approval:'Tasdiq kutilmoqda',
    rewarded:'Vaucher berildi', no_reward:"10 000 ga yetmadi", rejected:'Rad etildi', blocked:"To'xtatilgan"};
  const CS = {approved:'Tasdiqlangan', pending:'Ariza', rejected:'Rad etilgan', blocked:"To'xtatilgan"};
  const VS = {active:'Faol', used:'Ishlatilgan', merged:"Keyingisiga qo'shilgan", expired:'Muddati tugagan'};
  let tool = 'redeem', current = null;

  async function call(path, body){
    if (DEMO) return demo(path, body);
    return body ? api(path, {method:'POST', body}) : api(path);
  }

  async function act(body, after){
    try{ const r = await call('/creator/admin/api/action', body); haptic('ok'); toastMsg(r.message || 'Saqlandi'); (after||load)() }
    catch(e){ haptic('err'); alert(e.message) }
  }
  function toastMsg(t){ const el = document.getElementById('crToast'); if(!el) return; el.textContent = t; el.style.opacity = 1;
    setTimeout(()=>el.style.opacity = 0, 2200) }

  window.loadCreators = load;
  function setBadge(n){ const b = document.getElementById('crBadge'); if (b){ b.textContent = n; b.style.display = n > 0 ? 'inline-flex' : 'none' } }
  // Bosh sahifadagi «Blogerlar» tugmasida kutilayotgan tasdiqlar soni
  setTimeout(async () => { try{ const d = await call('/creator/admin/api/overview');
    setBadge(d.applications.length + d.screenshots.length + d.rewards.length) }catch(e){} }, 1500);
  async function load(){
    current = null;
    let d; try{ d = await call('/creator/admin/api/overview') }
    catch(e){ box().innerHTML = `<div class="err">${esc(e.message)}</div>`; return }
    const s = d.stats;
    const preq = d.products_enabled ? (d.product_requests || []) : [];
    const pend = d.applications.length + d.screenshots.length + d.rewards.length + preq.length;
    setBadge(pend);
    box().innerHTML = `
      ${d.bot_running === false ? `<div class="err">Blogerlar boti ishlamayapti: Railway'da CREATOR_BOT_TOKEN qo'shing.</div>` : ''}
      <div class="card"><div class="grid">
        <div class="tile"><div class="k">👥 Blogerlar</div><div class="v">${fmt(s.creators)}</div><div class="d">${s.applications ? s.applications + ' ta yangi ariza' : 'tasdiqlangan'}</div></div>
        <div class="tile"><div class="k">🎬 Kuzatuvdagi video</div><div class="v">${fmt(s.active_reels)}</div><div class="d">30 kunlik hisobda</div></div>
        <div class="tile"><div class="k">🎟 Vaucher · 30 kun</div><div class="v">${som(s.issued_30d)}</div><div class="d">chiqarilgan mukofot</div></div>
        <div class="tile"><div class="k">📌 Ishlatilmagan</div><div class="v">${som(s.outstanding)}</div><div class="d">do'kon majburiyati</div></div>
        <div class="tile"><div class="k">🛒 Promo savdo · 30 kun</div><div class="v">${som(s.promo_30d)}</div><div class="d">blogerlar kodlari orqali</div></div>
        <div class="tile"><div class="k">⏳ Tasdiq kutmoqda</div><div class="v ${pend ? 'warn' : ''}">${fmt(pend)}</div><div class="d">ariza, skrinshot, mukofot</div></div>
      </div></div>

      ${preq.map(r => `<div class="card">
        <div class="ch"><h3>🧸 @${esc(r.instagram)}: mahsulot so'rovi</h3><span class="s">${day(r.created_at)}</span></div>
        <div class="muted" style="font-size:15px;color:var(--text)"><b>${esc(r.product)}</b></div>
        <div class="grid" style="margin-top:10px"><button class="btn green" data-a="preq" data-id="${r.id}" data-ok="1">Ruxsat berish</button>
          <button class="btn red" data-a="preq" data-id="${r.id}" data-ok="0">Rad etish</button></div></div>`).join('')}

      ${d.applications.map(c => `<div class="card">
        <div class="ch"><h3>🆕 Ariza: @${esc(c.instagram)}</h3><span class="s">${day(c.created_at)}</span></div>
        <div class="muted">${esc(c.first_name)} ${esc(c.last_name||'')} · ${c.username ? '@'+esc(c.username) : ''} · ${esc(c.phone||'')}</div>
        ${vetBlock(c)}
        <div style="margin:10px 0"><a href="https://instagram.com/${esc(c.instagram)}" target="_blank">Instagram profilini ochish ›</a></div>
        <div class="grid"><button class="btn green" data-a="creator" data-tg="${c.tg_id}" data-ok="1">Tasdiqlash</button>
          <button class="btn red" data-a="creator" data-tg="${c.tg_id}" data-ok="0">Rad etish</button></div></div>`).join('')}

      ${d.screenshots.map(sh => `<div class="card">
        <div class="ch"><h3>📸 @${esc(sh.instagram)} · ${esc(sh.shortcode)}</h3><span class="s">${day(sh.created_at)}</span></div>
        ${sh.image_url ? `<a href="${esc(sh.image_url)}" target="_blank"><img src="${esc(sh.image_url)}" alt="Insights skrinshoti" style="width:100%;max-height:360px;object-fit:contain;border-radius:12px;background:#000"></a>` : ''}
        <div class="muted" style="margin-top:8px">Jarvis o'qidi: <b>${sh.ocr_views != null ? fmt(sh.ocr_views) : '—'}</b>${sh.typed_views != null ? ` · Bloger yozdi: <b>${fmt(sh.typed_views)}</b>` : ''} · Oldingi: ${fmt(sh.last_views)}</div>
        ${(sh.flags||[]).map(f => `<div class="warn" style="font-size:13.5px;margin-top:4px">⚠️ ${esc(f)}</div>`).join('') || `<div class="up" style="font-size:13.5px;margin-top:4px">✅ Shubhali belgi yo'q</div>`}
        <label class="f">Tasdiqlanadigan ko'rishlar</label><input type="number" inputmode="numeric" id="sv-${sh.id}" value="${sh.views}">
        <button class="btn green" style="margin-top:10px" data-a="verify" data-id="${sh.id}">Tasdiqlash</button>
        <div class="muted" style="margin-top:6px"><a href="${esc(sh.url)}" target="_blank">Videoni ochish ›</a></div></div>`).join('')}

      ${d.rewards.map(w => `<div class="card">
        <div class="ch"><h3>💰 Mukofot: @${esc(w.instagram)}</h3><span class="s">${esc(w.shortcode)}</span></div>
        <div class="muted">30 kunlik ko'rishlar: <b>${fmt(w.views)}</b> → <b>${som(w.amount)}</b></div>
        <div class="grid" style="margin-top:10px"><button class="btn green" data-a="reward" data-id="${w.id}" data-ok="1">Vaucher chiqarish</button>
          <button class="btn red" data-a="reward" data-id="${w.id}" data-ok="0">Rad etish</button></div></div>`).join('')}

      ${pend ? '' : `<div class="calm">✅ Tasdiq kutayotgan narsa yo'q</div>`}

      <div class="card">
        <div class="ch"><h3>Do'kon kassasi</h3></div>
        <div class="seg" id="crTools">
          <button data-t="redeem" class="${tool==='redeem'?'on':''}">Vaucher</button>
          <button data-t="promo" class="${tool==='promo'?'on':''}">Promo-kod</button>
          ${d.products_enabled ? `<button data-t="products" class="${tool==='products'?'on':''}">Mahsulot</button>` : ''}
          <button data-t="brief" class="${tool==='brief'?'on':''}">Topshiriq</button></div>
        <div id="crTool"></div>
      </div>

      <div class="card">
        <div class="ch"><h3>Blogerlar</h3><span class="s">${d.creators.length} ta</span></div>
        ${d.creators.length ? d.creators.map(c => `<div class="need" style="cursor:pointer" data-open="${c.tg_id}">
          <div class="t">${c.ig_connected ? '🔗 ' : ''}@${esc(c.instagram||'—')} <small>${esc(c.first_name)} · ${c.level} · ${CS[c.status]||c.status} · ${c.active_reels} faol video · promo ${c.promo.count} ta</small></div>
          <span style="color:var(--gold)">›</span></div>`).join('') : `<div class="muted">Hali bloger yo'q.</div>`}
      </div>
      <div id="crToast" style="position:fixed;left:50%;bottom:100px;transform:translateX(-50%);background:var(--gold);color:#1d1306;padding:10px 16px;border-radius:12px;font-weight:800;opacity:0;transition:opacity .2s;z-index:30;pointer-events:none"></div>`;
    renderTool(d.brief, d.products);
    box().querySelectorAll('[data-a]').forEach(b => b.onclick = () => {
      const a = b.dataset.a;
      if (a === 'creator') act({action:'creator', tg_id:+b.dataset.tg, approve:b.dataset.ok === '1'});
      if (a === 'reward') act({action:'reward', reward_id:+b.dataset.id, approve:b.dataset.ok === '1'});
      if (a === 'preq') act({action:'product_request', request_id:+b.dataset.id, approve:b.dataset.ok === '1'});
      if (a === 'verify') act({action:'verify', snapshot_id:+b.dataset.id, views:+document.getElementById('sv-'+b.dataset.id).value});
    });
    box().querySelectorAll('#crTools button').forEach(b => b.onclick = () => { tool = b.dataset.t;
      box().querySelectorAll('#crTools button').forEach(x => x.classList.toggle('on', x === b)); renderTool(d.brief, d.products) });
    box().querySelectorAll('[data-open]').forEach(el => el.onclick = () => openCreator(+el.dataset.open));
  }

  function vetBlock(c){
    let r = null; try{ r = c.vet_report ? JSON.parse(c.vet_report) : null }catch(e){}
    if (!r) return `<div class="muted" style="margin-top:8px">📊 Avtomatik baho yo'q — profilni o'zingiz ko'ring.</div>`;
    const tone = r.score >= 7 ? 'up' : r.score >= 4 ? 'warn' : 'down';
    return `<div style="margin-top:10px"><div class="${tone}" style="font-weight:800;font-size:17px">📊 Faollik: ${r.score ?? '—'} / 10</div>
      ${(r.parts||[]).map(p => `<div class="muted" style="font-size:13px">• ${esc(p.name)}: ${p.points}/10 — ${esc(p.detail)}</div>`).join('')}
      ${(r.warnings||[]).map(w => `<div class="warn" style="font-size:13px">⚠️ ${esc(w)}</div>`).join('')}</div>`;
  }

  function renderTool(brief, products){
    const el = document.getElementById('crTool');
    if (tool === 'products'){
      el.innerHTML = `<label class="f">Ruxsat ro'yxatiga mahsulot qo'shish</label>
        <input id="crProd" placeholder="Masalan: Rolikli krossovka"><button class="btn" style="margin-top:10px" id="crProdAdd">Qo'shish</button>
        <div class="muted" style="margin:10px 0 4px">Blogerlar faqat ro'yxatdagi yoki o'zlari yozgan mahsulotni so'raydi, ruxsatni siz berasiz.</div>
        ${(products||[]).map(p => `<div class="need" style="cursor:default"><div class="t">${esc(p.name)} <small>${p.active ? 'ro\'yxatda' : 'olib tashlangan'}</small></div>
          <button class="btn ${p.active ? 'red' : 'green'}" style="width:auto;padding:8px 12px" data-pt="${p.id}" data-on="${p.active ? 0 : 1}">${p.active ? 'Olib tashlash' : 'Qaytarish'}</button></div>`).join('')}`;
      document.getElementById('crProdAdd').onclick = () => act({action:'product_add', name:document.getElementById('crProd').value});
      el.querySelectorAll('[data-pt]').forEach(b => b.onclick = () => act({action:'product_active', product_id:+b.dataset.pt, active:b.dataset.on === '1'}));
      return;
    }
    if (tool === 'brief'){
      el.innerHTML = `<label class="f">Blogerlar kabinetida ko'rinadigan joriy topshiriq</label>
        <textarea id="crBrief" rows="4" placeholder="Masalan: shu hafta rolikli krossovka va dronlarni suratga oling">${esc(brief||'')}</textarea>
        <button class="btn" style="margin-top:10px" id="crBriefSave">Saqlash</button>`;
      document.getElementById('crBriefSave').onclick = () => act({action:'brief', text:document.getElementById('crBrief').value});
      return;
    }
    const isV = tool === 'redeem';
    el.innerHTML = `<label class="f">${isV ? 'Vaucher kodi' : 'Promo-kod'}</label>
      <input id="crCode" placeholder="${isV ? 'BD-XXXX-XXXX' : 'MADINABL'}" style="text-transform:uppercase">
      <label class="f">${isV ? 'Vaucherdan ishlatilgan summa' : 'Xarid summasi'} (so'm)</label><input id="crSum" type="number" inputmode="numeric">
      <label class="f">Chek raqami (ixtiyoriy)</label><input id="crRcpt">
      <button class="btn" style="margin-top:10px" id="crGo">${isV ? 'Ishlatish' : 'Xaridni yozish'}</button>
      <div class="muted" style="margin-top:6px">${isV ? "Kod bir marta ishlaydi. Qoldiq blogerning keyingi vaucheriga o'tadi va unga xabar boradi." : "Blogerga xabar boradi, kabinetida xaridlar soni ko'rinadi."}</div>`;
    document.getElementById('crGo').onclick = () => act({action: tool, code: document.getElementById('crCode').value.trim(),
      amount: +document.getElementById('crSum').value, receipt: document.getElementById('crRcpt').value});
  }

  async function openCreator(tgId){
    let d; try{ d = await call('/creator/admin/api/creator/' + tgId) } catch(e){ alert(e.message); return }
    current = tgId; const p = d.profile; window.scrollTo({top:0});
    const lv = p.level_override || 'auto';
    box().innerHTML = `
      <button class="btn ghost" id="crBack">‹ Blogerlar</button>
      <div class="card">
        <div class="ch"><h3>@${esc(p.instagram)}</h3><span class="s">${CS[p.status]||p.status}</span></div>
        <div class="muted">${esc(p.first_name)} ${esc(p.last_name||'')} · ${p.username ? '@'+esc(p.username) : ''} · ${esc(p.phone||'')}<br>
          Promo-kod: <b>${esc(p.promo_code||'—')}</b> · ${d.promo.count} ta xarid (${som(d.promo.sum)})<br>
          Balans: <b>${som(d.balance.available)}</b> · Kutilayotgan: ${som(d.balance.pending)} · Jami: ${som(d.balance.earned_total)}</div>
        <div class="muted" style="margin-top:6px">${d.ig_link ? (d.ig_link.status === 'ok'
          ? `🔗 Instagram ulangan · oxirgi yangilanish ${day(d.ig_link.last_sync_at)}` : `⚠️ Instagram ulanishi uzilgan`) : '📸 Instagram ulanmagan — skrinshot orqali'}</div>
        ${p.terms_accepted_at ? `<div class="muted" style="margin-top:6px">Shartlar qabul qilingan: v${esc(p.terms_version)} · ${day(p.terms_accepted_at)}</div>` : ''}
        ${vetBlock({vet_report: d.vetting && d.vetting.report ? JSON.stringify(d.vetting.report) : null})}
        <button class="btn ghost" style="margin-top:10px" id="crVet">📊 Qayta baholash</button>
        <label class="f">Daraja (hozir: ${d.level}${d.level_manual ? ', qo\'lda' : ', avtomatik'})</label>
        <div class="seg" id="crLevel">${['auto','Yangi','Faol','Hamkor'].map(l => `<button data-l="${l}" class="${lv===l?'on':''}">${l==='auto'?'Avto':l}</button>`).join('')}</div>
        <button class="btn ${p.status==='blocked'?'green':'red'}" style="margin-top:12px" id="crBlock">${p.status==='blocked' ? 'Qayta faollashtirish' : "To'xtatish (blok)"}</button>
      </div>
      <div class="sub" style="margin:14px 4px 0">Videolar va skrinshotlar</div>
      ${d.reels.length ? d.reels.map(r => `<div class="card">
        <div class="ch"><h3><a href="${esc(r.url)}" target="_blank">${esc(r.shortcode)}</a></h3><span class="s">${RS[r.status]||r.status}</span></div>
        <div class="muted">🧸 ${esc(r.product || '—')} · ${day(r.posted_at)} → ${day(r.ends_at)} · ${r.day}/30 kun · ${fmt(r.views)} ko'rish · ${som(r.reward)}</div>
        <div style="display:flex;gap:10px;overflow-x:auto;margin-top:10px;padding-bottom:4px">
          ${(r.snapshots||[]).map(s => `<a href="${esc(s.image_url||'#')}" target="_blank" style="flex:none;width:110px;text-decoration:none;color:inherit">
            ${s.image_url ? `<img src="${esc(s.image_url)}" alt="" style="width:110px;height:180px;object-fit:cover;border-radius:10px;background:#000">` : `<div class="tile" style="height:180px"></div>`}
            <div style="font-size:12.5px;font-weight:700;margin-top:4px">${fmt(s.views)} ${s.verified ? '✅' : '⏳'}</div>
            <div class="muted" style="font-size:11.5px">${day(s.created_at)}${(s.flags||[]).length ? ' · ⚠️ ' + s.flags.length : ''}</div></a>`).join('') || `<div class="muted">Skrinshot hali yo'q</div>`}
        </div></div>`).join('') : `<div class="card muted">Video yo'q</div>`}
      <div class="card"><div class="ch"><h3>Vaucherlar</h3></div>
        ${d.vouchers.length ? d.vouchers.map(v => `<div class="muted" style="padding:6px 0">${esc(v.code)} · ${som(v.nominal)} · ${VS[v.status]||v.status}${v.used_amount ? ' · ishlatildi '+som(v.used_amount) : ''} · ${day(v.issued_at)}</div>`).join('') : `<div class="muted">Yo'q</div>`}
      </div>
      <div class="card"><div class="ch"><h3>Promo-kod xaridlari</h3></div>
        ${d.promo_sales.length ? d.promo_sales.map(s => `<div class="muted" style="padding:6px 0">${som(s.amount)} · ${day(s.at)}${s.receipt ? ' · chek '+esc(s.receipt) : ''}</div>`).join('') : `<div class="muted">Hali yo'q</div>`}
      </div>
      <div id="crToast" style="position:fixed;left:50%;bottom:100px;transform:translateX(-50%);background:var(--gold);color:#1d1306;padding:10px 16px;border-radius:12px;font-weight:800;opacity:0;transition:opacity .2s;z-index:30;pointer-events:none"></div>`;
    document.getElementById('crBack').onclick = load;
    document.getElementById('crVet').onclick = () => act({action:'vet', tg_id:tgId}, () => openCreator(tgId));
    box().querySelectorAll('#crLevel button').forEach(b => b.onclick = () =>
      act({action:'level', tg_id:tgId, level: b.dataset.l === 'auto' ? null : b.dataset.l}, () => openCreator(tgId)));
    document.getElementById('crBlock').onclick = () =>
      act({action:'status', tg_id:tgId, status: p.status === 'blocked' ? 'approved' : 'blocked'}, () => openCreator(tgId));
  }

  // ---------- namuna (?demo) ----------
  function demo(path){
    const now = Date.now(), iso = d => new Date(now + d*864e5).toISOString();
    if (path.endsWith('/overview')) return {bot_running:true, products_enabled:false, brief:"Shu hafta rolikli krossovka va dronlarni suratga oling",
      stats:{creators:12, applications:1, active_reels:9, issued_30d:2340000, outstanding:1180000, promo_30d:4650000},
      applications:[{tg_id:7, instagram:'zarina_mama', first_name:'Zarina', last_name:'', username:'zarina', phone:'+998 93 000 00 00', created_at:iso(-1),
        vet_report: JSON.stringify({score:7.4, parts:[{name:'Auditoriya hajmi', points:10, detail:'38 400 obunachi'}, {name:"Ko'rishlar / obunachi", points:6, detail:"o'rtacha (mediana) 7 900 ko'rish — obunachilarning 21%"},
          {name:'Faollik (layk + izoh / ko\'rish)', points:8, detail:'5.6%'}, {name:"So'nggi post", points:10, detail:'1 kun oldin'}, {name:'Muntazamlik', points:8, detail:'oxirgi 30 kunda 9 ta post'}],
          warnings:[]})}],
      product_requests:[{id:3, instagram:'madina_blog', product:'Rolikli krossovka, pushti, 33-o\'lcham', created_at:iso(0)}],
      products:[{id:1, name:'Rolikli krossovka', active:1}, {id:2, name:'Dron X5', active:1}, {id:3, name:"Qo'g'irchoq uy", active:0}],
      screenshots:[{id:41, instagram:'madina_blog', shortcode:'DfzXgaOtx34', created_at:iso(0), ocr_views:47250, typed_views:47250, last_views:31800, views:47250,
        flags:[], url:'#'}],
      rewards:[{id:9, instagram:'aziz_vlog', shortcode:'DfwrT0BtJrI', views:34100, amount:196000}],
      creators:[{tg_id:1, instagram:'madina_blog', first_name:'Madina', level:'Faol', status:'approved', active_reels:2, promo:{count:14}},
        {tg_id:2, instagram:'aziz_vlog', first_name:'Aziz', level:'Hamkor', status:'approved', active_reels:1, promo:{count:31}}]};
    if (path.includes('/creator/admin/api/creator/')) return {profile:{instagram:'madina_blog', first_name:'Madina', status:'approved', promo_code:'MADINABL', phone:'+998 90 123 45 67', level_override:null, terms_version:'1.0', terms_accepted_at:iso(-60)},
      vetting:{score:8.1, report:{score:8.1, parts:[{name:'Auditoriya hajmi', points:10, detail:'24 100 obunachi'}], warnings:[]}},
      level:'Faol', level_manual:false, promo:{count:14, sum:3200000}, balance:{available:300000, pending:444000, earned_total:720000},
      reels:[{url:'#', shortcode:'DfzXgaOtx34', product:'Rolikli krossovka', status:'monitoring', posted_at:iso(-12), ends_at:iso(18), day:12, views:47250, reward:248000,
        snapshots:[{views:3100, verified:1, created_at:iso(-11), flags:[]}, {views:31800, verified:1, created_at:iso(-5), flags:[]}, {views:47250, verified:0, created_at:iso(0), flags:[]}]}],
      vouchers:[{code:'BD-7K92-QX4M', nominal:300000, status:'active', issued_at:iso(-9)}], promo_sales:[{amount:250000, at:iso(-2)}]};
    return {ok:true, message:'Namuna rejimi: saqlanmadi'};
  }
})();
