const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = v => { const n = Number(v); return Number.isFinite(n) ? n.toLocaleString('ko-KR', {maximumFractionDigits: 2}) : v; };
const ROLE_LABEL = {taken: '오늘 먹은 약 찍기', new: '새로 먹을 약 찍기'};
const QTY_LABEL = {taken: '오늘 먹은 개수', new: '이번에 먹을 개수'};

let token = '', busy = false;
let items = [];            // {role, approval_id, product, quantity}
let role = 'new', stream = null, photoBlob = null, photoUrl = '';
let match = null, chosen = null, page = 1, searchName = '';

const toast = m => { $('#status').textContent = m; clearTimeout(toast.t); toast.t = setTimeout(() => { $('#status').textContent = ''; }, 4000); };
const sheetSay = m => { $('#sheetStatus').textContent = m || ''; };

async function api(path, data) {
  if (busy) throw Error('진행 중인 요청을 기다려 주세요.');
  busy = true;
  try {
    const r = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-App-Token': token}, body: JSON.stringify(data)});
    const j = await r.json();
    if (!r.ok) throw Error(j.error || '요청을 처리하지 못했어요.');
    return j;
  } finally { busy = false; }
}

async function start() {
  try {
    const r = await (await fetch('/api/status')).json();
    token = r.token;
    const missing = [!r.mfds_ready && '식약처', !r.studio_ready && 'Upstage'].filter(Boolean);
    $('#connection').textContent = r.demo ? '가상 데모 모드' : missing.length ? `${missing.join('·')} 키 미입력` : '연결됨';
    $('#connection').className = 'pill ' + (r.demo ? 'demo' : missing.length ? 'off' : 'on');
  } catch { toast('서버 연결을 확인하세요.'); }
}

/* ---------- 카메라 ---------- */
function show(stage) { ['stageCamera', 'stageWait', 'stageResult'].forEach(id => { $('#' + id).hidden = id !== stage; }); }

async function openSheet(r) {
  if (!$('#consent').checked) { toast('먼저 사진 전송 동의를 체크해 주세요.'); $('#consent').focus(); return; }
  role = r; match = null; chosen = null; page = 1; searchName = '';
  $('#sheetTitle').textContent = ROLE_LABEL[r];
  sheetSay(''); show('stageCamera'); resetShot();
  $('#sheet').showModal();
  try {
    stream = await navigator.mediaDevices.getUserMedia({video: {facingMode: {ideal: 'environment'}, width: {ideal: 1920}}, audio: false});
    $('#video').srcObject = stream;
    $('#cameraNote').textContent = '약 상자 앞면(제품명·성분·함량)이 잘 보이게 찍어 주세요.';
  } catch {
    $('#cameraNote').textContent = '카메라를 열 수 없어요. 아래 "사진 파일 선택"을 이용하세요.';
    $('#shutter').disabled = true;
  }
}
function stopCamera() { if (stream) stream.getTracks().forEach(t => t.stop()); stream = null; $('#video').srcObject = null; }
function resetShot() {
  photoBlob = null; if (photoUrl) URL.revokeObjectURL(photoUrl); photoUrl = '';
  $('#preview').hidden = true; $('#video').hidden = false;
  $('#shutter').hidden = false; $('#shutter').disabled = false; $('#retake').hidden = true; $('#analyze').hidden = true;
}
function setPhoto(blob) {
  photoBlob = blob; photoUrl = URL.createObjectURL(blob);
  $('#preview').src = photoUrl; $('#preview').hidden = false; $('#video').hidden = true;
  $('#shutter').hidden = true; $('#retake').hidden = false; $('#analyze').hidden = false;
}
$('#shutter').onclick = () => {
  const v = $('#video');
  if (!v.videoWidth) return sheetSay('카메라가 아직 준비되지 않았어요.');
  const scale = Math.min(1, 2000 / Math.max(v.videoWidth, v.videoHeight));
  const c = $('#canvas'); c.width = Math.round(v.videoWidth * scale); c.height = Math.round(v.videoHeight * scale);
  c.getContext('2d').drawImage(v, 0, 0, c.width, c.height);
  c.toBlob(b => setPhoto(b), 'image/jpeg', 0.9);
};
$('#retake').onclick = resetShot;
$('#file').onchange = e => {
  const f = e.target.files[0]; e.target.value = '';
  if (!f) return;
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(f.type) || f.size > 10 * 1024 * 1024) return sheetSay('JPG·PNG·WEBP 사진(10MB 이하)을 선택하세요.');
  setPhoto(f);
};
$('#sheetClose').onclick = () => $('#sheet').close();
$('#sheet').addEventListener('close', stopCamera);

$('#analyze').onclick = async () => {
  try {
    sheetSay(''); stopCamera(); show('stageWait');
    const base64 = await new Promise((ok, no) => { const r = new FileReader(); r.onload = () => ok(r.result.split(',')[1]); r.onerror = no; r.readAsDataURL(photoBlob); });
    showMatch(await api('/api/analyze-and-match', {base64, mime: photoBlob.type || 'image/jpeg', consent: true}));
  } catch (e) { show('stageCamera'); sheetSay(e.message); }
};

