/*
 * 계룡 초·중학생 혜택·행사 안내
 * 순서: 데이터 읽기 → 검증 → 상태 계산 → 필터 → 정렬 → 화면 그리기
 * 내용을 고칠 때는 이 파일이 아니라 data/benefits.json, data/events.json 을 수정하세요.
 */
(function () {
  'use strict';

  var GRADES = ['초1', '초2', '초3', '초4', '초5', '초6', '중1', '중2', '중3'];
  var TYPES = ['혜택', '프로그램', '장학금'];
  var SCOPES = ['충남 전체', '계룡시'];
  var EVENT_FIELDS = ['과학', '문학', '기타'];
  var BENEFIT_REQUIRED = ['id', 'name', 'type', 'field', 'target', 'content', 'org', 'scope', 'url', 'source', 'checkedAt'];
  var EVENT_REQUIRED = ['id', 'name', 'field', 'target', 'org', 'url', 'source', 'checkedAt', 'residency'];
  var URGENT_DAYS = 7;
  var UNKNOWN = '확인 필요';

  var state = {
    tab: 'benefits',
    bSearch: '', bGrade: '', bType: '', bScope: '', bField: '', bOrg: '', bClosed: false,
    eGrade: '', eField: '', eFree: false, ePast: false
  };
  var benefits = [];
  var events = [];

  var el = {};
  ['tabs', 'filters-benefits', 'filters-events', 'b-search', 'b-grade', 'b-type', 'b-scope', 'b-field', 'b-org', 'b-closed',
    'e-grade', 'e-field', 'e-free', 'e-past', 'cards', 'result-count', 'empty', 'notice', 'reset-btn', 'empty-reset-btn'
  ].forEach(function (id) { el[id] = document.getElementById(id); });

  /* ---------- 날짜 (한국 시간 기준) ---------- */

  function todayKST() {
    // en-CA 형식은 YYYY-MM-DD
    return new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit'
    }).format(new Date());
  }

  function isValidDate(s) {
    if (typeof s !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
    var p = s.split('-').map(Number);
    var d = new Date(Date.UTC(p[0], p[1] - 1, p[2]));
    return d.getUTCFullYear() === p[0] && d.getUTCMonth() === p[1] - 1 && d.getUTCDate() === p[2];
  }

  function toDayNumber(s) {
    var p = s.split('-').map(Number);
    return Date.UTC(p[0], p[1] - 1, p[2]) / 86400000;
  }

  function formatDate(s) {
    return s.split('-').join('.');
  }

  function formatRange(start, end) {
    if (start && end) return start === end ? formatDate(start) : formatDate(start) + ' ~ ' + formatDate(end);
    if (start) return formatDate(start) + ' ~';
    if (end) return '~ ' + formatDate(end);
    return '';
  }

  /* ---------- 검증 ---------- */

  function validateCommon(item, required, seenIds, dateKeys) {
    var errors = [];
    if (!item || typeof item !== 'object') return ['객체 형식이 아닙니다'];
    required.forEach(function (key) {
      if (item[key] === undefined || item[key] === null || item[key] === '') {
        errors.push('"' + key + '" 항목이 비어 있습니다');
      }
    });
    if (errors.length) return errors;

    if (seenIds[item.id]) errors.push('id "' + item.id + '"가 중복됩니다');
    if (!Array.isArray(item.grades)) {
      errors.push('grades는 ["초1", "중3"]처럼 배열이어야 합니다 (모르면 [])');
    } else {
      item.grades.forEach(function (g) {
        if (GRADES.indexOf(g) === -1) errors.push('grades 값 "' + g + '"은(는) 허용되지 않습니다');
      });
    }
    dateKeys.forEach(function (key) {
      if (item[key] && !isValidDate(item[key])) errors.push(key + ' "' + item[key] + '"이(가) YYYY-MM-DD 형식이 아닙니다');
    });
    if (!isValidDate(item.checkedAt)) errors.push('checkedAt "' + item.checkedAt + '"이(가) YYYY-MM-DD 형식이 아닙니다');
    if (item.applyStart && item.applyEnd && item.applyStart > item.applyEnd) errors.push('applyStart가 applyEnd보다 늦습니다');
    if (!/^https?:\/\//.test(String(item.url))) errors.push('url은 http:// 또는 https:// 로 시작해야 합니다');
    return errors;
  }

  function validateBenefit(item, seenIds) {
    var errors = validateCommon(item, BENEFIT_REQUIRED, seenIds, ['applyStart', 'applyEnd']);
    if (errors.length && !item.id) return errors;
    if (TYPES.indexOf(item.type) === -1) errors.push('type은 ' + TYPES.join(', ') + ' 중 하나여야 합니다');
    if (SCOPES.indexOf(item.scope) === -1) errors.push('scope는 ' + SCOPES.join(' 또는 ') + '여야 합니다');
    return errors;
  }

  function validateEvent(item, seenIds) {
    var errors = validateCommon(item, EVENT_REQUIRED, seenIds, ['eventStart', 'eventEnd', 'applyStart', 'applyEnd']);
    if (errors.length && !item.id) return errors;
    if (EVENT_FIELDS.indexOf(item.field) === -1) errors.push('field는 ' + EVENT_FIELDS.join(', ') + ' 중 하나여야 합니다');
    if (item.eventStart && item.eventEnd && item.eventStart > item.eventEnd) errors.push('eventStart가 eventEnd보다 늦습니다');
    return errors;
  }

  /* ---------- 상태 계산 ---------- */

  function dday(left) {
    return left === 0 ? 'D-DAY' : 'D-' + left;
  }

  function benefitStatus(item, today) {
    var c = Object.assign({}, item);
    if (item.type === '혜택') {
      c.status = '혜택';
      c.badge = 'benefit';
      c.badgeText = '혜택';
      c.order = 2;
      return c;
    }
    var t = toDayNumber(today);
    // applyClosed: 정확한 마감일은 모르지만 마감이 확인된 항목
    if (item.applyClosed || (item.applyEnd && t > toDayNumber(item.applyEnd))) {
      c.status = '마감';
      c.badge = 'closed';
      c.badgeText = '마감';
      c.order = 4;
    } else if (item.applyStart && t < toDayNumber(item.applyStart)) {
      c.status = '예정';
      c.badge = 'upcoming';
      c.badgeText = '예정';
      c.order = 1;
      c.sortKey = toDayNumber(item.applyStart);
    } else if (item.applyStart || item.applyEnd) {
      var left = item.applyEnd ? toDayNumber(item.applyEnd) - t : null;
      c.status = '모집 중';
      c.badge = left !== null && left <= URGENT_DAYS ? 'urgent' : 'open';
      c.badgeText = '모집 중' + (left !== null ? ' ' + dday(left) : '');
      c.order = 0;
      c.sortKey = left === null ? 99999 : left;
    } else {
      c.status = '일정 확인';
      c.badge = 'neutral';
      c.badgeText = '일정 확인';
      c.order = 3;
    }
    return c;
  }

  function eventStatus(item, today) {
    var c = Object.assign({}, item);
    var t = toDayNumber(today);
    var start = item.eventStart ? toDayNumber(item.eventStart) : null;
    var end = item.eventEnd ? toDayNumber(item.eventEnd) : start;
    var applyStart = item.applyStart ? toDayNumber(item.applyStart) : null;
    var applyEnd = item.applyEnd ? toDayNumber(item.applyEnd) : null;
    c.urgent = false;

    if (start === null) {
      c.status = '상시';
      c.badge = 'neutral';
      c.badgeText = '상시';
      c.sortDay = Infinity;
    } else if (t > end) {
      c.status = '지난 행사';
      c.badge = 'closed';
      c.badgeText = '지난 행사';
      c.sortDay = Infinity;
    } else {
      c.sortDay = Math.max(start, t);
      if (applyStart !== null && t < applyStart) {
        c.status = '예정';
        c.badge = 'upcoming';
        c.badgeText = '신청 ' + (applyStart - t) + '일 후 시작';
      } else if (applyEnd !== null && t > applyEnd) {
        c.status = '예정';
        c.applyEnded = true;
        c.badge = 'closed';
        c.badgeText = t < start ? '신청 마감' : '진행 중 · 신청 마감';
      } else if (applyStart !== null || applyEnd !== null) {
        c.status = '신청 중';
        if (applyEnd === null) {
          c.badge = 'open';
          c.badgeText = '신청 중';
        } else {
          c.urgent = applyEnd - t <= URGENT_DAYS;
          c.badge = c.urgent ? 'urgent' : 'open';
          c.badgeText = '신청 중 ' + dday(applyEnd - t);
        }
      } else {
        c.status = '예정';
        c.badge = 'upcoming';
        c.badgeText = t < start ? '예정' : '진행 중';
      }
    }
    return c;
  }

  function compareBenefits(a, b) {
    if (a.order !== b.order) return a.order - b.order;
    if (a.order < 2) return a.sortKey - b.sortKey;
    return a.name.localeCompare(b.name, 'ko');
  }

  var EVENT_ORDER = { '신청 중': 0, '예정': 0, '상시': 1, '지난 행사': 2 };

  function compareEvents(a, b) {
    var o = EVENT_ORDER[a.status] - EVENT_ORDER[b.status];
    if (o !== 0) return o;
    if (a.status === '지난 행사') return (b.eventEnd || '').localeCompare(a.eventEnd || ''); // 최근 순
    if (a.sortDay !== b.sortDay) return a.sortDay < b.sortDay ? -1 : 1;                      // 날짜순
    return a.name.localeCompare(b.name, 'ko');
  }

  /* ---------- 필터 ---------- */

  // 학년을 확인하지 못한 항목(grades 비어 있음)은 어떤 학년을 골라도 숨기지 않음
  function matchesGrade(c, grade) {
    return !grade || c.grades.length === 0 || c.grades.indexOf(grade) !== -1;
  }

  function matchesBenefit(c) {
    if (c.status === '마감' && !state.bClosed) return false;
    if (!matchesGrade(c, state.bGrade)) return false;
    if (state.bType && c.type !== state.bType) return false;
    if (state.bScope && c.scope !== state.bScope) return false;
    if (state.bField && c.field !== state.bField) return false;
    if (state.bOrg && c.org !== state.bOrg) return false;
    if (state.bSearch) {
      var hay = [c.name, c.org, c.target, c.content, c.field].join(' ').toLowerCase();
      var words = state.bSearch.toLowerCase().split(/\s+/).filter(Boolean);
      if (!words.every(function (w) { return hay.indexOf(w) !== -1; })) return false;
    }
    return true;
  }

  function matchesEvent(c) {
    if ((c.status === '지난 행사' || c.applyEnded) && !state.ePast) return false;
    if (!matchesGrade(c, state.eGrade)) return false;
    if (state.eField && c.field !== state.eField) return false;
    if (state.eFree && String(c.cost).indexOf('무료') === -1) return false;
    return true;
  }

  /* ---------- URL 쿼리 ↔ 상태 ---------- */

  var QUERY_KEYS = {
    q: 'bSearch', grade: 'bGrade', type: 'bType', scope: 'bScope', field: 'bField', org: 'bOrg',
    egrade: 'eGrade', efield: 'eField'
  };

  function readQuery() {
    var q = new URLSearchParams(location.search);
    if (q.get('tab') === 'events') state.tab = 'events';
    Object.keys(QUERY_KEYS).forEach(function (k) {
      if (q.get(k)) state[QUERY_KEYS[k]] = q.get(k);
    });
    state.bClosed = q.get('closed') === '1';
    state.eFree = q.get('free') === '1';
    state.ePast = q.get('past') === '1';
  }

  function writeQuery() {
    var q = new URLSearchParams();
    if (state.tab === 'events') q.set('tab', 'events');
    Object.keys(QUERY_KEYS).forEach(function (k) {
      if (state[QUERY_KEYS[k]]) q.set(k, state[QUERY_KEYS[k]]);
    });
    if (state.bClosed) q.set('closed', '1');
    if (state.eFree) q.set('free', '1');
    if (state.ePast) q.set('past', '1');
    var s = q.toString();
    history.replaceState(null, '', location.pathname + (s ? '?' + s : ''));
  }

  /* ---------- 화면 그리기 ---------- */

  function make(tag, className, text) {
    var n = document.createElement(tag);
    if (className) n.className = className;
    if (text !== undefined) n.textContent = text; // textContent만 사용 → HTML 삽입 방지
    return n;
  }

  function chip(label, pressed, onClick) {
    var b = make('button', 'chip', label);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(pressed));
    b.addEventListener('click', onClick);
    return b;
  }

  function renderChips(container, values, current, onPick) {
    container.textContent = '';
    container.appendChild(chip('전체', !current, function () { onPick(''); }));
    values.forEach(function (v) {
      container.appendChild(chip(v, current === v, function () { onPick(v); }));
    });
  }

  function fillSelect(select, allLabel, values) {
    select.textContent = '';
    var all = make('option', '', allLabel);
    all.value = '';
    select.appendChild(all);
    values.forEach(function (v) {
      var o = make('option', '', v);
      o.value = v;
      select.appendChild(o);
    });
  }

  function uniqueSorted(list, key) {
    var seen = {};
    list.forEach(function (c) { seen[c[key]] = true; });
    return Object.keys(seen).sort(function (a, b) { return a.localeCompare(b, 'ko'); });
  }

  function renderTabs() {
    el.tabs.textContent = '';
    [['benefits', '충남·계룡 혜택/프로그램/장학금', benefits], ['events', '대전 행사', events]].forEach(function (t) {
      var tab = make('button', 'tab', t[1]);
      tab.type = 'button';
      tab.setAttribute('aria-pressed', String(state.tab === t[0]));
      tab.addEventListener('click', function () { state.tab = t[0]; update(); });
      el.tabs.appendChild(tab);
    });
  }

  function renderFilters() {
    renderTabs();
    el['filters-benefits'].hidden = state.tab !== 'benefits';
    el['filters-events'].hidden = state.tab !== 'events';

    renderChips(el['b-type'], TYPES, state.bType, function (v) { state.bType = v; update(); });
    renderChips(el['b-scope'], SCOPES, state.bScope, function (v) { state.bScope = v; update(); });
    renderChips(el['e-field'], EVENT_FIELDS, state.eField, function (v) { state.eField = v; update(); });

    if (el['b-search'].value !== state.bSearch) el['b-search'].value = state.bSearch;
    el['b-grade'].value = state.bGrade;
    el['b-field'].value = state.bField;
    el['b-org'].value = state.bOrg;
    el['b-closed'].checked = state.bClosed;
    el['e-grade'].value = state.eGrade;
    el['e-free'].checked = state.eFree;
    el['e-past'].checked = state.ePast;
  }

  function addRows(dl, rows) {
    rows.forEach(function (row) {
      if (!row[1]) return;
      dl.appendChild(make('dt', '', row[0]));
      dl.appendChild(make('dd', row[1] === UNKNOWN ? 'unknown' : '', row[1]));
    });
  }

  function gradeLabel(grades) {
    if (!grades.length) return '학년 확인 필요';
    var names = GRADES.filter(function (g) { return grades.indexOf(g) !== -1; });
    var first = GRADES.indexOf(names[0]);
    var contiguous = GRADES.indexOf(names[names.length - 1]) - first === names.length - 1;
    if (names.length === 1) return names[0];
    return contiguous ? names[0] + '~' + names[names.length - 1] : names.join(', ');
  }

  // 카드 공통 틀: "자세히 보기" 버튼을 누르면 아래 상세가 열림
  function card(c, meta, flags, summaryRows, detailRows, linkLabel) {
    var li = make('li', 'card' + (c.badge === 'closed' && c.status !== '예정' ? ' is-closed' : ''));
    var head = make('div', 'card-head');

    var top = make('div', 'card-top');
    top.appendChild(make('span', 'badge ' + c.badge, c.badgeText));
    top.appendChild(make('span', 'region', meta));
    head.appendChild(top);
    head.appendChild(make('h2', '', c.name));
    head.appendChild(make('p', 'organizer', c.org));

    var tags = make('ul', 'tags');
    tags.setAttribute('aria-label', '구분');
    tags.appendChild(make('li', 'tag', gradeLabel(c.grades)));
    flags.forEach(function (f) { tags.appendChild(make('li', 'tag ' + f[1], f[0])); });
    head.appendChild(tags);

    var info = make('dl', 'info');
    addRows(info, summaryRows);
    head.appendChild(info);
    li.appendChild(head);

    var body = make('div', 'card-detail');
    body.id = 'detail-' + c.id;
    body.hidden = true;
    var dl = make('dl', 'info');
    addRows(dl, detailRows);
    body.appendChild(dl);

    var actions = make('div', 'card-actions');
    var more = make('button', 'more', '자세히 보기');
    more.type = 'button';
    more.setAttribute('aria-expanded', 'false');
    more.setAttribute('aria-controls', body.id);
    more.setAttribute('aria-label', c.name + ' 자세히 보기');
    more.addEventListener('click', function () {
      body.hidden = !body.hidden;
      more.setAttribute('aria-expanded', String(!body.hidden));
    });
    actions.appendChild(more);

    var a = make('a', 'btn', linkLabel);
    a.href = c.url;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.setAttribute('aria-label', c.name + ' ' + linkLabel + ' (새 창)');
    actions.appendChild(a);
    li.appendChild(actions);
    li.appendChild(body);

    li.appendChild(make('p', 'updated', '출처 ' + c.source + ' · 최종 확인 ' + formatDate(c.checkedAt)));
    return li;
  }

  function benefitCard(c) {
    var flags = [];
    if (c.applyUnit && c.applyUnit !== '개인' && c.applyUnit !== UNKNOWN) flags.push([c.applyUnit + ' 신청', 'warn']);
    if (c.stale) flags.push(['오래된 자료 (' + c.dataYear + ')', 'warn']);
    var period = formatRange(c.applyStart, c.applyEnd);
    var apply = c.applyNote && period ? period + ' (' + c.applyNote + ')' : (period || c.applyNote || UNKNOWN);
    return card(c, c.type + ' · ' + c.field + ' · ' + c.scope, flags,
      [['대상', c.target], [c.type === '혜택' ? '내용' : '신청', c.type === '혜택' ? c.content : apply]],
      [['내용', c.type === '혜택' ? '' : c.content],
       ['일정', c.schedule], ['장소', c.place], ['신청 방법', c.applyMethod],
       ['신청 기간', c.type === '혜택' ? apply : ''], ['비용', c.cost],
       ['자료 기준', c.dataYear]],
      '공식 링크');
  }

  function eventCard(c) {
    var flags = [];
    if (c.targetUnconfirmed) flags.push(['대상 확인 필요', 'warn']);
    if (c.residency.indexOf('한정') !== -1) flags.push([c.residency, 'warn']);
    if (c.urgent) flags.push(['신청 마감 임박', 'alert']);
    if (String(c.cost).indexOf('무료') === 0) flags.push(['무료', 'free']);
    var period = formatRange(c.applyStart, c.applyEnd);
    var apply = c.applyNote && period ? period + ' (' + c.applyNote + ')' : (period || c.applyNote || UNKNOWN);
    return card(c, c.field, flags,
      [['일시', formatRange(c.eventStart, c.eventEnd) || c.schedule], ['대상', c.target], ['신청', apply]],
      [['시간·일정', c.eventStart ? c.schedule : ''], ['장소', c.place], ['신청 방법', c.applyMethod],
       ['비용', c.cost], ['정원', c.capacity], ['거주지', c.residency]],
      '공식 링크');
  }

  function renderList() {
    var isB = state.tab === 'benefits';
    var list = (isB ? benefits.filter(matchesBenefit).sort(compareBenefits)
      : events.filter(matchesEvent).sort(compareEvents));
    el.cards.textContent = '';
    list.forEach(function (c) { el.cards.appendChild(isB ? benefitCard(c) : eventCard(c)); });
    el['result-count'].textContent = list.length + (isB ? '개 항목' : '개 행사');
    el.empty.hidden = list.length !== 0;
  }

  function update() {
    renderFilters();
    renderList();
    writeQuery();
  }

  function resetFilters() {
    if (state.tab === 'benefits') {
      state.bSearch = state.bGrade = state.bType = state.bScope = state.bField = state.bOrg = '';
      state.bClosed = false;
    } else {
      state.eGrade = state.eField = '';
      state.eFree = state.ePast = false;
    }
    update();
  }

  function showNotice(title, lines) {
    if (!el.notice.hidden) el.notice.appendChild(make('hr'));
    el.notice.appendChild(make('strong', '', title));
    if (lines && lines.length) {
      var ul = make('ul');
      lines.forEach(function (l) { ul.appendChild(make('li', '', l)); });
      el.notice.appendChild(ul);
    }
    el.notice.hidden = false;
  }

  /* ---------- 데이터 읽기 ---------- */

  function load(path, key, validate, enrich, target) {
    return fetch(path, { cache: 'no-cache' })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.text();
      })
      .then(function (text) {
        var data;
        try {
          data = JSON.parse(text);
        } catch (e) {
          throw new Error('JSON 문법 오류: ' + e.message);
        }
        var items = Array.isArray(data && data[key]) ? data[key] : null;
        if (!items) throw new Error('최상위에 "' + key + '" 배열이 없습니다');

        var today = todayKST();
        var seen = {};
        var problems = [];
        items.forEach(function (item, i) {
          var errs = validate(item, seen);
          var label = (item && item.id) ? 'id "' + item.id + '"' : (i + 1) + '번째 항목';
          if (errs.length) {
            problems.push(label + ': ' + errs.join(', '));
            console.warn('[' + path + '] ' + label, errs);
            return;
          }
          seen[item.id] = true;
          target.push(enrich(item, today));
        });
        if (problems.length) {
          showNotice(problems.length + '개 항목에 오류가 있어 화면에서 뺐습니다. ' + path + '를 확인해 주세요.', problems);
        }
      })
      .catch(function (err) {
        console.error(err);
        var hint = location.protocol === 'file:'
          ? ['파일을 더블클릭해서 열면 데이터를 읽을 수 없습니다. README의 "내 컴퓨터에서 미리 보기"를 참고하세요.']
          : [err.message];
        showNotice(path + ' 파일을 읽을 수 없습니다.', hint);
      });
  }

  /* ---------- 시작 ---------- */

  function init() {
    fillSelect(el['b-grade'], '전체 학년', GRADES);
    fillSelect(el['e-grade'], '전체 학년', GRADES);

    el['b-search'].addEventListener('input', function () { state.bSearch = el['b-search'].value; renderList(); writeQuery(); });
    [['b-grade', 'bGrade'], ['b-field', 'bField'], ['b-org', 'bOrg'], ['e-grade', 'eGrade']].forEach(function (p) {
      el[p[0]].addEventListener('change', function () { state[p[1]] = el[p[0]].value; update(); });
    });
    el['b-closed'].addEventListener('change', function () { state.bClosed = el['b-closed'].checked; update(); });
    el['e-free'].addEventListener('change', function () { state.eFree = el['e-free'].checked; update(); });
    el['e-past'].addEventListener('change', function () { state.ePast = el['e-past'].checked; update(); });
    el['reset-btn'].addEventListener('click', resetFilters);
    el['empty-reset-btn'].addEventListener('click', resetFilters);

    readQuery();
    renderFilters();
    el['result-count'].textContent = '불러오는 중…';

    Promise.all([
      load('data/benefits.json', 'benefits', validateBenefit, benefitStatus, benefits),
      load('data/events.json', 'events', validateEvent, eventStatus, events)
    ]).then(function () {
      fillSelect(el['b-field'], '전체 분야', uniqueSorted(benefits, 'field'));
      fillSelect(el['b-org'], '전체 기관', uniqueSorted(benefits, 'org'));
      update();
    });
  }

  init();
})();
