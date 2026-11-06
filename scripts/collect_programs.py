"""표 형식으로 정리된 프로그램 목록을 읽어 혜택·행사 카드 형식으로 저장한다.

대상·기간·비용이 칸으로 나뉜 목록만 읽는다 (AI 없이 정확히 뽑을 수 있는 곳).
  - 계룡시청소년별마루센터 프로그램  → data/auto-benefits.json
  - 충남평생교육 강좌(cnall) 중 도 단위 기관 강좌 → data/auto-benefits.json
  - 국립중앙과학관 교육 프로그램      → data/auto-events.json
손으로 정리한 항목(benefits.json, events.json)과 같은 프로그램은 넣지 않는다.
학년이나 날짜를 확실히 읽지 못하면 비워 두어 사이트에 "확인 필요"로 나오게 한다.
한 기관이 실패하면 그 기관은 지난번 결과를 그대로 둔다.
"""
import hashlib
import json
import re
import sys
import time
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

from bs4 import BeautifulSoup

from collect_notices import KST, RobotsBlocked, clean, fetch, robots_allows

DATA = Path(__file__).resolve().parent.parent / 'data'
OUT_BENEFITS = DATA / 'auto-benefits.json'
OUT_EVENTS = DATA / 'auto-events.json'
GRADES = ['초1', '초2', '초3', '초4', '초5', '초6', '중1', '중2', '중3']
UNKNOWN = '확인 필요'
AUTO_NOTE = '자동 수집한 항목입니다. 자세한 조건은 공식 링크에서 확인하세요.'

# cnall에서 넣을 기관: 충남 전체 학생 대상 도 단위 기관만 (다른 시·군 도서관은 수록 범위 밖)
CNALL_ORGS = {
    '남부평생교육원': '충청남도교육청남부평생교육원',
    '서부평생교육원': '충청남도교육청서부평생교육원',
    '평생교육원': '충청남도교육청평생교육원',
    '학생교육문화원': '충청남도교육청학생교육문화원',
}

# 제목 단어로 분야 고르기 (benefits.json에 이미 있는 분야 이름을 씀)
FIELD_WORDS = [
    ('디지털·AI', ['AI', '코딩', '드론', '로봇', '메타버스', '디지털', '스마트', '소프트웨어', 'SW']),
    ('과학', ['과학', '실험', '탐구', '천문', '생물', '탐사']),
    ('독서', ['독서', '책', '그림책', '글쓰기', '문학', '도서']),
    ('진로', ['진로', '직업', '꿈']),
    ('역사', ['역사', '문화유산', '박물관']),
    ('문화·예술', ['미술', '음악', '공예', '그리기', '만들기', '예술', '공연', '댄스', '연극', '영화', '사진', '종이접기']),
]

TODAY = datetime.now(KST).date()


# ---------- 글자에서 값 뽑기 ----------

def school_year_grade(birth_year):
    """출생 연도 → 올해 학년 위치 (초1=1 … 중3=9). 3월 학년 시작 기준."""
    school_year = TODAY.year if TODAY.month >= 3 else TODAY.year - 1
    return school_year - (birth_year + 7) + 1


LEVEL_RANGE = {'초': (1, 6), '중': (7, 9), '고': (10, 12)}
LEVEL_BASE = {'초': 0, '중': 6, '고': 9}
# '초등', '중학', '중2'처럼 뒤에 등·학·숫자가 와야 학교급으로 봄 ('접수중', '집중'의 '중'은 무시)
LEVEL_RE = re.compile(r'(초|중|고)(?=등|학|\s*\d)(?:등|학)?(?:학교|학생|학년|부)?\s*(\d)?(?:\s*학년)?(?:\s*[~\-]\s*(\d)\s*(?:학년)?)?')


