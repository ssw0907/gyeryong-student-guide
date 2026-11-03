"""기관 게시판에서 초·중학생 관련 공고의 제목·날짜·링크를 모아 data/notices.json에 저장한다.

robots.txt가 허용하는 게시판만 읽는다. 한 기관이 실패해도 나머지는 계속 수집하고,
실패한 기관은 파일의 sources에 기록한다.
기관을 추가하려면 SOURCES에 항목을 넣는다.
"""
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

OUTPUT = Path(__file__).resolve().parent.parent / 'data' / 'notices.json'
KST = timezone(timedelta(hours=9))
KEEP_DAYS = 180
MAX_ITEMS = 300
BOT_NAME = 'gyeryong-student-guide'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; gyeryong-student-guide/1.0; '
                         '+https://github.com/ssw0907/gyeryong-student-guide)'}

# 제목에 하나라도 있어야 수록 (filter가 켜진 기관만)
INCLUDE = ['초등', '중학', '중등', '초·중', '초중', '학생', '청소년', '어린이', '아동', '가족', '자녀', '학부모',
           '영재', '장학', '방학', '캠프', '체험', '과학', '독서', '진로', '코딩', '탐방', '공모전', '축제', '꿈']
# 제목에 하나라도 있으면 제외
EXCLUDE = ['채용', '합격', '임기제', '공무원', '기간제', '입찰', '심의', '심사 결과', '선정결과', '선정 결과', '결과 안내',
           '결과 발표', '결과 공고', '민간위탁', '교직원', '교원', '교사', '지도사', '직무연수', '성인', '어르신', '노인',
           '시니어', '청년', '고등학생', '고등학교', '고교', '고입', '대입', '휴원', '휴관', '수요조사', '만족도', '성료',
           '을지연습', '포상', '유공', '담당자', '연수', '심포지엄', '학술대회', '전문지원기관', '결과', '산업안전', '운영터전']

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
    res = requests.get(url, headers=HEADERS, timeout=30, verify=verify)
    res.raise_for_status()
    if not res.encoding or res.encoding.lower() == 'iso-8859-1':
        res.encoding = res.apparent_encoding
    return res


def soup(url, verify):
    return BeautifulSoup(fetch(url, verify).text, 'html.parser')


def parse_table(src):
    """표 형식 게시판. 제목은 행에서 글자가 가장 긴 링크, 날짜는 제목 칸을 뺀 나머지 칸에서 찾는다."""
    items = []
    for tr in soup(src['url'], src['verify']).select('table tbody tr'):
        links = [a for a in tr.find_all('a') if clean(a.get_text())]
        if not links:
            continue
        a = max(links, key=lambda x: len(clean(x.get_text())))
        href = a.get('href', '')
        # 파일 직접 받기와 스크립트 링크는 상세 주소가 아니므로 게시판 주소로 연결
        direct = href and not href.startswith(('#', 'javascript')) and 'dl.action' not in href
        title_cell = a.find_parent('td')
        rest = ' '.join(clean(td.get_text(' ')) for td in tr.find_all('td') if td is not title_cell)
        items.append({'title': clean(a.get_text()), 'date': find_date(rest),
                      'url': urljoin(src['url'], href) if direct else src['url']})
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


