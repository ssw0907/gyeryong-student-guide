"""기관 게시판에서 초·중학생이 볼 만한 공고의 제목·날짜·링크를 모아 data/notices.json에 저장한다.

누락을 줄이는 것이 우선이다. 교육·청소년 기관은 학생과 무관한 것이 확실한 공고만 빼고,
일반 행정·연구 기관은 제목에 학생 관련 단어가 있는 공고만 넣는다.
robots.txt가 허용하는 게시판만 읽는다. 한 기관이 실패해도 나머지는 계속 수집하고,
실패하거나 읽을 수 없는 기관은 파일의 sources에 기록해 사이트에 바로가기로 보여 준다.
기관을 추가하려면 SOURCES에 항목을 넣는다.
"""
import hashlib
import json
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

OUTPUT = Path(__file__).resolve().parent.parent / 'data' / 'notices.json'
KST = timezone(timedelta(hours=9))
KEEP_DAYS = 180
MAX_ITEMS = 500
SOURCE_DEADLINE = 150  # 기관 하나에 쓸 수 있는 최대 시간(초)
BOT_NAME = 'gyeryong-student-guide'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; gyeryong-student-guide/1.0; '
                         '+https://github.com/ssw0907/gyeryong-student-guide)'}

# 일반 행정·연구 기관(strict)은 제목에 하나라도 있어야 수록 (초·중학생 대상일 가능성이 있는 말)
INCLUDE = ['초등', '중학', '중등', '초·중', '초중', '학생', '청소년', '어린이', '아동', '가족', '자녀', '학부모', '학교',
           '영재', '장학', '방학', '캠프', '체험', '과학교실', '과학관', '독서', '진로', '코딩', '탐방', '공모전', '축제',
           '견학', '꿈', '그림책', '키즈', '주니어']
# 모든 기관에서 제목에 하나라도 있으면 제외 (학생과 무관한 것이 확실한 행정 공고만)
EXCLUDE = ['채용', '합격', '임기제', '공무원', '기간제', '입찰', '계약', '심의', '민간위탁', '교직원', '직무연수',
           '휴원', '휴관', '서버점검', '서비스 일시 중지', '시스템 점검', '공시송달', '분묘', '무연고', '도시계획', '환지',
           '용지', '매각', '매도', '보조금 교부', '보조사업자', '수탁기관', '행정처분', '청문', '고시', '행정예고', '의견수렴',
           '열람', '정보공개', '결과', '결산', '예산', '퇴직', '취업심사', '사칭', '특허', '연구과제', '과제 공고', '기술이전',
           '인턴연구원', '초빙', '참여기업', '강사 모집', '강사모집', '확정자',
           # 어른·기관 대상이 확실한 공고
           '어르신', '노인', '시니어', '상담원', '토론회', '간담회', '개발·보급', '담당자', '지도사', '연수', '심포지엄',
           '학술대회', '유공', '포상', '성료', '을지연습', '전문지원기관', '산업안전', '운영터전']

DATE_RE = re.compile(r'(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})')


def clean(text):
    return re.sub(r'\s+', ' ', text or '').strip()


def find_date(text):
    m = DATE_RE.search(text or '')
    if not m:
        return ''
    try:
        return datetime(int(m[1]), int(m[2]), int(m[3])).strftime('%Y-%m-%d')
    except ValueError:
        return ''


def fetch(url, verify):
    if not verify:
        requests.packages.urllib3.disable_warnings()
    res = requests.get(url, headers=HEADERS, timeout=40, verify=verify)
    res.raise_for_status()
    if not res.encoding or res.encoding.lower() == 'iso-8859-1':
        res.encoding = res.apparent_encoding
    return res


def soup(url, verify):
    return BeautifulSoup(fetch(url, verify).text, 'html.parser')


def strip_marks(title):
    return re.sub(r'^((공지|새글|NEW|new|HOT)\s*)+', '', title).strip()


FILE_LINK = re.compile(r'FileDown|dl\.action|download', re.I)
FILE_TEXT = re.compile(r'다운로드|\.(zip|pdf|hwpx?|png|jpe?g|xlsx?|docx?)$', re.I)