def parse_grades(text):
    """대상 글에서 학년을 읽는다. 확실하지 않으면 None, 초·중학생이 아니면 []."""
    text = text or ''
    points = []  # (최소, 최대) 학년 위치. 유아=0, 성인=13

    # 출생 연도: '2011~2016년생', '17년생~14년생', '(2016년생)'
    years = [int(y) + (2000 if len(y) == 2 else 0) for y in re.findall(r'(\d{4}|\d{2})\s*년\s*생', text)]
    span = re.search(r'(\d{4}|\d{2})\s*[~\-]\s*(\d{4}|\d{2})\s*년\s*생', text)
    if span:
        years += [int(y) + (2000 if len(y) == 2 else 0) for y in span.groups()]
    for y in years:
        if 1990 < y <= TODAY.year:
            g = school_year_grade(y)
            points.append((g, g))

    levels = list(LEVEL_RE.finditer(text))
    numbered = {m[1] for m in levels if m[2]}  # '[초3~5] … 초등영어'처럼 숫자 학년이 있으면 그 학교급의 '초등' 단어는 무시
    for m in levels:
        level, a, b = m.groups()
        base = LEVEL_BASE[level]
        if a and b:
            points.append((base + int(a), base + int(b)))
        elif a:
            points.append((base + int(a), base + int(a)))
        elif level not in numbered:
            points.append(LEVEL_RANGE[level])
    if re.search(r'초\s*[·,]\s*중', text):  # '초·중학생'
        points += [LEVEL_RANGE['초'], LEVEL_RANGE['중']]
    if '유아' in text or re.search(r'\d+\s*세\s*(유아|이상)', text):
        points.append((0, 0))
    if '성인' in text or '전 연령' in text:
        points.append((13, 13))

    if not points:
        return None
    if '~' in text or '이상' in text or '부터' in text:
        lo = min(p[0] for p in points)
        hi = max(p[1] for p in points)
        if '이상' in text and not re.search(r'이상\s*[~\-]', text) and hi == lo:
            hi = 13
        wanted = range(lo, hi + 1)
    else:
        wanted = set()
        for lo, hi in points:
            wanted.update(range(lo, hi + 1))
    return [GRADES[i - 1] for i in sorted(set(wanted)) if 1 <= i <= 9]


DATE_TOKEN = re.compile(r'(?<![\d:])(?:(20\d{2})\s*(?:[.\-/]|년)\s*)?(\d{1,2})\s*(?:[.\-/]|월)\s*(\d{1,2})\s*(?:일|\.|(?![\d:]))')


def parse_dates(text, ref_year=None):
    """'2026. 11. 6.(금) 10:00 - 11. 20.' 같은 글에서 (시작, 끝) 날짜를 읽는다. 연도가 없으면 앞 날짜나 ref_year를 쓴다."""
    found = []
    year = ref_year
    for m in DATE_TOKEN.finditer(text or ''):
        y, mo, d = m.groups()
        if y:
            year = int(y)
        if not year:
            continue
        try:
            day = date(year, int(mo), int(d))
        except ValueError:
            continue
        if found and day < found[-1] and not y:  # 연말을 넘기는 기간: '12. 20. ~ 1. 10.'
            try:
                day = date(year + 1, int(mo), int(d))
                year += 1
            except ValueError:
                continue
        found.append(day)
    if not found:
        return '', ''
    return found[0].isoformat(), found[-1].isoformat()


def guess_field(title, default):
    for field, words in FIELD_WORDS:
        if any(w in title for w in words):
            return field
    return default


def target_text(grades):
    if not grades:
        return ''
    if len(grades) == 1:
        return grades[0]
    return grades[0] + '~' + grades[-1]


def norm(title):
    t = re.sub(r'20\d{2}\s*년?|\(.*?\)|<.*?>|\[.*?\]|「|」|\'|"|‘|’|“|”|안내|모집|운영', '', title)
    return re.sub(r'[\s·:,.!?\-]', '', t).lower()


def make_id(prefix, *parts):
    return 'auto-%s-%s' % (prefix, hashlib.sha1('|'.join(parts).encode('utf-8')).hexdigest()[:10])


def soup(url):
    return BeautifulSoup(fetch(url, True).text, 'html.parser')


def check_robots(url):
    if not robots_allows(url, True):
        raise RobotsBlocked('robots.txt가 자동 수집을 허용하지 않음')


# ---------- 기관별 읽기 ----------

def collect_gryouth():
    base = 'https://www.gryouth.or.kr/prog/prgrm/star/sub03_01/list.do'
    check_robots(base)
    items = []
    for page in range(1, 4):  # 한 쪽에 4건. 최근 12건이면 모집 중·예정 프로그램은 모두 들어옴
        rows = soup(base + '?pageIndex=%d' % page).select('table tbody tr')
        for tr in rows:
            cell = {td.get('data-cell-header'): clean(td.get_text(' ')) for td in tr.find_all('td')}
            name = cell.get('프로그램명')
            if not name:
                continue
            target = cell.get('신청대상', '')
            grades = parse_grades(target)
            if grades == []:
                continue  # 초·중학생 대상이 아님
            apply_start, apply_end = parse_dates(cell.get('신청기간'))
            run_start, run_end = parse_dates(cell.get('수강기간'))
            if cell.get('모집상태') == '모집마감' or (apply_end and apply_end < TODAY.isoformat()):
                continue  # 신청이 끝난 프로그램
            cost = cell.get('수강료', '')
            items.append({
                'id': make_id('gryouth', name, run_start),
                'name': name, 'type': '프로그램', 'field': guess_field(name, '청소년 활동'),
                'target': target or UNKNOWN, 'grades': grades or [],
                'content': AUTO_NOTE + (' 모집 인원 ' + cell['신청/모집인원'] + '.' if cell.get('신청/모집인원') else ''),
                'schedule': formatted_range(run_start, run_end), 'place': cell.get('진행장소') or UNKNOWN,
                'applyMethod': '별마루센터 누리집 신청 (' + (cell.get('모집구분') or '모집') + ')',
                'applyStart': apply_start, 'applyEnd': apply_end, 'applyNote': '',
                'cost': '무료' if re.match(r'0\s*원', cost) else (cost or UNKNOWN),
                'org': '계룡시청', 'scope': '계룡시', 'applyUnit': '개인', 'url': base,
                'source': '계룡시청소년별마루센터', 'checkedAt': TODAY.isoformat(),
                'dataYear': (run_start or apply_start or TODAY.isoformat())[:4], 'stale': False, 'auto': True,
            })
        time.sleep(1)
    return items