/* ---------- 인식 결과와 제품 선택 ---------- */
function badge(c) {
  if (!c.comparison.calculation_ready) return ['계산 불가', 'review'];
  if (c.comparison.all_match) return ['성분 일치', 'ok'];
  if (c.comparison.rows.some(r => r.status === '함량 불일치')) return ['함량 다름', 'diff'];
  return ['확인 필요', 'review'];
}
const mg = s => s && s.mg !== null ? `${num(s.mg)}mg` : '–';
function compareTable(c) {
  return `<table><tr><th>성분</th><th class="num">사진</th><th class="num">공식</th><th></th></tr>${c.comparison.rows.map(r =>
    `<tr><td>${esc(r.name)}</td><td class="num">${mg(r.photo?.strength)}</td><td class="num">${mg(r.official?.strength)}</td><td>${r.status === '일치' ? '<span class="badge ok">✓</span>' : `<span class="badge ${r.status === '함량 불일치' ? 'diff' : 'review'}">${esc(r.status)}</span>`}</td></tr>`).join('')}</table>`;
}
function unitOf(c) { return c.comparison.official_ingredients?.[0]?.basis_unit || '개'; }

function showMatch(result) {
  match = result; show('stageResult');
  const ex = result.photo_extracted || {};
  $('#thumb').src = photoUrl; $('#thumb').hidden = !photoUrl;
  $('#recogName').textContent = [ex.product_name_raw, ex.manufacturer].filter(Boolean).join(' · ') || '제품명을 읽지 못했어요';
  const ingr = (ex.effective_ingredients || []).map(i => [i.ingredient_name || i.base_ingredient_name, i.strength_per_unit].filter(Boolean).join(' ')).filter(Boolean);
  $('#recogIngr').textContent = ingr.length ? '유효성분: ' + ingr.join(', ') : '유효성분을 읽지 못했어요';
  if (!searchName && typeof ex.product_name_raw === 'string') $('#query').value = ex.product_name_raw;
  const auto = (result.candidates || []).find(c => c.candidate_id === result.auto_candidate_id);
  $('#autoReason').textContent = [result.reason || result.auto_reason, ...(result.warnings || [])].filter(Boolean).join(' ');
  renderCandidates();
  if (auto) pick(auto, true); else { chosen = null; $('#pickedBox').innerHTML = ''; $('#otherBox').hidden = false; }
}

function renderCandidates() {
  const list = match.candidates || [];
  const pages = Math.max(1, Math.ceil((match.total || 0) / 10));
  $('#prev').hidden = page <= 1; $('#next').hidden = page >= pages;
  $('#candidates').innerHTML = list.map((c, n) => { const [l, cls] = badge(c);
    return `<button class="cand" data-cand="${n}"><span><strong>${esc(c.product.ITEM_NAME)}</strong><small>${esc(c.product.ENTP_NAME)}</small></span><span class="badge ${cls}">${l}</span></button>`; }).join('')
    || '<p class="hint">찾은 제품이 없어요. 제품명을 바꿔 검색해 보세요.</p>';
  document.querySelectorAll('[data-cand]').forEach(b => { b.onclick = () => pick(list[Number(b.dataset.cand)], false); });
}

function pick(c, isAuto) {
  chosen = c;
  const p = c.product, mismatch = !c.comparison.all_match;
  $('#pickedBox').innerHTML = `<div class="picked">
    <div class="top"><div><small>${isAuto ? '자동 선택된 공식 제품' : '선택한 공식 제품'}</small><strong>${esc(p.ITEM_NAME)}</strong><small>${esc(p.ENTP_NAME)} · 품목기준코드 ${esc(p.ITEM_SEQ)}</small></div><span class="badge ${badge(c)[1]}">${badge(c)[0]}</span></div>
    ${compareTable(c)}
    ${c.comparison.calculation_ready ? '' : '<p class="hint danger">공식 함량 기준을 확인하지 못해 이 제품은 추가할 수 없어요.</p>'}
    ${mismatch && c.comparison.calculation_ready ? '<label class="check"><input id="ack" type="checkbox"> 사진과 다른 항목을 확인했어요. 계산은 공식 값으로 해요.</label>' : ''}
    <div class="row"><span class="qty">${QTY_LABEL[role]} <input id="qty" type="number" min="0" max="100" step="any" value="1"> ${esc(unitOf(c))}</span>
    <button id="addPicked" class="primary wide" ${c.comparison.calculation_ready ? '' : 'disabled'}>목록에 추가</button></div>
    ${isAuto ? '<button id="notThis" class="link">다른 제품이에요 ›</button>' : ''}</div>`;
  $('#otherBox').hidden = isAuto;
  $('#addPicked').onclick = addPicked;
  if (isAuto) $('#notThis').onclick = () => { $('#otherBox').hidden = false; $('#notThis').remove(); $('#otherBox').scrollIntoView({behavior: 'smooth'}); };
}