def parse_table(src):
    """표 형식 게시판. 제목은 행에서 글자가 가장 긴 링크(또는 버튼), 날짜는 제목 칸을 뺀 나머지 칸에서 찾는다."""
    items = []
    for tr in soup(src['url'], src['verify']).select('table tbody tr'):
        links = [a for a in tr.find_all(['a', 'button']) if clean(a.get_text())
                 and not FILE_TEXT.search(clean(a.get_text())) and not FILE_LINK.search(a.get('href', ''))]
        if not links:
            continue
        a = max(links, key=lambda x: len(clean(x.get_text())))
        href = a.get('href', '') if a.name == 'a' else ''
        # 스크립트 링크와 버튼은 상세 주소가 없으므로 게시판 주소로 연결
        direct = href and not href.startswith(('#', 'javascript'))
        title_cell = a.find_parent('td')
        rest = ' '.join(clean(td.get_text(' ')) for td in tr.find_all('td') if td is not title_cell)
        items.append({'title': strip_marks(clean(a.get_text())), 'date': find_date(rest),
                      'url': urljoin(src['url'], href) if direct else src['url']})
    return items


def parse_list(src):
    """ul·li 형식 목록. 날짜가 함께 적힌 항목의 링크를 공고로 본다."""
    items = []
    seen = set()
    page = soup(src['url'], src['verify'])
    scope = page.select_one(src['selector']) if src.get('selector') else page
    for li in (scope.select('li') if scope else []):
        a = li.find('a', href=True)
        if not a or li.find('li'):
            continue
        title = strip_marks(clean(a.get_text(' ')))
        date = find_date(clean(li.get_text(' ')).replace(title, ''))
        href = a['href']
        if not date or len(title) < 4 or href.startswith(('#', 'javascript')) or href in seen:
            continue
        seen.add(href)
        items.append({'title': title, 'date': date, 'url': urljoin(src['url'], href)})
    return items


def parse_cnall(src):
    """충남평생교육정보시스템 강좌 목록. 기관과 지역을 기관명에 붙이고, 접수 시작일을 날짜로 쓴다."""
    items = []
    for li in soup(src['url'], src['verify']).find_all('li'):
        a = li.select_one('div.title a')
        m = re.search(r"fnDetail\('(\d+)'\)", a.get('href', '')) if a else None
        if not m:
            continue
        info = clean(li.get_text(' '))
        org = li.select_one('.themeFC')
        area = li.select_one('div.title i.etc')
        apply = re.search(r'접수기간\s*:\s*([\d.]+)', info)
        items.append({'title': clean(a.get_text()), 'date': find_date(apply[1] if apply else ''),
                      'org': '%s (%s)' % (clean(org.get_text()), clean(area.get_text())) if org and area else '',
                      'url': urljoin(src['url'], 'lectureDetail.do?lectureIdx=' + m[1])})
    return items


def parse_cne_bbs(src):
    """충남교육청 계열 게시판. 링크가 onclick의 logDetail에 들어 있다."""
    items = []
    bbs_query = src['url'].split('?', 1)[1]
    for tr in soup(src['url'], src['verify']).select('table tbody tr'):
        a = next((x for x in tr.find_all('a') if 'logDetail' in x.get('onclick', '')), None)
        m = re.search(r"logDetail\('(\d+)'", a.get('onclick', '')) if a else None
        if not m:
            continue
        title_cell = a.find_parent('td')
        rest = ' '.join(clean(td.get_text(' ')) for td in tr.find_all('td') if td is not title_cell)
        items.append({'title': clean(a.get_text()), 'date': find_date(rest),
                      'url': urljoin(src['url'], '/cop/bbs/selectBoardArticle.do?%s&nttNo=%s' % (bbs_query, m[1]))})
    return items


def parse_brd_json(src):
    """brdList.do가 JSON을 돌려주는 게시판 (논산계룡교육지원청, 진로융합교육원)."""
    menu = re.search(r'menu_cd=(\d+)', src['url'])[1]
    rows = fetch(src['url'], src['verify']).json().get('brdList') or []
    return [{'title': clean(r.get('title')), 'date': find_date(r.get('write_dt')),
             'url': urljoin(src['url'], 'brdDetail.do?menu_cd=%s&num=%s' % (menu, r['num']))}
            for r in rows if r.get('title') and r.get('num')]


def parse_es_board(src):
    """board.es 게시판 (cn.pass.or.kr, 한국표준과학연구원). 표가 아닌 ul 목록이다."""
    items = []
    for a in soup(src['url'], src['verify']).find_all('a', href=re.compile(r'act=view')):
        row = a.find_parent('ul') or a.parent
        title = clean(a.get_text())
        items.append({'title': strip_marks(title),
                      'date': find_date(clean(row.get_text(' ')).replace(title, '')),
                      'url': urljoin(src['url'], a['href'])})
    return items