def collect_cnall():
    base = 'https://www.cnall.or.kr/lecture/lectureList.do?searchTab=ING&targetCd=%s&currentPageNo=%d'
    check_robots(base % ('D', 1))
    items = {}
    for target_cd in ('D', 'E'):  # 초등학생, 중학생
        for page in range(1, 6):
            lis = soup(base % (target_cd, page)).select('ul.lecture-list > li')
            if not lis:
                break
            for li in lis:
                a = li.select_one('.title a')
                org_el = li.select_one('.info strong')
                m = re.search(r"fnDetail\('(\d+)'\)", a.get('href', '') if a else '')
                if not (a and org_el and m):
                    continue
                org = CNALL_ORGS.get(clean(org_el.get_text()))
                if not org or m[1] in items:
                    continue
                name = clean(a.get_text())
                info = clean(li.get_text(' '))
                icons = ', '.join(clean(i.get_text()) for i in li.select('.icon.target'))
                # 제목에 학년이 적혀 있으면 그것을, 없으면 대상 표시(초등학생·중학생 등)를 씀
                grades = parse_grades(name) if re.search(r'(초|중)[^\s]*\s*\d', name) else parse_grades(icons)
                if grades == []:
                    continue
                period = re.search(r'강좌기간\s*:\s*([\d.\s~]+)', info)
                apply = re.search(r'접수기간\s*:\s*([\d.\s~]+)', info)
                run_start, run_end = parse_dates(period[1] if period else '')
                apply_start, apply_end = parse_dates(apply[1] if apply else '')
                if apply_end and apply_end < TODAY.isoformat():
                    continue  # 신청이 끝난 강좌 ('모집중' 목록에 진행 중인 강좌도 섞여 나옴)
                fee = re.search(r'수강료\s*:\s*(\S+)', info)
                when = ' '.join(clean(s.get_text()) for s in li.select('.info')[0].find_all('span')[2:]) if li.select('.info') else ''
                items[m[1]] = {
                    'id': 'auto-cnall-' + m[1],
                    'name': name, 'type': '프로그램', 'field': guess_field(name, '평생교육'),
                    'target': icons or UNKNOWN, 'grades': grades or [],
                    'content': AUTO_NOTE + ' 수강 지역 조건 확인 필요.',
                    'schedule': (formatted_range(run_start, run_end) + ' ' + when).strip(), 'place': UNKNOWN,
                    'applyMethod': 'cnall.or.kr 강좌 신청', 'applyStart': apply_start, 'applyEnd': apply_end,
                    'applyNote': '', 'cost': fee[1] if fee else UNKNOWN,
                    'org': org, 'scope': '충남 전체', 'applyUnit': '개인',
                    'url': 'https://www.cnall.or.kr/lecture/lectureDetail.do?lectureIdx=' + m[1],
                    'source': org + ' (충남평생교육 강좌 신청)', 'checkedAt': TODAY.isoformat(),
                    'dataYear': (run_start or apply_start or TODAY.isoformat())[:4], 'stale': False, 'auto': True,
                }
            time.sleep(1)
    return list(items.values())


