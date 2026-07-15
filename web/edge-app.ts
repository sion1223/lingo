// Supabase Edge Function: app — 아이패드/애플펜슬 한자 필기 채점 페이지 (정적 HTML)
const SCORE_FN = "https://tnsxhhdmnbhgzqgwosto.supabase.co/functions/v1/score";
const ANON_KEY =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRuc3hoaGRtbmJoZ3pxZ3dvc3RvIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODM4MjQwOTQsImV4cCI6MjA5OTQwMDA5NH0.hSWZAEHbbXTutOHxmofzJOC7yZBUhFgLARKSYF_AtxI";

const HTML = `<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>링고 — 한자 필기 채점</title>
<style>
  * { box-sizing: border-box; margin: 0; }
  body { font-family: -apple-system, "Apple SD Gothic Neo", sans-serif;
         background: #f5f3ee; color: #2b2925; padding: 16px;
         display: flex; flex-direction: column; align-items: center; }
  h1 { font-size: 20px; margin: 4px 0 12px; }
  #status { font-size: 13px; margin-bottom: 10px; }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%;
         margin-right: 5px; background: #bbb; }
  .on  { background: #34a853; } .off { background: #d93025; } .warm { background: #f9ab00; }
  #bar { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; flex-wrap: wrap; justify-content: center; }
  #chr { width: 64px; height: 44px; font-size: 26px; text-align: center;
         border: 1px solid #ccc; border-radius: 10px; background: #fff; }
  button { height: 44px; padding: 0 16px; font-size: 15px; border: none;
           border-radius: 10px; background: #e8e4db; color: #2b2925; }
  button:active { opacity: .7; }
  #go { background: #2b6cb0; color: #fff; font-weight: 600; }
  .quick { padding: 0 12px; font-size: 18px; }
  #wrap { position: relative; }
  canvas { background: #fff; border-radius: 16px; box-shadow: 0 2px 10px rgba(0,0,0,.08);
           touch-action: none; display: block; }
  #result { max-width: 520px; width: 100%; margin-top: 14px; }
  #score-big { font-size: 40px; font-weight: 700; text-align: center; }
  #score-sub { text-align: center; font-size: 13px; color: #777; margin-bottom: 8px; }
  .msg { background: #fff; border-radius: 12px; padding: 10px 14px; margin: 6px 0;
         font-size: 14px; box-shadow: 0 1px 4px rgba(0,0,0,.06); }
  .msg b { color: #2b6cb0; }
  #overlay { position: absolute; inset: 0; display: none; align-items: center;
             justify-content: center; background: rgba(255,255,255,.75);
             border-radius: 16px; font-size: 16px; font-weight: 600; }
</style>
</head>
<body>
<h1>링고 — 한자 필기 채점</h1>
<div id="status"><span class="dot" id="dot"></span><span id="stxt">서버 확인 중…</span></div>
<div id="bar">
  <input id="chr" value="永" maxlength="1">
  <button id="load">가이드</button>
  <button id="undo">한 획 지우기</button>
  <button id="clear">전체 지우기</button>
  <button id="go">채점하기</button>
</div>
<div id="bar">
  <button class="quick">永</button><button class="quick">水</button>
  <button class="quick">木</button><button class="quick">日</button>
  <button class="quick">語</button>
</div>
<div id="wrap">
  <canvas id="cv"></canvas>
  <div id="overlay">채점 중…</div>
</div>
<div id="result"></div>
<script>
var FN = '${SCORE_FN}';
var KEY = '${ANON_KEY}';
var cv = document.getElementById('cv');
var ctx = cv.getContext('2d');
var SIZE = Math.min(window.innerWidth - 32, 520);
var DPR = window.devicePixelRatio || 1;
cv.width = SIZE * DPR; cv.height = SIZE * DPR;
cv.style.width = SIZE + 'px'; cv.style.height = SIZE + 'px';
ctx.scale(DPR, DPR);
ctx.lineCap = 'round'; ctx.lineJoin = 'round';

var strokes = [];      // 완성 획 [[x,y]...] (0..1)
var cur = null;        // 그리는 중인 획
var template = null;   // 가이드 획
var lastReport = null;
var penSeen = false;

function api(payload, cb, errcb) {
  fetch(FN, { method: 'POST',
    headers: { 'Content-Type': 'application/json',
               'Authorization': 'Bearer ' + KEY, 'apikey': KEY },
    body: JSON.stringify(payload) })
  .then(function(r){ return r.json().then(function(b){ cb(r.status, b); }); })
  .catch(function(e){ if (errcb) errcb(e); });
}

function drawStroke(pts, color, width, dash) {
  if (pts.length < 2) return;
  ctx.strokeStyle = color; ctx.lineWidth = width;
  ctx.setLineDash(dash || []);
  ctx.beginPath();
  ctx.moveTo(pts[0][0] * SIZE, pts[0][1] * SIZE);
  for (var i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0] * SIZE, pts[i][1] * SIZE);
  ctx.stroke(); ctx.setLineDash([]);
}

function qColor(q) { return q > 0.7 ? '#34a853' : (q > 0.4 ? '#f9ab00' : '#d93025'); }

function redraw() {
  ctx.clearRect(0, 0, SIZE, SIZE);
  // 십자 보조선
  ctx.strokeStyle = '#eee'; ctx.lineWidth = 1; ctx.setLineDash([6, 6]);
  ctx.beginPath(); ctx.moveTo(SIZE/2, 0); ctx.lineTo(SIZE/2, SIZE);
  ctx.moveTo(0, SIZE/2); ctx.lineTo(SIZE, SIZE/2); ctx.stroke(); ctx.setLineDash([]);
  if (template) {
    for (var j = 0; j < template.length; j++) drawStroke(template[j], '#d8d3c8', 7);
    for (var j2 = 0; j2 < template.length; j2++) {
      var p = template[j2][0];
      ctx.fillStyle = '#a89f8f'; ctx.font = '12px sans-serif';
      ctx.fillText(String(j2 + 1), p[0] * SIZE + 4, p[1] * SIZE - 4);
    }
  }
  if (lastReport && lastReport.user) {
    for (var k = 0; k < lastReport.user.length; k++) {
      var e = lastReport.strokes[k];
      drawStroke(lastReport.user[k], qColor(e ? e.q : 0.5), 5);
    }
    if (lastReport.missing && template) {
      for (var m = 0; m < lastReport.missing.length; m++)
        drawStroke(template[lastReport.missing[m]], '#2b6cb0', 4, [8, 6]);
    }
  } else {
    for (var s = 0; s < strokes.length; s++) drawStroke(strokes[s], '#2b2925', 5);
    if (cur) drawStroke(cur, '#2b2925', 5);
  }
}

function pos(ev) {
  var r = cv.getBoundingClientRect();
  return [(ev.clientX - r.left) / SIZE, (ev.clientY - r.top) / SIZE];
}
cv.addEventListener('pointerdown', function(ev) {
  if (ev.pointerType === 'pen') penSeen = true;
  if (penSeen && ev.pointerType === 'touch') return; // 손바닥 무시
  ev.preventDefault();
  lastReport = null; document.getElementById('result').innerHTML = '';
  cur = [pos(ev)];
  cv.setPointerCapture(ev.pointerId);
});
cv.addEventListener('pointermove', function(ev) {
  if (!cur) return;
  if (penSeen && ev.pointerType === 'touch') return;
  ev.preventDefault();
  var evs = ev.getCoalescedEvents ? ev.getCoalescedEvents() : [ev];
  for (var i = 0; i < evs.length; i++) cur.push(pos(evs[i]));
  redraw();
});
function endStroke(ev) {
  if (!cur) return;
  if (cur.length >= 2) strokes.push(cur);
  cur = null; redraw();
}
cv.addEventListener('pointerup', endStroke);
cv.addEventListener('pointercancel', endStroke);

document.getElementById('undo').onclick = function() {
  strokes.pop(); lastReport = null;
  document.getElementById('result').innerHTML = ''; redraw();
};
document.getElementById('clear').onclick = function() {
  strokes = []; lastReport = null;
  document.getElementById('result').innerHTML = ''; redraw();
};
var quicks = document.getElementsByClassName('quick');
for (var qi = 0; qi < quicks.length; qi++) {
  quicks[qi].onclick = function(e) {
    document.getElementById('chr').value = e.target.textContent;
    loadTemplate();
  };
}

function setStatus(cls, txt) {
  document.getElementById('dot').className = 'dot ' + cls;
  document.getElementById('stxt').textContent = txt;
}
function health() {
  api({ action: 'health' }, function(st, b) {
    if (b.ok && b.model_loaded) setStatus('on', 'GPU 서버 온라인 (' + (b.device || '') + ')');
    else if (b.ok) setStatus('warm', '서버 부팅 중 — 모델 로드까지 잠시 기다리세요');
    else setStatus('off', '서버 꺼짐 — RunPod에서 pod를 켜세요');
  }, function() { setStatus('off', '연결 실패'); });
}
health(); setInterval(health, 20000);

function loadTemplate() {
  var ch = document.getElementById('chr').value.trim();
  if (!ch) return;
  api({ action: 'template', char: ch }, function(st, b) {
    if (st === 200) { template = b.strokes; lastReport = null; redraw(); }
    else alert(b.message || b.detail || '템플릿을 불러올 수 없습니다');
  });
}
document.getElementById('load').onclick = loadTemplate;

document.getElementById('go').onclick = function() {
  var ch = document.getElementById('chr').value.trim();
  if (!ch) { alert('문자를 입력하세요'); return; }
  if (strokes.length === 0) { alert('먼저 글자를 써 보세요'); return; }
  document.getElementById('overlay').style.display = 'flex';
  api({ action: 'score', char: ch, strokes: strokes }, function(st, b) {
    document.getElementById('overlay').style.display = 'none';
    if (st !== 200) {
      document.getElementById('result').innerHTML =
        '<div class="msg">' + (b.message || b.detail || '오류: ' + st) + '</div>';
      return;
    }
    lastReport = b;
    if (!template) template = b.template;
    redraw();
    var html = '<div id="score-big">' + b.score + '점</div>' +
      '<div id="score-sub">모델 점수 ' + b.base_model_score +
      ' · ' + b.elapsed + '초</div>';
    var corr = b.corrections || [];
    for (var i = 0; i < corr.length; i++) {
      var c = corr[i];
      var label = c.index >= 0 ? (c.index + 1) + '번 획' : '누락';
      var gain = (c.gain != null && c.gain > 0) ? ' (+' + c.gain.toFixed(1) + '점 기대)' : '';
      html += '<div class="msg"><b>' + label + gain + '</b> — ' +
              c.messages.join(' · ') + '</div>';
    }
    if (corr.length === 0)
      html += '<div class="msg">교정할 획이 없습니다 — 잘 썼습니다!</div>';
    document.getElementById('result').innerHTML = html;
  }, function() {
    document.getElementById('overlay').style.display = 'none';
    document.getElementById('result').innerHTML =
      '<div class="msg">네트워크 오류 — 다시 시도하세요</div>';
  });
};
redraw();
</script>
</body>
</html>`;

Deno.serve(() =>
  new Response(HTML, {
    headers: { "Content-Type": "text/html; charset=utf-8" },
  })
);