def parse_gryouth(src):
    """별마루센터 프로그램 목록. 게시일이 없어 신청 시작일을 날짜로 쓴다."""
    items = []
    for tr in soup(src['url'], src['verify']).select('table tbody tr'):
        btn = tr.select_one('td[data-cell-header="프로그램명"] button')
        period = tr.select_one('td[data-cell-header="신청기간"]')
        if btn:
            items.append({'title': clean(btn.get_text()), 'date': find_date(period.get_text(' ') if period else ''),
                          'url': src['url']})
    return items


def parse_science(src):
    """국립중앙과학관 교육 목록. 행마다 tbody가 있고, 게시일이 없어 일정 칸의 첫 날짜를 쓴다."""
    items = []
    for body in soup(src['url'], src['verify']).select('tbody.singlerow'):
        m = re.search(r"fn_moveBbsNttDetail\('(\d+)'\)", body.get('onclick', ''))
        a = body.select_one('a[title="제목"]')
        if m and a:
            title_cell = a.find_parent('td')
            rest = ' '.join(clean(td.get_text(' ')) for td in body.find_all('td') if td is not title_cell)
            items.append({'title': clean(a.get_text()), 'date': find_date(rest),
                          'url': urljoin(src['url'], 'moveBbsNttDetail.do?nttSn=' + m[1])})
    return items


CNE_BBS = 'cop/bbs/selectBoardList.do?'
GYERYONG = {'region': '충남·계룡', 'scope': '계룡시'}
CHUNGNAM = {'region': '충남·계룡', 'scope': '충남 전체'}
DAEJEON = {'region': '대전'}