def collect_science():
    base = 'https://www.science.go.kr/mps/0/bbs/163/moveBbsNttList.do'
    check_robots(base)
    items = []
    for body in soup(base).select('tbody.singlerow'):
        m = re.search(r"fn_moveBbsNttDetail\('(\d+)'\)", body.get('onclick', ''))
        tds = [clean(td.get_text(' ')) for td in body.find_all('td')]
        if not m or len(tds) < 7:
            continue
        _, status, name, target, period, apply, hours = tds[:7]
        name = re.sub(r'\s*안내$', '', name)
        apply_start, apply_end = parse_dates(apply)
        ref = int((apply_start or TODAY.isoformat())[:4])
        start, end = parse_dates(period, ref)
        if not start or end < TODAY.isoformat():
            continue  # 지난 교육이거나 일정을 읽지 못함
        grades = parse_grades(target)
        if grades == []:
            continue
        items.append({
            'id': 'auto-science-' + m[1],
            'name': name, 'field': '과학', 'target': target or UNKNOWN, 'grades': grades or [],
            'targetUnconfirmed': grades is None,
            'eventStart': start, 'eventEnd': end, 'schedule': hours, 'place': '국립중앙과학관',
            'applyMethod': '국립중앙과학관 누리집 예약 (로그인 필요)',
            'applyStart': apply_start, 'applyEnd': apply_end, 'applyNote': '' if apply_start else apply,
            'cost': UNKNOWN, 'capacity': UNKNOWN, 'org': '국립중앙과학관',
            'url': base.replace('moveBbsNttList.do', 'moveBbsNttDetail.do?nttSn=' + m[1]),
            'source': '국립중앙과학관 (자동 수집)', 'checkedAt': TODAY.isoformat(),
            'residency': '제한 문구 없음(목록 기준, 신청 전 확인)', 'auto': True,
        })
    return items


def formatted_range(start, end):
    if not start:
        return ''
    s = start.replace('-', '.')
    if not end or end == start:
        return s
    return s + '~' + (end[5:].replace('-', '.') if end[:4] == start[:4] else end.replace('-', '.'))


# ---------- 저장 ----------

def is_duplicate(item, manual):
    """손으로 정리한 항목과 같은 프로그램인지.
    같은 상세 주소이거나, 이름이 거의 같고 날짜도 같아야 한다 ('11월 주말과학교실'과 '12월 주말과학교실'은 다른 항목)."""
    n = norm(item['name'])
    months = set(re.findall(r'(\d{1,2})\s*월', item['name']))
    starts = {item.get('eventStart'), item.get('applyStart')} - {'', None}
    for m in manual:
        if item['url'] == m.get('url') and 'list' not in item['url'].lower():
            return True
        mn = norm(m.get('name', ''))
        if not (n and mn and (n in mn or mn in n or SequenceMatcher(None, n, mn).ratio() >= 0.8)):
            continue
        other_months = set(re.findall(r'(\d{1,2})\s*월', m.get('name', '')))
        if months and other_months and months != other_months:
            continue
        other_starts = {m.get('eventStart'), m.get('applyStart')} - {'', None}
        if starts and other_starts and not (starts & other_starts):
            continue
        return True
    return False


SOURCES = [
    ('gryouth', '계룡시청소년별마루센터', collect_gryouth, 'benefits'),
    ('cnall', '충남평생교육 강좌 신청', collect_cnall, 'benefits'),
    ('science', '국립중앙과학관', collect_science, 'events'),
]


def load(path, key):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    manual = {
        'benefits': json.loads((DATA / 'benefits.json').read_text(encoding='utf-8'))['benefits'],
        'events': json.loads((DATA / 'events.json').read_text(encoding='utf-8'))['events'],
    }
    outputs = {'benefits': load(OUT_BENEFITS, 'benefits'), 'events': load(OUT_EVENTS, 'events')}
    result = {'benefits': {'items': [], 'sources': []}, 'events': {'items': [], 'sources': []}}

    for key, org, collect, kind in SOURCES:
        status = {'key': key, 'org': org, 'ok': True, 'kept': 0}
        try:
            found = collect()
            items = [i for i in found if not is_duplicate(i, manual[kind])]
            status['read'] = len(found)
        except Exception as err:  # 한 기관의 실패가 전체를 멈추지 않도록 지난 결과를 유지
            status['ok'] = False
            status['error'] = '%s: %s' % (type(err).__name__, str(err)[:200])
            prefix = 'auto-%s-' % key
            items = [i for i in outputs[kind].get(kind, []) if i['id'].startswith(prefix)]
        status['kept'] = len(items)
        result[kind]['items'] += items
        result[kind]['sources'].append(status)
        print('%-8s %s kept=%d %s' % (key, 'ok  ' if status['ok'] else 'FAIL', status['kept'], status.get('error', '')))

    for kind, path in (('benefits', OUT_BENEFITS), ('events', OUT_EVENTS)):
        seen, unique = set(), []
        for item in result[kind]['items']:
            if item['id'] not in seen:
                seen.add(item['id'])
                unique.append(item)
        path.write_text(json.dumps({'updatedAt': TODAY.isoformat(), 'sources': result[kind]['sources'], kind: unique},
                                   ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    ok = sum(1 for k in result.values() for s in k['sources'] if s['ok'])
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
