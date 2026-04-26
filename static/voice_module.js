/**
 * voice_module.js — El Mazraa Voice Over v7
 *
 * CHANGES vs v6 (ONLY these):
 *  1. Browse popup: plus grand, animé avec son + shimmer + particules
 *     + window.triggerVoiceBrowseOpen() exposée → le LLM peut cliquer via voiceHint()
 *  2. Rapport: _renderReport() injecte un HTML pro (logo, sections stylées, badges)
 *     auto-déclenché à chaque mise à jour de #report-content
 *
 * TOUT LE RESTE EST IDENTIQUE À v6.
 */

(function () {
  'use strict';

  const SEGMENT_MS     = 3500;
  const MIN_AUDIO_MS   = 600;
  const SILENCE_RMS    = 0.007;
  const SILENCE_PEAK   = 0.012;
  const API_TRANSCRIBE = '/voice/transcribe';

  const HALLUCINATION_PATTERNS = [
    /^sous-titres par/i, /^subtitles? by/i, /^transcribed? by/i,
    /^jordan c\d/i, /^satu$/i, /^kot top$/i, /^\d\s+[a-z]\s+[A-Z]$/,
    /patchwork/i, /juanfrance/i, /telecomushina/i, /revitalization.*grave/i,
    /^point,\s*wait/i, /sofi doll/i, /^cer.*zier/i,
  ];

  let _active = false, _muted = false, _stream = null;
  let _recorder = null, _processing = false, _segStart = 0;
  const _synth = window.speechSynthesis;
  const _fileInputCache = {};

  /* ── Browse Observer ── */
  function _initBrowseObserver() {
    _scanForFileInputs();
    new MutationObserver(() => _scanForFileInputs()).observe(document.body, { childList: true, subtree: true });
  }
  function _scanForFileInputs() {
    ['inp-water-rgb','inp-water-micro','inp-manure-rgb','inp-manure-micro'].forEach(id => {
      const fi = document.getElementById(`_fi_${id}`);
      if (fi) { _fileInputCache[id] = fi; return; }
      const f = document.getElementById(id);
      if (f?._browseInput) _fileInputCache[id] = f._browseInput;
    });
  }

  /* ── Context ── */
  function _ctx() {
    const g = id => (document.getElementById(id)?.value || '').trim();
    const w = g('inp-water-rgb'), wm = g('inp-water-micro'), m = g('inp-manure-rgb'), mm = g('inp-manure-micro');
    const reportVisible = (document.getElementById('report-wrapper')?.style.display || 'none') !== 'none'
      || (document.getElementById('report-content')?.textContent || '').length > 50;
    return {
      water_rgb_filled: !!w, water_micro_filled: !!wm, manure_rgb_filled: !!m, manure_micro_filled: !!mm,
      question_filled: !!g('inp-request'), report_ready: reportVisible,
      analysis_running: document.getElementById('btn-launch')?.disabled === true,
      any_image_filled: !!(w || wm || m || mm),
      active_tab: document.querySelector('.tab.active')?.textContent?.trim() || 'Rapport',
    };
  }

  /* ── Silence / Hallucination ── */
  function _isSilent(pcm) {
    let sum = 0, peak = 0;
    for (let i = 0; i < pcm.length; i++) { const v = Math.abs(pcm[i]); sum += v*v; if (v > peak) peak = v; }
    return Math.sqrt(sum/pcm.length) < SILENCE_RMS && peak < SILENCE_PEAK;
  }
  function _isHallucination(t) {
    t = (t||'').trim();
    return !t || t.length < 2 || HALLUCINATION_PATTERNS.some(p => p.test(t));
  }

  /* ── TTS ── */
  function _speak(text, onEnd) {
    if (!_synth || !text) { onEnd?.(); return; }
    _synth.cancel(); _muted = true;
    const utt = new SpeechSynthesisUtterance(text);
    utt.lang = 'fr-FR'; utt.rate = 1.08; utt.pitch = 1.0; utt.volume = 0.9;
    const voices = _synth.getVoices();
    const fr = voices.find(v => v.lang.startsWith('fr') && v.localService) || voices.find(v => v.lang.startsWith('fr'));
    if (fr) utt.voice = fr;
    const release = () => setTimeout(() => { _muted = false; onEnd?.(); }, 350);
    utt.onend = release; utt.onerror = release;
    _synth.speak(utt); _setUI('speaking');
  }

  /* ── Recording ── */
  function _nextSegment() {
    if (!_active || !_stream) return;
    const chunks = []; let rec;
    try { rec = new MediaRecorder(_stream, { mimeType: 'audio/webm;codecs=opus' }); }
    catch (_) { try { rec = new MediaRecorder(_stream); } catch (_2) { return; } }
    _recorder = rec; _segStart = Date.now();
    rec.ondataavailable = e => { if (e.data?.size > 0) chunks.push(e.data); };
    rec.onstop = () => _onSegmentReady(chunks, rec.mimeType || 'audio/webm');
    rec.start();
    setTimeout(() => { if (rec.state === 'recording') rec.stop(); }, SEGMENT_MS);
  }
  async function _onSegmentReady(chunks, mimeType) {
    if (!_active) return;
    if (_muted || _processing) { setTimeout(_nextSegment, 100); return; }
    if (Date.now() - _segStart < MIN_AUDIO_MS) { _nextSegment(); return; }
    const blob = new Blob(chunks, { type: mimeType });
    if (blob.size < 1000) { _nextSegment(); return; }
    try {
      const ab = await blob.arrayBuffer();
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const decoded = await ctx.decodeAudioData(ab.slice(0));
      await ctx.close();
      if (_isSilent(decoded.getChannelData(0))) { _nextSegment(); return; }
    } catch (_) {}
    _processing = true; _setUI('processing');
    try {
      const fd = new FormData();
      fd.append('audio', blob, 'audio.webm');
      fd.append('context', JSON.stringify(_ctx()));
      const resp = await fetch(API_TRANSCRIBE, { method: 'POST', body: fd });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (_active) _dispatch(data);
    } catch (err) {
      console.warn('[Voice] API error:', err);
      _processing = false; _setUI('listening');
      if (_active) _nextSegment();
    }
  }

  /* ── Dispatch ── */
  function _dispatch(data) {
    const { action, params = {}, speech, transcript } = data;
    if (_isHallucination(transcript)) {
      _processing = false; if (_active) { _setUI('listening'); _nextSegment(); } return;
    }
    if (transcript && transcript !== '...') _setTranscript(transcript);
    if (typeof appendLog === 'function')
      appendLog({ type: 'voice_command', data: { transcript: transcript || '', action: action || 'noop' } });
    (_ACTIONS[action] || _ACTIONS['noop'])(params);
    if (speech) { _speak(speech, () => { _processing = false; if (_active) { _setUI('listening'); _nextSegment(); } }); }
    else { _processing = false; if (_active) { _setUI('listening'); _nextSegment(); } }
  }

  /* ══════════════════════════════════════════════════════
   * FIX v7 #1 — BROWSE POPUP PRO
   * ══════════════════════════════════════════════════════ */

  const _FIELD_LABELS = {
    'inp-water-rgb':    { icon: '💧', label: 'Eau RGB' },
    'inp-water-micro':  { icon: '🔬', label: 'Eau Microscopique' },
    'inp-manure-rgb':   { icon: '🌿', label: 'Fientes RGB' },
    'inp-manure-micro': { icon: '🔬', label: 'Fientes Microscopique' },
  };

  function _playBrowseSound() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      [[440,0],[554,0.09],[659,0.18],[880,0.27]].forEach(([freq,t]) => {
        const osc = ctx.createOscillator(), gain = ctx.createGain();
        osc.connect(gain); gain.connect(ctx.destination);
        osc.type = 'sine'; osc.frequency.value = freq;
        gain.gain.setValueAtTime(0, ctx.currentTime+t);
        gain.gain.linearRampToValueAtTime(0.13, ctx.currentTime+t+0.04);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime+t+0.28);
        osc.start(ctx.currentTime+t); osc.stop(ctx.currentTime+t+0.3);
      });
    } catch(_) {}
  }

  function _playClickSound() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator(), gain = ctx.createGain();
      osc.connect(gain); gain.connect(ctx.destination);
      osc.type = 'sine'; osc.frequency.value = 1047;
      gain.gain.setValueAtTime(0.18, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime+0.18);
      osc.start(ctx.currentTime); osc.stop(ctx.currentTime+0.2);
    } catch(_) {}
  }

  function _ensureBrowsePopup() {
    if (document.getElementById('_vbp_root')) return;
    const style = document.createElement('style');
    style.textContent = `
      #_vbp_root {
        position:fixed; bottom:130px; left:50%;
        transform:translateX(-50%) translateY(30px) scale(0.88);
        background:var(--bg-surface,#fff);
        border:2px solid var(--accent,#2e7d32);
        border-radius:24px;
        padding:32px 40px 26px;
        box-shadow:0 0 0 8px var(--accent-glow,rgba(46,125,50,0.10)),0 28px 72px rgba(0,0,0,0.16),0 4px 16px rgba(46,125,50,0.18);
        z-index:1500; display:flex; flex-direction:column; align-items:center;
        gap:18px; min-width:360px; max-width:440px;
        opacity:0; pointer-events:none;
        transition:opacity 0.3s ease,transform 0.38s cubic-bezier(0.34,1.56,0.64,1);
        font-family:'Sora',sans-serif; overflow:hidden; text-align:center;
      }
      #_vbp_root.visible { opacity:1; pointer-events:all; transform:translateX(-50%) translateY(0) scale(1); }
      #_vbp_root::before {
        content:''; position:absolute; top:0; left:0; right:0; height:4px;
        background:linear-gradient(90deg,var(--accent,#2e7d32),var(--accent2,#43a047),var(--teal,#00796b),var(--blue,#1565c0),var(--accent,#2e7d32));
        background-size:300%; animation:_vbp_sh 2.5s linear infinite;
        border-radius:24px 24px 0 0;
      }
      #_vbp_root::after {
        content:''; position:absolute; inset:0; pointer-events:none;
        background:radial-gradient(circle at 20% 85%,var(--accent-soft,rgba(46,125,50,0.07)) 0%,transparent 55%),
                   radial-gradient(circle at 80% 15%,rgba(21,101,192,0.05) 0%,transparent 55%);
        border-radius:22px;
      }
      @keyframes _vbp_sh { 0%{background-position:0%}100%{background-position:300%} }
      #_vbp_icon_wrap {
        width:80px; height:80px; border-radius:50%;
        background:var(--accent-soft,rgba(46,125,50,0.09));
        border:2px solid var(--accent-glow,rgba(46,125,50,0.22));
        display:flex; align-items:center; justify-content:center; font-size:34px;
        position:relative; animation:_vbp_br 2.2s ease-in-out infinite; z-index:1;
      }
      #_vbp_icon_wrap::after {
        content:''; position:absolute; inset:-7px; border-radius:50%;
        border:1.5px solid var(--accent-glow,rgba(46,125,50,0.22));
        animation:_vbp_ping 2s ease-out infinite;
      }
      @keyframes _vbp_br { 0%,100%{transform:scale(1)}50%{transform:scale(1.07)} }
      @keyframes _vbp_ping { 0%{transform:scale(1);opacity:0.6}100%{transform:scale(1.65);opacity:0} }
      #_vbp_title {
        font-size:17px; font-weight:700; color:var(--text-1,#1b2e1c);
        line-height:1.3; position:relative; z-index:1;
      }
      #_vbp_sub {
        font-size:12px; color:var(--text-3,#7a9e7c); margin-top:-8px;
        line-height:1.5; position:relative; z-index:1;
      }
      #_vbp_open_btn {
        display:flex; align-items:center; gap:10px; padding:15px 0;
        background:linear-gradient(135deg,var(--accent,#2e7d32),var(--accent2,#43a047));
        color:#fff; border:none; border-radius:14px;
        font-size:14.5px; font-weight:700; font-family:'Sora',sans-serif;
        cursor:pointer; letter-spacing:0.4px;
        box-shadow:0 6px 22px var(--accent-glow,rgba(46,125,50,0.32));
        transition:transform 0.15s ease,box-shadow 0.15s ease;
        position:relative; z-index:1; width:100%; justify-content:center;
      }
      #_vbp_open_btn:hover { transform:translateY(-3px); box-shadow:0 10px 30px var(--accent-glow,rgba(46,125,50,0.42)); }
      #_vbp_open_btn:active { transform:scale(0.96); }
      #_vbp_open_btn .vbp_fi { font-size:20px; }
      #_vbp_tip {
        font-size:10.5px; color:var(--text-3,#7a9e7c);
        background:var(--muted-bg,rgba(46,125,50,0.04));
        border:1px solid var(--border,rgba(60,100,50,0.10));
        border-radius:8px; padding:6px 14px;
        font-family:'JetBrains Mono',monospace; letter-spacing:0.3px;
        position:relative; z-index:1; width:100%;
      }
      #_vbp_cancel {
        font-size:11.5px; color:var(--text-3,#7a9e7c); cursor:pointer;
        background:none; border:none; font-family:'Sora',sans-serif;
        transition:color 0.15s; padding:2px 8px; position:relative; z-index:1;
      }
      #_vbp_cancel:hover { color:var(--red,#c62828); }
    `;
    document.head.appendChild(style);
    const el = document.createElement('div');
    el.id = '_vbp_root';
    el.innerHTML = `
      <div id="_vbp_icon_wrap">📂</div>
      <div id="_vbp_title">Choisir une image</div>
      <div id="_vbp_sub">Appuyez sur le bouton ci-dessous pour ouvrir le sélecteur</div>
      <button id="_vbp_open_btn"><span class="vbp_fi">📁</span> Ouvrir le dossier</button>
      <div id="_vbp_tip">💡 Dites "ferme ça" pour annuler · "clique" pour ouvrir</div>
      <button id="_vbp_cancel">Annuler</button>
    `;
    document.body.appendChild(el);
    document.getElementById('_vbp_cancel').onclick = _hideBrowsePopup;
  }

  let _currentBrowseFieldId = null, _browseTimeout = null;

  function _showBrowsePopup(fieldId) {
    _ensureBrowsePopup();
    _currentBrowseFieldId = fieldId;
    const info = _FIELD_LABELS[fieldId] || { icon: '📁', label: 'Image' };
    document.getElementById('_vbp_icon_wrap').textContent = info.icon;
    document.getElementById('_vbp_title').innerHTML =
      `Choisir : <span style="color:var(--accent,#2e7d32)">${info.label}</span>`;

    // Re-wire button
    const oldBtn = document.getElementById('_vbp_open_btn');
    const newBtn = oldBtn.cloneNode(true);
    newBtn.innerHTML = `<span class="vbp_fi">📁</span> Ouvrir le dossier`;
    oldBtn.parentNode.replaceChild(newBtn, oldBtn);
    document.getElementById('_vbp_cancel').onclick = _hideBrowsePopup;

    newBtn.onclick = function () {
      _playClickSound();
      _hideBrowsePopup();
      _scanForFileInputs();
      const fi = _fileInputCache[fieldId]
              || document.getElementById(`_fi_${fieldId}`)
              || document.getElementById(fieldId)?._browseInput;
      if (fi) { fi.click(); return; }
      const field = document.getElementById(fieldId);
      const foldBtn = field?.closest('div')?.querySelector('button');
      if (foldBtn) { foldBtn.click(); return; }
      const tmp = document.createElement('input');
      tmp.type = 'file'; tmp.accept = 'image/*';
      tmp.style.cssText = 'position:absolute;opacity:0;pointer-events:none;top:-9999px;';
      document.body.appendChild(tmp);
      tmp.onchange = () => {
        const f = tmp.files[0], el = document.getElementById(fieldId);
        if (f && el) {
          el._selectedFile = f; el.value = f.name; el.classList.add('voice-filled');
          setTimeout(() => el.classList.remove('voice-filled'), 2000);
          if (typeof showToast === 'function') showToast(`✅ Image : ${f.name}`, 'success', 3000);
        }
        try { document.body.removeChild(tmp); } catch(_) {}
      };
      tmp.click();
    };

    document.getElementById('_vbp_root').classList.add('visible');
    _playBrowseSound();
    clearTimeout(_browseTimeout);
    _browseTimeout = setTimeout(_hideBrowsePopup, 20000);
  }

  function _hideBrowsePopup() {
    clearTimeout(_browseTimeout);
    document.getElementById('_vbp_root')?.classList.remove('visible');
    _currentBrowseFieldId = null;
  }

  /* Exposé pour que le LLM puisse "cliquer" via voiceHint('clique') */
  window.triggerVoiceBrowseOpen = function () {
    const popup = document.getElementById('_vbp_root');
    if (popup?.classList.contains('visible')) {
      const btn = document.getElementById('_vbp_open_btn');
      if (btn) { btn.click(); return true; }
    }
    return false;
  };

  /* ── Close modal ── */
  function _closeAnyModal() {
    const bp = document.getElementById('_vbp_root');
    if (bp?.classList.contains('visible')) { _hideBrowsePopup(); return true; }
    const sp = document.getElementById('popup-overlay');
    if (sp?.classList.contains('visible')) { sp.classList.remove('visible'); return true; }
    const ao = document.getElementById('analyze-overlay');
    if (ao?.classList.contains('visible')) {
      if (typeof hideAnalyzeOverlay === 'function') hideAnalyzeOverlay();
      else ao.classList.remove('visible');
      return true;
    }
    return false;
  }

  function _triggerBrowse(fieldId) {
    _scanForFileInputs(); _showBrowsePopup(fieldId); return true;
  }

  /* ══════════════════════════════════════════════════════
   * FIX v7 #2 — RAPPORT PRO HTML
   * ══════════════════════════════════════════════════════ */

  function _escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  const _SECTION_ICONS = {
    'eau':'💧','water':'💧','fiente':'🌿','manure':'🌿',
    'risque':'⚠️','risk':'⚠️','microb':'🦠','pathog':'🦠','bact':'🦠',
    'valori':'💰','roi':'📈','retour':'📈','invest':'📈',
    'environ':'🌍','impact':'🌍','norme':'📋','nt 106':'📋',
    'recommand':'✅','action':'✅','conclusion':'🎯',
    'résumé':'📄','summary':'📄','rapport':'📄',
    'saison':'🌾','season':'🌾','marché':'🛒','market':'🛒',
    'qualité':'🔬','quality':'🔬','analyse':'🔬',
    'mémoire':'💾','memory':'💾','introduction':'📌',
  };

  function _getSectionIcon(title) {
    const t = title.toLowerCase();
    for (const [k,v] of Object.entries(_SECTION_ICONS)) if (t.includes(k)) return v;
    return '📌';
  }

  function _isHeader(line) {
    const t = line.trim();
    if (!t) return false;
    return /^(={2,}|#{1,3}|-{3,})\s*.+/.test(t)
        || /^[IVX\d]+[\.\)]\s+[A-Za-zÀ-ÿ]/.test(t)
        || (t === t.toUpperCase() && t.replace(/[^A-ZÀ-Ÿ]/g,'').length > 3)
        || /^[💧🌿⚠️🦠💰📈🌍✅📄🌾🛒🔬💾📌🎯📋]/.test(t);
  }

  function _cleanTitle(line) {
    return line.replace(/^[=\-#*\s💧🌿⚠️🦠💰📈🌍✅📄🌾🛒🔬💾📌🎯📋]+/,'').replace(/[=\-#*\s]+$/,'').trim();
  }

  function _ensureReportStyle() {
    if (document.getElementById('_em_report_css')) return;
    const s = document.createElement('style');
    s.id = '_em_report_css';
    s.textContent = `
      #report-content { white-space:normal !important; padding:0 !important; }
      .em-report { font-family:'Sora',sans-serif; color:var(--text-1,#1b2e1c); }
      .em-rh {
        display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px;
        padding:22px 28px 18px;
        background:linear-gradient(135deg,var(--accent-soft,rgba(46,125,50,0.07)) 0%,transparent 100%);
        border-bottom:2px solid var(--accent-glow,rgba(46,125,50,0.20));
        border-radius:var(--radius,14px) var(--radius,14px) 0 0;
      }
      .em-rh-logo { display:flex; align-items:center; gap:12px; }
      .em-rh-emoji { font-size:38px; filter:drop-shadow(0 2px 6px rgba(46,125,50,0.22)); }
      .em-rh-brand-name { font-family:'Cormorant Garamond',serif; font-size:21px; font-weight:600; color:var(--text-1,#1b2e1c); letter-spacing:0.4px; }
      .em-rh-brand-sub { font-size:9px; font-family:'JetBrains Mono',monospace; color:var(--text-3,#7a9e7c); text-transform:uppercase; letter-spacing:1.2px; }
      .em-rh-chips { display:flex; gap:6px; flex-wrap:wrap; }
      .em-chip { font-size:9.5px; font-family:'JetBrains Mono',monospace; font-weight:600; padding:3px 10px; border-radius:99px; border:1px solid var(--accent-glow,rgba(46,125,50,0.22)); background:var(--accent-soft,rgba(46,125,50,0.07)); color:var(--accent,#2e7d32); letter-spacing:0.5px; }
      .em-rbody { padding:20px 24px 24px; display:flex; flex-direction:column; gap:14px; }
      .em-sec {
        border-radius:10px; overflow:hidden;
        border:1px solid var(--border,rgba(60,100,50,0.10));
        background:var(--bg-elevated,#f8fbf5);
        transition:box-shadow 0.2s, transform 0.2s;
      }
      .em-sec:hover { box-shadow:0 6px 20px var(--accent-glow,rgba(46,125,50,0.12)); transform:translateY(-1px); }
      .em-sec-hd {
        display:flex; align-items:center; gap:10px; padding:11px 18px;
        background:var(--bg-surface,#fff); border-bottom:1px solid var(--border,rgba(60,100,50,0.10));
      }
      .em-sec-num {
        width:22px; height:22px; border-radius:50%;
        background:var(--accent,#2e7d32); color:#fff;
        font-size:10px; font-weight:700; font-family:'JetBrains Mono',monospace;
        display:flex; align-items:center; justify-content:center; flex-shrink:0;
      }
      .em-sec-ico { font-size:15px; flex-shrink:0; }
      .em-sec-title { font-size:13px; font-weight:700; color:var(--text-1,#1b2e1c); flex:1; }
      .em-sec-body { padding:14px 18px; font-size:12.5px; color:var(--text-2,#3d5c3e); line-height:1.85; }
      .em-line { display:block; margin-bottom:3px; }
      .em-bullet { display:flex; gap:8px; align-items:flex-start; margin-bottom:5px; }
      .em-bullet::before { content:'▸'; color:var(--accent,#2e7d32); font-size:10px; margin-top:4px; flex-shrink:0; }
      .em-hl { display:block; background:linear-gradient(90deg,var(--accent-soft,rgba(46,125,50,0.07)),transparent); border-left:3px solid var(--accent,#2e7d32); padding:4px 10px; margin:4px 0; border-radius:0 6px 6px 0; font-family:'JetBrains Mono',monospace; font-size:11.5px; }
      .em-rf { padding:12px 28px; border-top:1px solid var(--border,rgba(60,100,50,0.10)); display:flex; justify-content:space-between; align-items:center; font-size:9.5px; font-family:'JetBrains Mono',monospace; color:var(--text-3,#7a9e7c); flex-wrap:wrap; gap:6px; }
      @media print { .em-sec { break-inside:avoid; } .em-rh { background:#f0f4ed!important; } }
    `;
    document.head.appendChild(s);
  }

  function _renderReport(rawText) {
    if (!rawText || rawText.length < 20) return;
    _ensureReportStyle();

    const now = new Date().toLocaleString('fr-FR', { day:'2-digit', month:'long', year:'numeric', hour:'2-digit', minute:'2-digit' });
    const lines = rawText.split('\n');
    const sections = [];
    let cur = null;

    for (const line of lines) {
      if (_isHeader(line)) {
        if (cur) sections.push(cur);
        const title = _cleanTitle(line);
        cur = { title, icon: _getSectionIcon(title), lines: [] };
      } else {
        if (!cur) cur = { title: 'Analyse', icon: '📄', lines: [] };
        cur.lines.push(line);
      }
    }
    if (cur) sections.push(cur);

    let sectionsHTML = '';
    sections.forEach((sec, idx) => {
      const body = sec.lines.join('\n').trim();
      const formattedLines = (body ? body.split('\n') : []).map(l => {
        const t = l.trim();
        if (!t) return '<br>';
        if (/\d[\d\s,\.]*\s*(TND|DT|%|mg\/l|kg|UFC|ml|ppm|°C|mS\/cm)/i.test(t))
          return `<span class="em-hl">${_escHtml(t)}</span>`;
        if (/^[-•*]\s/.test(t))
          return `<div class="em-bullet">${_escHtml(t.replace(/^[-•*]\s*/,''))}</div>`;
        return `<span class="em-line">${_escHtml(t)}</span>`;
      }).join('');

      sectionsHTML += `
        <div class="em-sec">
          <div class="em-sec-hd">
            <div class="em-sec-num">${idx+1}</div>
            <span class="em-sec-ico">${sec.icon}</span>
            <div class="em-sec-title">${_escHtml(sec.title)}</div>
          </div>
          ${body ? `<div class="em-sec-body">${formattedLines}</div>` : ''}
        </div>`;
    });

    const html = `<div class="em-report">
      <div class="em-rh">
        <div class="em-rh-logo">
          <div class="em-rh-emoji">🌾</div>
          <div><div class="em-rh-brand-name">El Mazraa</div><div class="em-rh-brand-sub">Waste Intelligence · A2A Platform · Tunisie</div></div>
        </div>
        <div class="em-rh-chips">
          <span class="em-chip">📄 Rapport d'analyse</span>
          <span class="em-chip">🗓 ${now}</span>
          <span class="em-chip">🔒 Confidentiel</span>
        </div>
      </div>
      <div class="em-rbody">${sectionsHTML}</div>
      <div class="em-rf">
        <span>🌿 El Mazraa Agro-AI Platform</span>
        <span>Généré automatiquement · ${now}</span>
        <span>Confidentiel · Usage interne uniquement</span>
      </div>
    </div>`;

    const el = document.getElementById('report-content');
    if (!el) return;
    el.innerHTML = html;
  }

  /* Observer report-content */
  function _initReportObserver() {
    const el = document.getElementById('report-content');
    if (!el) { setTimeout(_initReportObserver, 600); return; }

    // Patch textContent setter
    const desc = Object.getOwnPropertyDescriptor(Node.prototype, 'textContent');
    Object.defineProperty(el, 'textContent', {
      set(v) {
        desc.set.call(this, v);
        if (v && v.trim().length > 30) setTimeout(() => _renderReport(v), 90);
      },
      get() { return desc.get.call(this); },
      configurable: true,
    });

    // Aussi via MutationObserver (dashboard_patch utilise textContent direct)
    let _pendingRender = false;
    new MutationObserver(() => {
      if (_pendingRender) return;
      _pendingRender = true;
      setTimeout(() => {
        _pendingRender = false;
        const text = el.textContent?.trim();
        if (text && text.length > 30 && !el.querySelector('.em-report')) _renderReport(text);
      }, 150);
    }).observe(el, { characterData: true, childList: true, subtree: true });
  }

  /* ── Scroll ── */
  function _scroll(dir) {
    const amount = dir === 'up' ? -350 : 350;
    for (const el of [document.querySelector('.tab-content.active'), document.getElementById('report-content'), document.getElementById('event-log'), document.querySelector('.pipeline-section'), document.documentElement, document.body]) {
      if (el && el.scrollHeight > el.clientHeight + 5) { el.scrollBy({ top: amount, behavior: 'smooth' }); return; }
    }
    window.scrollBy({ top: amount, behavior: 'smooth' });
  }

  /* ── Fill ── */
  function _fill(id, value) {
    if (value == null) return;
    const el = document.getElementById(id);
    if (!el) return;
    const s = String(value); el.value = s;
    el.classList.add('voice-filled'); setTimeout(() => el.classList.remove('voice-filled'), 2000);
    el.dispatchEvent(new Event('input',{bubbles:true}));
    el.dispatchEvent(new Event('change',{bubbles:true}));
    el.dispatchEvent(new KeyboardEvent('keyup',{bubbles:true}));
    el.focus(); el.setSelectionRange?.(s.length, s.length);
  }

  /* ── Actions ── */
  const _ACTIONS = {
    tab_report: () => typeof switchTab==='function' && switchTab('report'),
    tab_stats:  () => typeof switchTab==='function' && switchTab('stats'),
    tab_log:    () => typeof switchTab==='function' && switchTab('log'),
    launch_analysis: () => { const c=_ctx(); if(!c.analysis_running && c.any_image_filled && typeof launchAnalysis==='function') launchAnalysis(); },
    copy_report:  () => typeof copyReport==='function' && copyReport(),
    print_report: () => typeof printReport==='function' && printReport(),
    clear_fields: (p) => {
      const map = { question:'inp-request', water:'inp-water-rgb', manure:'inp-manure-rgb' };
      const fields = p?.field ? [map[p.field]||p.field] : ['inp-water-rgb','inp-water-micro','inp-manure-rgb','inp-manure-micro','inp-request'];
      fields.forEach(id => { const el=document.getElementById(id); if(el){el.value='';el._selectedFile=null;el.classList.remove('voice-filled');} });
    },
    scroll_up: () => _scroll('up'),
    scroll_down: () => _scroll('down'),
    theme_dark:  () => { if(document.documentElement.getAttribute('data-theme')!=='dark' && typeof toggleTheme==='function') toggleTheme(); },
    theme_light: () => { if(document.documentElement.getAttribute('data-theme')!=='light' && typeof toggleTheme==='function') toggleTheme(); },
    stop_voice: () => window.stopVoiceMode?.(),
    close_modal: () => { if(!_closeAnyModal() && typeof showToast==='function') showToast('Aucune fenêtre ouverte.','info',1500); },
    /* ── FIX v7: cliquer le bouton ouvrir du browse popup ── */
    click_browse_btn: () => { const ok=window.triggerVoiceBrowseOpen?.(); if(!ok && typeof showToast==='function') showToast('Aucun sélecteur ouvert.','info',1500); },
    browse_water_rgb:    () => _triggerBrowse('inp-water-rgb'),
    browse_water_micro:  () => _triggerBrowse('inp-water-micro'),
    browse_manure_rgb:   () => _triggerBrowse('inp-manure-rgb'),
    browse_manure_micro: () => _triggerBrowse('inp-manure-micro'),
    fill_water_rgb:    p => _fill('inp-water-rgb',   p?.value??''),
    fill_water_micro:  p => _fill('inp-water-micro', p?.value??''),
    fill_manure_rgb:   p => _fill('inp-manure-rgb',  p?.value??''),
    fill_manure_micro: p => _fill('inp-manure-micro',p?.value??''),
    fill_question:     p => _fill('inp-request',     p?.value??''),
    noop: ()=>{}, unknown: ()=>{},
  };

  /* ── UI helpers ── */
  function _setUI(state) {
    const btn=document.getElementById('voice-btn'), overlay=document.getElementById('voice-overlay');
    const ind=document.getElementById('voice-indicator'), title=document.getElementById('voice-overlay-title');
    if (!btn) return;
    btn.className='voice-btn'; overlay?.classList.remove('listening');
    if (state==='listening') { btn.classList.add('listening'); overlay?.classList.add('visible','listening'); if(ind) ind.className='voice-indicator'; if(title) title.textContent='En écoute…'; }
    else if (state==='processing') { btn.classList.add('processing'); overlay?.classList.add('visible'); if(ind) ind.className='voice-indicator processing'; if(title) title.textContent='Traitement…'; }
    else if (state==='speaking') { overlay?.classList.add('visible'); if(ind) ind.className='voice-indicator processing'; if(title) title.textContent='Je parle…'; }
    else { overlay?.classList.remove('visible'); }
  }
  function _setTranscript(text) {
    const el=document.getElementById('voice-transcript'), echo=document.getElementById('voice-command-echo');
    if(el) el.textContent=`"${text}"`;
    if(echo){echo.textContent=text;echo.classList.add('visible');}
  }
  function _beep(freq) {
    try {
      const ctx=new(window.AudioContext||window.webkitAudioContext)();
      const osc=ctx.createOscillator(),gain=ctx.createGain();
      osc.connect(gain);gain.connect(ctx.destination);
      osc.type='sine';osc.frequency.value=freq;
      gain.gain.setValueAtTime(0.14,ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001,ctx.currentTime+0.2);
      osc.start(ctx.currentTime);osc.stop(ctx.currentTime+0.2);
    } catch(_){}
  }

  /* ── voiceHint ── */
  window.voiceHint = function(text) {
    if (!_active) { if(typeof showToast==='function') showToast('Activez d\'abord le micro 🎤','info',2000); return; }
    fetch('/voice/transcribe_text',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,context:_ctx()})})
      .then(r=>r.json()).then(d=>_dispatch(d)).catch(console.warn);
  };

  /* ── Public API ── */
  window._voiceActive = false;
  window.speak = t => _speak(t, null);
  window.toggleVoiceMode = () => _active ? window.stopVoiceMode() : window.startVoiceMode();

  window.startVoiceMode = async () => {
    if (_active) return;
    try { _stream = await navigator.mediaDevices.getUserMedia({ audio:true, video:false }); }
    catch(_) { if(typeof showToast==='function') showToast('Accès micro refusé.','error',5000); return; }
    _active = window._voiceActive = true; _muted = _processing = false;
    _beep(880); _setUI('listening');
    _speak('Mode vocal activé. Je vous écoute.', () => { _processing=false; if(_active) _nextSegment(); });
  };

  window.stopVoiceMode = () => {
    if (!_active) return;
    _active = window._voiceActive = false; _muted = false;
    _beep(440); _synth?.cancel(); _hideBrowsePopup();
    if(_recorder?.state==='recording') try{_recorder.stop();}catch(_){}
    _stream?.getTracks().forEach(t=>t.stop()); _stream = null; _setUI('idle');
    if(typeof showToast==='function') showToast('Mode vocal désactivé.','info',2000);
  };

  document.addEventListener('keydown', e => {
    const inInput = ['INPUT','TEXTAREA'].includes(document.activeElement?.tagName);
    if (e.code==='Space' && !inInput && !e.repeat) { e.preventDefault(); window.toggleVoiceMode(); }
    if (e.key==='Escape') { if(!_closeAnyModal() && _active) window.stopVoiceMode(); }
  });

  if (document.readyState==='loading') {
    document.addEventListener('DOMContentLoaded', () => { _initBrowseObserver(); _initReportObserver(); });
  } else { _initBrowseObserver(); _initReportObserver(); }

  setTimeout(_scanForFileInputs, 500);
  setTimeout(_scanForFileInputs, 1500);
  setTimeout(_scanForFileInputs, 3000);

  if (_synth) _synth.onvoiceschanged = () => _synth.getVoices();

  console.log('[Voice] El Mazraa v7 — browse popup pro + report HTML renderer');
})();