# filter: 'loose'는 제외 단어만 적용, 'strict'는 포함 단어도 필요, None은 모두 수록
# parser가 None인 기관은 robots.txt가 읽기를 허용하지 않아 바로가기로만 보여 준다 (check는 판정에 쓰는 게시판 주소)
SOURCES = [
    dict(GYERYONG, key='gyeryong-notice', org='계룡시청', filter='strict', parser=None,
         url='https://gyeryong.go.kr/kr/html/sub03/030101.html'),
    dict(GYERYONG, key='gryouth-star', org='계룡시청소년별마루센터', filter=None, parser=parse_gryouth,
         url='https://www.gryouth.or.kr/prog/prgrm/star/sub03_01/list.do'),
    dict(GYERYONG, key='gryouth-star-notice', org='계룡시청소년별마루센터', filter='loose', parser=parse_table,
         url='https://www.gryouth.or.kr/bbs/BBSMSTR_000000000021/list.do'),
    dict(GYERYONG, key='gryouth-1388', org='계룡시청소년상담복지센터', filter='loose', parser=parse_table,
         url='https://www.gryouth.or.kr/bbs/BBSMSTR_000000000010/list.do'),
    dict(GYERYONG, key='cnnse-40', org='충청남도논산계룡교육지원청', filter='loose', parser=parse_brd_json,
         url='https://www.cnnse.go.kr/home/board/brdList.do?menu_cd=000040'),
    dict(GYERYONG, key='cnnse-199', org='충청남도논산계룡교육지원청', filter='loose', parser=parse_brd_json,
         url='https://www.cnnse.go.kr/home/board/brdList.do?menu_cd=000199'),
    dict(CHUNGNAM, key='chungnam-notice', org='충청남도청', filter='strict', parser=parse_table,
         url='https://www.chungnam.go.kr/cnportal/bbs/B0000250/list.do?menuNo=500452'),
    dict(CHUNGNAM, key='chungnam-gosi', org='충청남도청', filter='strict', parser=parse_table,
         url='https://www.chungnam.go.kr/cnportal/province/province/list.do?menuNo=500487'),
    dict(CHUNGNAM, key='cne', org='충청남도교육청', filter='loose', parser=None,
         url='https://www.cne.go.kr/', check='https://www.cne.go.kr/boardCnts/list.do'),
    dict(CHUNGNAM, key='icec', org='충청남도교육청진로융합교육원', filter='loose', parser=parse_brd_json,
         url='http://icec.cne.go.kr/home/board/brdList.do?menu_cd=000041'),
    dict(CHUNGNAM, key='chsl', org='충청남도교육청학생교육문화원', filter='loose', parser=parse_cne_bbs,
         url='https://chsl.cne.go.kr/' + CNE_BBS + 'menuId=MNU_0000000000001002&bbsId=BBSMSTR_000000000121'),
    dict(CHUNGNAM, key='cle', org='충청남도교육청평생교육원', filter='loose', parser=parse_cne_bbs,
         url='http://cle.cne.go.kr/' + CNE_BBS + 'menuId=MNU_0000000000000181&bbsId=BBSMSTR_000000000003'),
    dict(CHUNGNAM, key='cnbl', org='충청남도교육청남부평생교육원', filter='loose', parser=parse_cne_bbs,
         url='http://cnbl.cne.go.kr/' + CNE_BBS + 'menuId=MNU_0000000000001210&bbsId=BBSMSTR_000000000139'),
    dict(CHUNGNAM, key='cnall-elementary', org='충남평생교육정보시스템', filter='loose', parser=parse_cnall,
         url='https://www.cnall.or.kr/lecture/lectureList.do?searchTab=ING&targetCd=D'),
    dict(CHUNGNAM, key='cnall-middle', org='충남평생교육정보시스템', filter='loose', parser=parse_cnall,
         url='https://www.cnall.or.kr/lecture/lectureList.do?searchTab=ING&targetCd=E'),
    dict(CHUNGNAM, key='cnall-notice', org='충남평생교육정보시스템', filter='loose', parser=parse_table,
         url='https://www.cnall.or.kr/bbs/101/bbsPostList.do'),
    dict(CHUNGNAM, key='cnse', org='충청남도교육청과학교육원', filter='loose', parser=None,
         url='https://www.cnse.or.kr/main/board/list.action?q=d636ce2d19a523b2924c63232c3cae51ff9282aeab77ec41cdfaf1fdd875101a'),
    dict(CHUNGNAM, key='cnstc', org='충청남도교육청안전수련원', filter='loose', parser=None,
         url='https://www.cnstc.or.kr/', check='https://www.cnstc.or.kr/main/board/list.action'),
    dict(CHUNGNAM, key='cnis', org='충청남도교육청해양수련원', filter='loose', parser=None,
         url='https://www.cnis.go.kr/', check='https://www.cnis.go.kr/main/board/list.action'),
    dict(CHUNGNAM, key='chungmoo', org='충무교육원', filter='loose', parser=None,
         url='https://www.chungmoo-ei.or.kr/', check='https://www.chungmoo-ei.or.kr/main/board/list.action'),
    dict(CHUNGNAM, key='clehrd', org='충남평생교육진흥원', filter='loose', parser=None,
         url='https://www.clehrd.or.kr/', check='https://www.clehrd.or.kr/bbs/list.do'),
    dict(CHUNGNAM, key='pass-activity', org='충청남도청소년활동진흥센터', filter='loose', parser=parse_es_board,
         url='https://cn.pass.or.kr/board.es?mid=a21201030000&bid=0039'),
    dict(CHUNGNAM, key='pass-counsel', org='충청남도청소년상담복지센터', filter='loose', parser=parse_es_board,
         url='https://cn.pass.or.kr/board.es?mid=a21201020000&bid=0038'),
    dict(DAEJEON, key='science', org='국립중앙과학관', filter=None, parser=parse_science,
         url='https://www.science.go.kr/mps/0/bbs/163/moveBbsNttList.do'),
    dict(DAEJEON, key='dsecc', org='대전학생교육문화원', filter='loose', parser=parse_list,
         url='https://dsecc.djsch.kr/djsecc/main.do'),
    dict(DAEJEON, key='seogu-notice', org='대전 서구청', filter='strict', parser=parse_table,
         url='https://www.seogu.go.kr/bbs/BBSMSTR_000000000238/list.do'),
    dict(DAEJEON, key='seogu-exp', org='대전 서구청', filter='strict', parser=parse_table,
         url='https://www.seogu.go.kr/prog/exprnLnbns/yeyak/sub03_01/list.do'),
    dict(DAEJEON, key='kriss', org='한국표준과학연구원', filter='strict', parser=parse_es_board,
         url='https://www.kriss.re.kr/board.es?mid=a10503000000&bid=0002'),
    dict(DAEJEON, key='kaeri', org='한국원자력연구원', filter='strict', parser=parse_list,
         url='https://www.kaeri.re.kr/board?menuId=MENU00332'),
    dict(DAEJEON, key='kribb', org='한국생명공학연구원', filter='strict', parser=parse_table,
         url='https://www.kribb.re.kr/kor/sub03/sub03_01_01.jsp'),
    dict(DAEJEON, key='kaist', org='KAIST', filter='strict', parser=parse_table,
         url='https://www.kaist.ac.kr/kr/html/footer/0823.html'),
    dict(DAEJEON, key='daejeon', org='대전광역시 (OK예약·도서관·미술관)', filter='strict', parser=None,
         url='https://www.daejeon.go.kr/okr2019/OkrExpList.do?menuSeq=8201'),
    dict(DAEJEON, key='dje', org='대전광역시교육청', filter='loose', parser=None,
         url='https://www.dje.go.kr/', check='https://www.dje.go.kr/boardCnts/list.do'),
    dict(DAEJEON, key='desre', org='대전교육과학연구원', filter='loose', parser=None,
         url='https://desre.djsch.kr/boardCnts/list.do?boardID=107229&m=0202&s=des'),
    dict(DAEJEON, key='ulibrary', org='대전 공공도서관 (한밭·동대전·유성)', filter='loose', parser=None,
         url='https://www.u-library.kr/', check='https://www.u-library.kr/event/list'),
]


