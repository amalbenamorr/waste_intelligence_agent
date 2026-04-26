/**
 * dashboard_patch.js — El Mazraa v5 — FULL FIX
 *
 * FIXES v5:
 *  1. Browse buttons: file inputs créés immédiatement ET avec retry robuste
 *  2. launchAnalysis() patché: upload via FormData + SSE correct
 *     ─ currentSession est stocké DANS la réponse fetch (plus de race condition)
 *  3. fetchAndShowResult v5: remplace la version du HTML, affiche rapport ET stats
 *  4. handleEvent v5: session_done → fetchAndShowResult avec bon session_id
 *  5. Son + popup de succès déclenchés correctement
 *  6. switchTab expose correctement les deux layouts
 */

(function () {
  'use strict';

  /* ── Champs image à browsifier ─────────────────────────── */
  const FIELDS = [
    { inputId: 'inp-water-rgb',    label: 'Eau RGB' },
    { inputId: 'inp-water-micro',  label: 'Eau Micro' },
    { inputId: 'inp-manure-rgb',   label: 'Fientes RGB' },
    { inputId: 'inp-manure-micro', label: 'Fientes Micro' },
  ];

  /* ── Ajoute bouton 📁 + file input caché ──────────────── */
  function _addBrowse(cfg) {
    const input = document.getElementById(cfg.inputId);
    if (!input || input.dataset.browsePatched) return;
    input.dataset.browsePatched = '1';

    const wrap = document.createElement('div');
    wrap.style.cssText = 'display:flex;gap:5px;align-items:center;';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    input.style.flex = '1';
    input.style.minWidth = '0';

    // Hidden file input — ID = _fi_{inputId} pour voice_module
    const fi = document.createElement('input');
    fi.type   = 'file';
    fi.accept = 'image/*';
    fi.id     = `_fi_${cfg.inputId}`;
    fi.style.cssText = 'position:absolute;opacity:0;pointer-events:none;width:1px;height:1px;top:-9999px;';
    document.body.appendChild(fi);

    // Stocker ref sur le champ (fallback pour voice)
    input._browseInput = fi;

    fi.addEventListener('change', () => {
      const f = fi.files[0];
      if (!f) return;
      input._selectedFile = f;
      input.value = f.name;
      input.classList.add('voice-filled');
      setTimeout(() => input.classList.remove('voice-filled'), 2000);
      if (typeof showToast === 'function')
        showToast(`✅ ${cfg.label} : ${f.name}`, 'success', 3000);
      fi.value = '';
    });

    // Bouton visible
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.title = `Choisir ${cfg.label}`;
    btn.textContent = '📁';
    btn.style.cssText = [
      'flex-shrink:0','width:30px','height:30px','border-radius:6px',
      'border:1px solid var(--border-med,#333)','background:var(--bg-elevated,#1a1a1a)',
      'cursor:pointer','font-size:14px','display:flex','align-items:center',
      'justify-content:center','transition:all 0.2s','line-height:1',
    ].join(';');

    btn.onmouseenter = () => { btn.style.borderColor='var(--accent,#4caf50)'; btn.style.background='var(--accent-soft,rgba(76,175,80,.1))'; };
    btn.onmouseleave = () => { btn.style.borderColor='var(--border-med,#333)'; btn.style.background='var(--bg-elevated,#1a1a1a)'; };
    btn.onclick = () => fi.click();
    wrap.appendChild(btn);
  }

  function _initBrowse() {
    FIELDS.forEach(_addBrowse);
  }

  /* ── Patch launchAnalysis pour gérer File objects ──────── */
  function _patchLaunch() {
    const orig = window.launchAnalysis;
    if (!orig) return false;

    window.launchAnalysis = async function () {
      const inputs = FIELDS.map(f => document.getElementById(f.inputId));
      const hasFile = inputs.some(el => el?._selectedFile instanceof File);

      // Si aucun File object mais texte dans les champs → utiliser l'ancienne méthode
      if (!hasFile) return orig.call(this);

      const anyFilled = inputs.some(el => el?._selectedFile instanceof File || el?.value?.trim());
      if (!anyFilled) {
        if (typeof showToast === 'function') showToast('Veuillez fournir au moins une image.', 'error');
        return;
      }

      const btn = document.getElementById('btn-launch');
      if (btn?.disabled) return;

      // Disable button
      if (btn) {
        btn.disabled = true;
        const sp = document.getElementById('btn-spinner');
        const ic = document.getElementById('btn-icon');
        const tx = document.getElementById('btn-text');
        if (sp) sp.style.display = 'block';
        if (ic) ic.style.display = 'none';
        if (tx) tx.textContent   = 'Analyse en cours…';
      }

      _resetReportArea();

      if (window.eventSource) { window.eventSource.close(); window.eventSource = null; }
      if (typeof window.logCount !== 'undefined') {
        window.logCount = 0;
        const badge = document.getElementById('log-count');
        if (badge) badge.style.display = 'none';
      }
      const logEl = document.getElementById('event-log');
      if (logEl) logEl.innerHTML = '';

      // Build FormData
      const fd = new FormData();
      const fieldKeys = ['water_rgb','water_micro','manure_rgb','manure_micro'];
      inputs.forEach((el, i) => {
        if (el?._selectedFile instanceof File)
          fd.append(fieldKeys[i], el._selectedFile, el._selectedFile.name);
        else if (el?.value?.trim())
          fd.append(fieldKeys[i] + '_path', el.value.trim());
      });
      const req = document.getElementById('inp-request')?.value?.trim() || '';
      if (req) fd.append('user_request', req);

      if (typeof showAnalyzeOverlay === 'function') showAnalyzeOverlay();
      if (typeof resetPipeline === 'function') resetPipeline();

      try {
        const resp = await fetch('/analyze_files', { method: 'POST', body: fd });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();

        // ── FIX v5: stocker session_id ici, pas depuis l'event SSE ──
        window.currentSession = data.session_id;
        console.log('[Patch v5] Session started:', data.session_id);

        const es = new EventSource(data.events_url);
        window.eventSource = es;
        es.onmessage = e => {
          try {
            const event = JSON.parse(e.data);
            if (typeof handleEvent === 'function') handleEvent(event);
          } catch (err) { console.error('[Patch] parse:', err); }
        };
        es.onerror = () => {
          es.close();
          if (typeof hideAnalyzeOverlay === 'function') hideAnalyzeOverlay();
          _enableBtnFallback();
        };
      } catch (e) {
        if (typeof showToast === 'function') showToast('Erreur: ' + e.message, 'error');
        if (typeof hideAnalyzeOverlay === 'function') hideAnalyzeOverlay();
        _enableBtnFallback();
      }
    };
    return true;
  }

  /* ── Patch fetchAndShowResult — v5 FIXED ────────────────── */
  function _patchFetchResult() {
    // Remplacer la fonction dans window pour que handleEvent l'utilise
    window.fetchAndShowResult = async function (sessionId, sessionData) {
      const sid = sessionId || window.currentSession;
      if (!sid) {
        console.warn('[Patch v5] fetchAndShowResult: no session_id');
        _enableBtnFallback();
        return;
      }

      console.log('[Patch v5] Fetching result for session:', sid);

      for (let i = 0; i < 10; i++) {
        await new Promise(r => setTimeout(r, 1500));
        try {
          const resp = await fetch(`/result/${sid}`);
          if (resp.status === 202) {
            console.log(`[Patch v5] Result not ready yet (attempt ${i+1})`);
            continue;
          }
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          const data = await resp.json();

          console.log('[Patch v5] Result received:', Object.keys(data));

          _showReport(data);
          _showStats(data);

          // Success popup + sound
          if (typeof showSuccessPopup === 'function') showSuccessPopup(data);
          else _fallbackSuccessPopup(data);

          if (typeof enableBtn === 'function') enableBtn();
          else _enableBtnFallback();

          if (window._voiceActive && typeof window.speak === 'function')
            window.speak('Analyse terminée. Le rapport est disponible.');

          return;
        } catch (e) {
          console.error('[Patch v5] fetchResult error:', e);
        }
      }

      console.warn('[Patch v5] Max retries reached for session:', sid);
      if (typeof enableBtn === 'function') enableBtn();
      else _enableBtnFallback();
    };
    return true;
  }

  /* ── Affichage rapport — v5 FIXED ────────────────────── */
  function _showReport(data) {
    const report = data.report_full || data.report_preview || '';
    if (!report || report.length < 10) {
      console.warn('[Patch v5] Empty report data');
      return;
    }

    console.log('[Patch v5] Showing report, length:', report.length);

    // Layout nouveau (report-wrapper / report-content <pre>)
    const wrapper = document.getElementById('report-wrapper');
    const emptyR  = document.getElementById('empty-report');
    const content = document.getElementById('report-content');
    const metaEl  = document.getElementById('report-meta');

    if (content) {
      content.textContent = report;
      content.style.display = '';
    }
    if (wrapper) {
      wrapper.style.display = 'flex';
      wrapper.classList.add('fade-in');
    }
    if (emptyR) emptyR.style.display = 'none';
    if (metaEl) {
      const dt = new Date().toLocaleString('fr-FR');
      metaEl.textContent = `Généré le ${dt} · ${data.waste_type || '?'} · ${data.total_time_s || '?'}s`;
    }

    // Auto-switch vers l'onglet rapport
    if (typeof switchTab === 'function') {
      switchTab('report');
    } else {
      // Fallback manuel
      document.querySelectorAll('.tab').forEach((t, i) => t.classList.toggle('active', i === 0));
      document.querySelectorAll('.tab-content').forEach((c, i) => c.classList.toggle('active', i === 0));
    }
  }

  /* ── Affichage stats — v5 FIXED ─────────────────────── */
  function _showStats(data) {
    console.log('[Patch v5] Showing stats');

    const set = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val;
    };

    set('stat-total-time',    data.total_time_s    ?? '--');
    set('stat-parallel-time', data.parallel_time_s ?? '--');
    set('stat-waste-type',    data.waste_type       ?? '--');
    set('stat-conf-water',    data.water_confidence  != null ? Number(data.water_confidence).toFixed(2)  : '--');
    set('stat-conf-manure',   data.manure_confidence != null ? Number(data.manure_confidence).toFixed(2) : '--');
    set('stat-memory',        data.memory_saved ? 'Oui ✓' : 'Non');

    const emptyStats  = document.getElementById('empty-stats');
    const statsContent = document.getElementById('stats-content');
    if (emptyStats)   emptyStats.style.display   = 'none';
    if (statsContent) {
      statsContent.style.display = 'block';
      statsContent.classList.add('fade-in');
    }

    const tbody = document.getElementById('agents-tbody');
    if (tbody) {
      tbody.innerHTML = '';
      (data.agents_called || []).forEach(a => {
        const status = a.status || 'unknown';
        const time   = a.time_s ? Number(a.time_s).toFixed(2) + 's' : '--';
        tbody.innerHTML += `<tr>
          <td>${a.agent_name || a.agent_id || '?'}</td>
          <td><span class="badge ${status === 'success' ? 'success' : status}">${status}</span></td>
          <td>${time}</td>
        </tr>`;
      });
    }
  }

  /* ── Popup de succès fallback ────────────────────────── */
  function _fallbackSuccessPopup(data) {
    _playSuccessSound();
    if (typeof showToast === 'function')
      showToast(`✅ Analyse terminée en ${data.total_time_s || '?'}s`, 'success', 5000);
  }

  function _playSuccessSound() {
    try {
      const ctx   = new (window.AudioContext || window.webkitAudioContext)();
      const notes = [523.25, 659.25, 783.99, 1046.5];
      notes.forEach((freq, i) => {
        const osc  = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain); gain.connect(ctx.destination);
        osc.type = 'sine'; osc.frequency.value = freq;
        gain.gain.setValueAtTime(0, ctx.currentTime + i*0.12);
        gain.gain.linearRampToValueAtTime(0.18, ctx.currentTime + i*0.12 + 0.06);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + i*0.12 + 0.42);
        osc.start(ctx.currentTime + i*0.12);
        osc.stop(ctx.currentTime + i*0.12 + 0.45);
      });
    } catch (_) {}
  }

  /* ── Reset UI avant nouvelle analyse ─────────────────── */
  function _resetReportArea() {
    const wrapper = document.getElementById('report-wrapper');
    const emptyR  = document.getElementById('empty-report');
    const content = document.getElementById('report-content');
    const emptyS  = document.getElementById('empty-stats');
    const stats   = document.getElementById('stats-content');
    if (wrapper)  wrapper.style.display  = 'none';
    if (emptyR)   emptyR.style.display   = 'flex';
    if (content)  { content.textContent = ''; }
    if (emptyS)   emptyS.style.display   = 'flex';
    if (stats)    stats.style.display    = 'none';
    const log = document.getElementById('event-log');
    if (log) log.innerHTML = '';
  }

  function _enableBtnFallback() {
    const btn = document.getElementById('btn-launch');
    if (!btn) return;
    btn.disabled = false;
    const sp = document.getElementById('btn-spinner');
    const ic = document.getElementById('btn-icon');
    const tx = document.getElementById('btn-text');
    if (sp) sp.style.display = 'none';
    if (ic) ic.style.display = 'inline';
    if (tx) tx.textContent   = "Lancer l'analyse A2A";
    if (!sp && !tx) btn.textContent = 'Launch A2A Analysis';
  }

  /* ── Patch handleEvent pour intercepter session_done ──── */
  function _patchHandleEvent() {
    const orig = window.handleEvent;
    if (!orig) return false;

    window.handleEvent = function(event) {
      // Laisser l'original gérer tout...
      orig.call(this, event);

      // ... puis intercepter session_done pour s'assurer que
      // fetchAndShowResult est appelé avec le bon session_id
      if (event && event.type === 'session_done') {
        const sid = window.currentSession;
        console.log('[Patch v5] session_done intercepted, session:', sid);
        // La version originale dans dashboard.html appelle déjà fetchAndShowResult
        // mais avec l'ancienne implémentation. Notre patch a déjà remplacé
        // window.fetchAndShowResult, donc ça devrait fonctionner.
        // Si ce n'est pas le cas, on force:
        setTimeout(() => {
          if (sid && typeof window.fetchAndShowResult === 'function') {
            // Vérifier si le rapport est déjà affiché
            const reportEl = document.getElementById('report-content');
            if (!reportEl?.textContent?.trim()) {
              console.log('[Patch v5] Forcing fetchAndShowResult');
              window.fetchAndShowResult(sid, event.data || {});
            }
          }
        }, 2000);
      }

      // Intercepter aussi "done" (certaines versions le renvoient)
      if (event && event.type === 'done') {
        const sid = window.currentSession;
        if (sid) {
          setTimeout(() => {
            const reportEl = document.getElementById('report-content');
            if (!reportEl?.textContent?.trim()) {
              window.fetchAndShowResult(sid, {});
            }
          }, 1000);
        }
      }
    };
    return true;
  }

  /* ── Init ─────────────────────────────────────────────── */
  function _init() {
    _initBrowse();

    let retries = 0;
    let launchPatched = false;
    let fetchPatched  = false;
    let eventPatched  = false;

    const tryPatch = () => {
      if (!launchPatched) launchPatched = _patchLaunch();
      if (!fetchPatched)  fetchPatched  = _patchFetchResult();
      if (!eventPatched)  eventPatched  = _patchHandleEvent();

      if (!launchPatched || !fetchPatched || !eventPatched) {
        if (retries++ < 50) setTimeout(tryPatch, 200);
        else console.warn('[Patch v5] Could not patch all functions after 50 retries');
      } else {
        console.log('[Patch v5] All functions patched successfully');
      }
    };
    tryPatch();

    if (typeof window.playSuccessSound === 'undefined') {
      window.playSuccessSound = _playSuccessSound;
    }
  }

  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', _init);
  else
    _init();

  console.log('[Patch] dashboard_patch.js v5 loaded — report+stats fix + browse fix');
})();