async function addPicked() {
  try {
    sheetSay('');
    const quantity = $('#qty').value;
    if (!(Number(quantity) > 0)) throw Error('개수를 입력하세요.');
    if (items.some(i => i.product.code === String(chosen.product.ITEM_SEQ))) throw Error('이미 목록에 있는 약이에요.');
    const mismatch = !chosen.comparison.all_match;
    if (mismatch && !$('#ack')?.checked) throw Error('사진과 다른 항목을 확인하고 체크해 주세요.');
    const r = await api('/api/confirm-match', {candidate_id: chosen.candidate_id, confirmed: true, mismatch_ack: mismatch});
    items.push({role, approval_id: r.approval_id, product: r.product, quantity});
    $('#sheet').close(); renderLists();
    toast(`${r.product.name}을(를) 추가했어요.`);
  } catch (e) { sheetSay(e.message); }
}

async function search() {
  try { sheetSay(''); const r = await api('/api/match-search', {photo_id: match.photo_id, name: searchName, page}); r.photo_extracted = match.photo_extracted; match = r; renderCandidates(); $('#autoReason').textContent = [r.reason, ...(r.warnings || [])].filter(Boolean).join(' '); }
  catch (e) { sheetSay(e.message); }
}
$('#searchForm').onsubmit = e => { e.preventDefault(); page = 1; searchName = $('#query').value.trim(); search(); };
$('#prev').onclick = () => { page--; searchName ||= $('#query').value.trim(); search(); };
$('#next').onclick = () => { page++; searchName ||= $('#query').value.trim(); search(); };

/* ---------- 목록과 판정 ---------- */
function renderLists() {
  for (const r of ['taken', 'new']) {
    $('#list-' + r).innerHTML = items.map((it, n) => it.role !== r ? '' :
      `<div class="item"><div class="name"><strong>${esc(it.product.name)}</strong><small>${it.product.ingredients.map(i => `${esc(i.name)} ${num(i.amount)}${esc(i.unit)}`).join(', ')}</small></div>
       <span class="qty"><input data-qty="${n}" type="number" min="0" max="100" step="any" value="${esc(it.quantity)}" aria-label="${esc(it.product.name)} 개수"> ${esc(it.product.quantity_unit)}</span>
       <button class="link" data-del="${n}" aria-label="빼기">✕</button></div>`).join('');
  }
  document.querySelectorAll('[data-qty]').forEach(i => { i.oninput = () => { items[Number(i.dataset.qty)].quantity = i.value; $('#verdict').innerHTML = ''; }; });
  document.querySelectorAll('[data-del]').forEach(b => { b.onclick = () => { items.splice(Number(b.dataset.del), 1); renderLists(); }; });
  $('#judge').disabled = !items.some(i => i.role === 'new');
  $('#verdict').innerHTML = '';
}
document.querySelectorAll('[data-shoot]').forEach(b => { b.onclick = () => openSheet(b.dataset.shoot); });

$('#judge').onclick = async () => {
  try {
    $('#judge').disabled = true; toast('확인 중이에요…');
    const rep = await api('/api/evaluate', {mode: 'day', check_safety: true,
      products: items.map(i => ({approval_id: i.approval_id, quantity: i.quantity, role: i.role}))});
    const j = rep.judgement;
    const rows = rep.ingredients.map(i => {
      const pct = i.percent ?? null, cls = pct === null ? '' : pct > 100 ? 'over' : pct >= 80 ? 'hi' : '';
      return `<tr><td>${esc(i.ingredient)}${i.duplicate ? ' <span class="badge review">중복</span>' : ''}</td><td class="num">${num(i.taken_mg)}</td><td class="num">${num(i.new_mg)}</td>
        <td class="num"><b>${num(i.total_mg)}</b>${pct !== null ? `<div class="bar ${cls}"><i data-pct="${Math.min(pct, 100)}"></i></div>` : ''}</td>
        <td class="num">${i.limit_mg ? num(i.limit_mg) + (pct !== null ? `<br><small>${pct}%</small>` : '') : '<small>기준 없음</small>'}</td></tr>`; }).join('');
    $('#verdict').innerHTML = `<div class="verdict v-${j.level}"><h3>${esc(j.title)}</h3><p>${esc(j.message)}</p>${j.reasons.length ? `<ul>${j.reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}</div>
      <table><tr><th>성분 (mg)</th><th class="num">먹은 양</th><th class="num">새로</th><th class="num">하루 합계</th><th class="num">하루 최대</th></tr>${rows}</table>
      <details><summary>확인하지 않은 항목</summary><ul>${j.unassessed.map(u => `<li>${esc(u)}</li>`).join('')}</ul></details>`;
    // CSP(style-src 'self')가 인라인 style 속성을 막으므로 막대 길이는 스크립트로 설정
    document.querySelectorAll('[data-pct]').forEach(el => { el.style.width = el.dataset.pct + '%'; });
    $('#status').textContent = '';
    $('#verdict').scrollIntoView({behavior: 'smooth'});
  } catch (e) { toast(e.message); }
  finally { $('#judge').disabled = !items.some(i => i.role === 'new'); }
};

renderLists();
start();