def wanted(title, mode):
    if mode is None:
        return True
    if any(word in title for word in EXCLUDE):
        return False
    return mode == 'loose' or any(word in title for word in INCLUDE)


class RobotsBlocked(Exception):
    pass


robots_cache = {}


def parse_robots(text):
    """이 수집기에 해당하는 규칙 목록. 이름으로 지정한 그룹이 있으면 그것을, 없으면 * 그룹을 쓴다."""
    groups, agents, rules, in_rules = [], [], [], False
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if ':' not in line:
            continue
        key, value = [x.strip() for x in line.split(':', 1)]
        key = key.lower()
        if key == 'user-agent':
            if in_rules:
                groups.append((agents, rules))
                agents, rules, in_rules = [], [], False
            agents.append(value.lower())
        elif key in ('allow', 'disallow'):
            in_rules = True
            rules.append((key, value))
    groups.append((agents, rules))
    named = [r for a, r in groups if BOT_NAME in a]
    return sum(named or [r for a, r in groups if '*' in a], [])


def rule_matches(path, pattern):
    regex = re.escape(pattern).replace(r'\*', '.*')
    if regex.endswith(r'\$'):
        regex = regex[:-2] + '$'
    return re.match(regex, path) is not None


def robots_allows(url, verify):
    """RFC 9309 방식: 가장 길게 일치하는 규칙을 따르고, 길이가 같으면 허용을 우선한다.
    robots.txt가 없으면(4xx) 허용으로 본다."""
    parts = urlparse(url)
    root = '%s://%s' % (parts.scheme, parts.netloc)
    if root not in robots_cache:
        res = requests.get(root + '/robots.txt', headers=HEADERS, timeout=40, verify=verify)
        if res.status_code >= 500:
            res.raise_for_status()
        text = res.text if res.status_code == 200 and '<html' not in res.text[:300].lower() else ''
        robots_cache[root] = parse_robots(text)
    path = (parts.path or '/') + ('?' + parts.query if parts.query else '')
    verdict, length = 'allow', -1
    for kind, pattern in robots_cache[root]:
        if pattern and rule_matches(path, pattern):
            if len(pattern) > length or (len(pattern) == length and kind == 'allow'):
                verdict, length = kind, len(pattern)
    return verdict == 'allow'


def with_deadline(func, seconds):
    """응답을 끝내지 않는 서버가 전체 작업을 멈추지 않도록 시간 제한을 둔다. 넘기면 TimeoutError."""
    result = {}

    def run():
        try:
            result['value'] = func()
        except Exception as err:
            result['error'] = err

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise TimeoutError('%d초 안에 끝나지 않음' % seconds)
    if 'error' in result:
        raise result['error']
    return result['value']


def collect_source(src):
    """인증서가 불완전한 기관이 있어, 검증 실패 때만 검증 없이 다시 읽는다 (공개 목록 읽기 전용).
    접속 시간 초과는 한 번 더 시도한다."""
    def run(verify):
        src['verify'] = verify
        if not robots_allows(src.get('check', src['url']), verify) or src['parser'] is None:
            raise RobotsBlocked('robots.txt가 자동 수집을 허용하지 않음')
        return src['parser'](src)

    for attempt in range(2):
        try:
            try:
                return run(True)
            except requests.exceptions.SSLError:
                return run(False)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if attempt:
                raise
            time.sleep(10)