def parse_pass_board(src):
    """cn.pass.or.kr 게시판. 표가 아닌 ul 목록이다."""
    items = []
    for a in soup(src['url'], src['verify']).find_all('a', href=re.compile(r'act=view')):
        row = a.find_parent('ul') or a.parent
        title = clean(a.get_text())
        items.append({'title': re.sub(r'^(공지\s*)?(새글\s*)?', '', title),
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
SOURCES = [
    {'key': 'gyeryong-notice', 'org': '계룡시청', 'region': '충남·계룡', 'filter': True, 'parser': parse_table,
     'url': 'https://gyeryong.go.kr/kr/html/sub03/030101.html'},
    {'key': 'gyeryong-gosi', 'org': '계룡시청', 'region': '충남·계룡', 'filter': True, 'parser': parse_table,
     'url': 'https://gyeryong.go.kr/kr/html/sub03/030102.html'},
    {'key': 'gryouth-star', 'org': '계룡시청소년별마루센터', 'region': '충남·계룡', 'filter': False, 'parser': parse_gryouth,
     'url': 'https://www.gryouth.or.kr/prog/prgrm/star/sub03_01/list.do'},
    {'key': 'cnnse-40', 'org': '충청남도논산계룡교육지원청', 'region': '충남·계룡', 'filter': True, 'parser': parse_brd_json,
     'url': 'https://www.cnnse.go.kr/home/board/brdList.do?menu_cd=000040'},
    {'key': 'cnnse-199', 'org': '충청남도논산계룡교육지원청', 'region': '충남·계룡', 'filter': True, 'parser': parse_brd_json,
     'url': 'https://www.cnnse.go.kr/home/board/brdList.do?menu_cd=000199'},
    {'key': 'icec', 'org': '충청남도교육청진로융합교육원', 'region': '충남·계룡', 'filter': True, 'parser': parse_brd_json,
     'url': 'http://icec.cne.go.kr/home/board/brdList.do?menu_cd=000041'},
    {'key': 'chsl', 'org': '충청남도교육청학생교육문화원', 'region': '충남·계룡', 'filter': True, 'parser': parse_cne_bbs,
     'url': 'https://chsl.cne.go.kr/' + CNE_BBS + 'menuId=MNU_0000000000001002&bbsId=BBSMSTR_000000000121'},
    {'key': 'cle', 'org': '충청남도교육청평생교육원', 'region': '충남·계룡', 'filter': True, 'parser': parse_cne_bbs,
     'url': 'http://cle.cne.go.kr/' + CNE_BBS + 'menuId=MNU_0000000000000181&bbsId=BBSMSTR_000000000003'},
    {'key': 'cnbl', 'org': '충청남도교육청남부평생교육원', 'region': '충남·계룡', 'filter': True, 'parser': parse_cne_bbs,
     'url': 'http://cnbl.cne.go.kr/' + CNE_BBS + 'menuId=MNU_0000000000001210&bbsId=BBSMSTR_000000000139'},
    {'key': 'cnse-gifted', 'org': '충청남도교육청과학교육원', 'region': '충남·계룡', 'filter': True, 'parser': parse_table,
     'url': 'https://www.cnse.or.kr/main/board/list.action?q=d93832d83fa7d414e3be7ada72de790793969fbac8b6b987b03c1e741c5ea8eb'},
    {'key': 'cnse-notice', 'org': '충청남도교육청과학교육원', 'region': '충남·계룡', 'filter': True, 'parser': parse_table,
     'url': 'https://www.cnse.or.kr/main/board/list.action?q=d636ce2d19a523b2924c63232c3cae51ff9282aeab77ec41cdfaf1fdd875101a'},
    {'key': 'pass-activity', 'org': '충청남도청소년활동진흥센터', 'region': '충남·계룡', 'filter': True, 'parser': parse_pass_board,
     'url': 'https://cn.pass.or.kr/board.es?mid=a21201030000&bid=0039'},
    {'key': 'pass-counsel', 'org': '충청남도청소년상담복지센터', 'region': '충남·계룡', 'filter': True, 'parser': parse_pass_board,
     'url': 'https://cn.pass.or.kr/board.es?mid=a21201020000&bid=0038'},
    {'key': 'science', 'org': '국립중앙과학관', 'region': '대전', 'filter': False, 'parser': parse_science,
     'url': 'https://www.science.go.kr/mps/0/bbs/163/moveBbsNttList.do'},
]


def wanted(title):
    if any(word in title for word in EXCLUDE):
        return False
    return any(word in title for word in INCLUDE)


class RobotsBlocked(Exception):
    pass


robots_cache = {}


def robots_allows(url, verify):
    """사이트의 robots.txt가 이 수집기의 접근을 허용하는지 확인한다. 파일이 없으면(4xx) 허용으로 본다."""
    parts = urlparse(url)
    root = '%s://%s' % (parts.scheme, parts.netloc)
    if root not in robots_cache:
        res = requests.get(root + '/robots.txt', headers=HEADERS, timeout=30, verify=verify)
        if res.status_code >= 500:
            res.raise_for_status()
        parser = RobotFileParser()
        parser.parse(res.text.splitlines() if res.status_code == 200 else [])
        robots_cache[root] = parser
    return robots_cache[root].can_fetch(BOT_NAME, url)


def collect_source(src):
    """인증서가 불완전한 기관이 있어, 검증 실패 때만 검증 없이 다시 읽는다 (공개 목록 읽기 전용)."""
    def run(verify):
        src['verify'] = verify
        if not robots_allows(src['url'], verify):
            raise RobotsBlocked('robots.txt가 자동 수집을 허용하지 않음')
        return src['parser'](src)

    try:
        return run(True)
    except requests.exceptions.SSLError:
        return run(False)


def main():
    now = datetime.now(KST)
    today = now.strftime('%Y-%m-%d')
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime('%Y-%m-%d')

    previous = {}
    if OUTPUT.exists():
        previous = {n['id']: n for n in json.loads(OUTPUT.read_text(encoding='utf-8')).get('notices', [])}

    merged = dict(previous)
    statuses = []
    for src in SOURCES:
        status = {'key': src['key'], 'org': src['org'], 'url': src['url'], 'ok': True, 'read': 0, 'kept': 0}
        try:
            items = collect_source(src)
            status['read'] = len(items)
            if not items:
                raise ValueError('목록에서 공고를 찾지 못함 (게시판 구조 변경 가능)')
            for item in items:
                if src['filter'] and not wanted(item['title']):
                    continue
                if not item['url'].startswith(('http://', 'https://')) or urlparse(item['url']).netloc == '':
                    continue
                date = item['date'] if item['date'] and item['date'] <= today else ''
                if date and date < cutoff:
                    continue
                nid = src['key'] + '-' + hashlib.sha1(item['title'].encode('utf-8')).hexdigest()[:12]
                first_seen = previous.get(nid, {}).get('firstSeen', today)
                merged[nid] = {'id': nid, 'title': item['title'], 'org': src['org'], 'region': src['region'],
                               'date': date, 'url': item['url'], 'firstSeen': first_seen}
                status['kept'] += 1
        except RobotsBlocked as err:
            status['ok'] = False
            status['blocked'] = True
            status['error'] = str(err)
        except Exception as err:  # 한 기관의 실패가 전체를 멈추지 않도록 함
            status['ok'] = False
            status['error'] = '%s: %s' % (type(err).__name__, str(err)[:200])
        statuses.append(status)
        print('%-16s %s read=%d kept=%d %s' % (src['key'], 'ok  ' if status['ok'] else 'FAIL', status['read'],
                                               status['kept'], status.get('error', '')))
        time.sleep(1)

    def sort_key(n):
        return n['date'] or n['firstSeen']

    notices = [n for n in merged.values() if sort_key(n) >= cutoff]
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
