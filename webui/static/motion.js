/* JobScout webui — motion system (anime.js v4, ESM via CDN).
 *
 * Principes :
 *   - animation FONCTIONNELLE only : entrée de contenu, feedback d'action,
 *     attention sur le delta. Durées courtes (150–350 ms).
 *   - prefers-reduced-motion → tout est désactivé (le CSS garde tout visible).
 *   - le typing du header ne joue qu'une fois par session (sessionStorage).
 *   - échec de chargement CDN → fallback silencieux, l'UI reste utilisable.
 */

const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;

let anime = null;
if (!REDUCED) {
  try {
    anime = await import('https://cdn.jsdelivr.net/npm/animejs@4.0.2/+esm');
    // Par défaut v4 fige le moteur quand l'onglet est hidden — un onglet ouvert
    // en arrière-plan resterait à mi-animation. Nos animations sont courtes et
    // one-shot : on les laisse tourner, coût nul en continu.
    if (anime.engine) anime.engine.pauseOnDocumentHidden = false;
  } catch (e) {
    console.warn('anime.js indisponible — animations désactivées', e);
  }
}

/* Le CSS ne cache jamais rien (progressive enhancement) : si anime.js
   manque, le contenu est simplement statique — aucun fallback à appliquer. */

/* ── entrée d'une liste : stagger court ─────────────────────────────── */
function staggerIn(scope) {
  const items = scope.querySelectorAll('.anim-li');
  if (!items.length) return;
  if (!anime) return;
  anime.animate(items, {
    opacity: [0, 1],
    translateY: [8, 0],
    duration: 240,
    delay: anime.stagger(22, { start: 0 }),
    ease: 'outQuad',
  });
}

/* ── pop d'un fragment isolé (alerte, détail) ───────────────────────── */
function popIn(scope) {
  const els = scope.classList?.contains('anim-pop')
    ? [scope] : scope.querySelectorAll('.anim-pop');
  if (!els.length) return;
  if (!anime) return;
  anime.animate(els, {
    opacity: [0, 1], translateY: [6, 0], duration: 220, ease: 'outQuad',
  });
}

/* ── KPI count-up ───────────────────────────────────────────────────── */
function countUps(scope) {
  scope.querySelectorAll('[data-countup]').forEach(el => {
    const target = parseFloat(el.dataset.countup);
    if (Number.isNaN(target)) return;
    if (!anime) { el.textContent = el.dataset.suffix ? target + el.dataset.suffix : target; return; }
    const decimals = el.dataset.decimals ? parseInt(el.dataset.decimals) : 0;
    const obj = { v: 0 };
    anime.animate(obj, {
      v: target, duration: 700, ease: 'outCubic',
      onUpdate: () => {
        el.textContent = obj.v.toFixed(decimals) + (el.dataset.suffix || '');
      },
    });
  });
}

/* ── sparkline SVG : tracé progressif ───────────────────────────────── */
function drawLines(scope) {
  const lines = scope.querySelectorAll('polyline.drawable, path.drawable');
  if (!lines.length || !anime?.svg) return;
  try {
    lines.forEach((el, i) => {
      const d = anime.svg.createDrawable(el);
      anime.animate(d, { draw: '0 1', duration: 900, delay: i * 150, ease: 'inOutQuad' });
    });
  } catch (e) { /* tracé statique = très bien aussi */ }
}

/* ── remplissage des barres .sbar (width est posée en data-w) ───────── */
function fillBars(scope) {
  scope.querySelectorAll('.sbar .fl[data-w]').forEach(el => {
    requestAnimationFrame(() => { el.style.width = el.dataset.w + '%'; });
  });
}

/* ── header : prompt qui se tape (1× par session) ───────────────────── */
function typePrompt() {
  const el = document.querySelector('[data-type-once]');
  if (!el) return;
  const key = 'pds_typed_' + location.pathname;
  if (REDUCED || !anime || sessionStorage.getItem(key)) return;
  sessionStorage.setItem(key, '1');
  const full = el.textContent;
  el.textContent = '';
  el.style.minHeight = '1.2em';
  let i = 0;
  const tick = () => {
    el.textContent = full.slice(0, ++i);
    if (i < full.length) setTimeout(tick, 9 + Math.random() * 18);
  };
  tick();
}

/* ── feedback : flash phosphore sur l'élément qui vient d'agir ──────── */
function pulse(el) {
  if (!anime || !el) return;
  anime.animate(el, {
    boxShadow: ['0 0 0 rgba(0,255,65,0)', '0 0 14px rgba(0,255,65,.35)',
                '0 0 0 rgba(0,255,65,0)'],
    duration: 550, ease: 'outQuad',
  });
}

/* ── orchestration ──────────────────────────────────────────────────── */
function animateScope(scope) {
  staggerIn(scope);
  popIn(scope);
  countUps(scope);
  drawLines(scope);
  fillBars(scope);
}

document.addEventListener('DOMContentLoaded', () => {
  animateScope(document);
  typePrompt();
});
if (document.readyState !== 'loading') {
  animateScope(document);
  typePrompt();
}

/* fragments HTMX : on anime UNIQUEMENT le contenu qui vient d'arriver */
document.body.addEventListener('htmx:afterSwap', e => {
  animateScope(e.detail.target);
});
document.body.addEventListener('htmx:afterRequest', e => {
  const src = e.detail.elt;
  if (src?.matches?.('button,.btn') && e.detail.successful) pulse(src);
});



export { pulse };