def main():
    now = datetime.now(KST)
    today = now.strftime('%Y-%m-%d')
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime('%Y-%m-%d')
    only = set(sys.argv[1:])  # 시험용: 일부 기관만 실행하고 파일은 쓰지 않음

    previous = {}
    if OUTPUT.exists() and not only:
        previous = {n['id']: n for n in json.loads(OUTPUT.read_text(encoding='utf-8')).get('notices', [])}

    merged = dict(previous)
    statuses = []
    for src in SOURCES:
        if only and src['key'] not in only:
            continue
        status = {'key': src['key'], 'org': src['org'], 'region': src['region'], 'url': src['url'],
                  'ok': True, 'read': 0, 'kept': 0}
        try:
            items = with_deadline(lambda: collect_source(src), SOURCE_DEADLINE)
            status['read'] = len(items)
            if not items:
                raise ValueError('목록에서 공고를 찾지 못함 (게시판 구조 변경 가능)')
            for item in items:
                if not item['title'] or not wanted(item['title'], src['filter']):
                    continue
                if not item['url'].startswith(('http://', 'https://')) or urlparse(item['url']).netloc == '':
                    continue
                date = item['date'] if item['date'] and item['date'] <= today else ''
                if date and date < cutoff:
                    continue
                nid = src['key'] + '-' + hashlib.sha1(item['title'].encode('utf-8')).hexdigest()[:12]
                first_seen = previous.get(nid, {}).get('firstSeen', today)
                merged[nid] = {'id': nid, 'title': item['title'], 'org': item.get('org') or src['org'],
                               'region': src['region'],
                               'scope': src.get('scope', ''),
                               'date': date, 'url': item['url'], 'firstSeen': first_seen}
                status['kept'] += 1
                if only:
                    print('    %s | %s | %s' % (date or '----------', item['title'][:60], item['url'][:70]))
        except RobotsBlocked as err:
            status['ok'] = False
            status['blocked'] = True
            status['error'] = str(err)
        except Exception as err:  # 한 기관의 실패가 전체를 멈추지 않도록 함
            status['ok'] = False
            status['error'] = '%s: %s' % (type(err).__name__, str(err)[:200])
        statuses.append(status)
        print('%-20s %s read=%d kept=%d %s' % (src['key'], 'ok  ' if status['ok'] else 'FAIL', status['read'],
                                               status['kept'], status.get('error', '')))
        if not status.get('blocked'):
            time.sleep(1)

    if only:
        return 0

    # 이번에 실패한 기관의 지난 공고는 그대로 남기고, 오래된 공고는 정리함
    def sort_key(n):
        return n['date'] or n['firstSeen']

    # 제외 단어를 바꾸면 이미 저장된 공고에도 바로 적용되도록 다시 거름
    modes = {src['key']: src['filter'] for src in SOURCES}
    notices = [n for n in merged.values() if sort_key(n) >= cutoff
               and wanted(n['title'], modes.get(n['id'].rsplit('-', 1)[0], 'loose'))]
    # 제목에 학년이 보이면 적어 둠 (사이트 학년 필터용). 못 읽으면 [] → "학년 확인 필요"
    from collect_programs import parse_grades  # 서로 불러오는 순서 문제를 피하려고 여기서 불러옴
    scopes = {src['key']: src.get('scope', '') for src in SOURCES}
    for n in notices:
        n['grades'] = parse_grades(n['title']) or []
        n['scope'] = scopes.get(n['id'].rsplit('-', 1)[0], n.get('scope', ''))  # 이전에 저장된 공고에도 범위 적용
    # 같은 기관의 여러 게시판에 올라온 같은 제목은 하나만 남김
    seen = set()
    unique = []
    for n in sorted(notices, key=lambda x: (sort_key(x), x['id']), reverse=True):
        if (n['org'], n['title']) not in seen:
            seen.add((n['org'], n['title']))
            unique.append(n)

    OUTPUT.write_text(json.dumps({'updatedAt': today, 'sources': statuses, 'notices': unique[:MAX_ITEMS]},
                                 ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    ok = sum(1 for s in statuses if s['ok'])
    print('sources ok %d/%d, notices %d' % (ok, len(statuses), len(unique[:MAX_ITEMS])))
    return 0 if ok else 1  # 모든 기관이 실패할 때만 작업을 실패로 표시


if __name__ == '__main__':
    sys.exit(main())
