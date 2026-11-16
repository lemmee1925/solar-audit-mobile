/* ============================================================================
   mobile-enhance.js — Progressive mobile UX enhancements for Solar Audit
   Only runs at <=700px. Additive & degrades gracefully.
   ============================================================================ */
(function () {
  'use strict';
  var MOBILE = function () { return window.matchMedia('(max-width: 700px)').matches; };
  if (!MOBILE()) return;

  document.addEventListener('DOMContentLoaded', function () {

    /* 1. Active bottom-nav highlight */
    try {
      var path = window.location.pathname;
      document.querySelectorAll('.mobile-bottom-nav a').forEach(function (a) {
        var href = a.getAttribute('href') || '';
        if (href && href !== '/' && path.indexOf(href) === 0) a.classList.add('m-active');
        if (href === '/' && path === '/') a.classList.add('m-active');
      });
    } catch (e) {}

    /* 2. Collapsible sections for long forms */
    function makeAccordion(card, index) {
      var titleEl = card.querySelector('.card-title, .card-header h2, .card-header .card-title');
      var titleText = titleEl ? titleEl.textContent.trim() : ('Section ' + (index + 1));
      var fieldCount = card.querySelectorAll('.form-group').length;
      if (fieldCount < 4) return;
      var acc = document.createElement('div');
      acc.className = 'm-accordion' + (index === 0 ? ' open' : '');
      var head = document.createElement('div');
      head.className = 'm-accordion-head';
      head.innerHTML = '<span class="m-title">' + titleText + '</span>' +
        '<span class="m-count">' + fieldCount + ' fields</span>' +
        '<span class="m-chev">▶</span>';
      var body = document.createElement('div');
      body.className = 'm-accordion-body';
      var header = card.querySelector('.card-header');
      if (header) header.style.display = 'none';
      while (card.firstChild) { body.appendChild(card.firstChild); }
      acc.appendChild(head);
      acc.appendChild(body);
      card.appendChild(acc);
      head.addEventListener('click', function () { acc.classList.toggle('open'); });
    }
    document.querySelectorAll('form').forEach(function (form) {
      var cards = form.querySelectorAll(':scope > .card');
      if (cards.length >= 2) { cards.forEach(function (c, i) { makeAccordion(c, i); }); }
    });

    /* 3. Sticky Save bar */
    (function stickySave() {
      var candidate = null;
      document.querySelectorAll('form').forEach(function (f) {
        if (f.id === 'addEquipmentForm' || f.hasAttribute('data-no-sticky')) return;
        if (f.getAttribute('onsubmit') && /confirm\(/.test(f.getAttribute('onsubmit')) && f.querySelector('.btn-danger')) return;
        var submit = f.querySelector('button[type="submit"], input[type="submit"]');
        if (submit && !candidate) candidate = { form: f, submit: submit };
      });
      if (!candidate) return;
      if (document.querySelector('.mobile-save-bar')) return;
      var label = (candidate.submit.textContent || 'Save').trim();
      var bar = document.createElement('div');
      bar.className = 'm-sticky-save';
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn btn-primary';
      btn.textContent = label;
      btn.addEventListener('click', function () {
        if (candidate.form.requestSubmit) candidate.form.requestSubmit(candidate.submit);
        else candidate.submit.click();
      });
      bar.appendChild(btn);
      document.body.appendChild(bar);
      document.body.classList.add('m-has-sticky');
    })();

    /* 4. Tables -> cards */
    document.querySelectorAll('.card > table, .panel > table').forEach(function (table) {
      var heads = Array.prototype.map.call(table.querySelectorAll('thead th'),
        function (th) { return th.textContent.trim(); });
      if (!heads.length) return;
      table.classList.add('m-carded');
      table.querySelectorAll('tbody tr').forEach(function (tr) {
        var cells = tr.querySelectorAll('td');
        if (cells.length <= 1) { tr.classList.add('m-empty-row'); return; }
        cells.forEach(function (td, i) {
          var label = heads[i] || '';
          td.setAttribute('data-label', label);
          if (td.querySelector('.btn, form') && /action/i.test(label)) td.classList.add('m-actions');
        });
      });
    });

    /* 5. Floating "+ New Audit" button */
    (function fab() {
      var p = window.location.pathname;
      var showOn = (p === '/audits' || p === '/dashboard' || p === '/reports' ||
                    p === '/plants' || p === '/mobile' || p === '/');
      if (!showOn) return;
      if (document.querySelector('.m-fab')) return;
      var a = document.createElement('a');
      a.className = 'm-fab';
      a.href = '/audit/new';
      a.setAttribute('aria-label', 'Start new audit');
      a.textContent = '＋';
      document.body.appendChild(a);
    })();

  });
})();