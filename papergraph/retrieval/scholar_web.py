"""Direct Scholar HTML parsing. Access failures are never empty neighborhoods."""
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from papergraph.retrieval.openalex import RetrievalError


def parse_scholar_html(html: str) -> dict:
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(' ', strip=True).lower()
    if soup.select_one('#gs_captcha_ccl, #captcha-form, .g-recaptcha') or any(
        marker in text for marker in ['unusual traffic', 'not a robot', 'automated queries', '异常流量']
    ):
        raise RetrievalError('Google Scholar access restricted or CAPTCHA required; no empty-result fallback')
    container = soup.select_one('#gs_res_ccl_mid')
    if container is None:
        raise RetrievalError('Google Scholar page format unrecognized; cannot interpret as zero citations')
    results = []
    for block in container.select('.gs_r.gs_or'):
        heading = block.select_one('.gs_rt')
        if heading is None:
            raise RetrievalError('Google Scholar result missing title')
        for label in heading.select('.gs_ct1, .gs_ct2'):
            label.decompose()
        link = heading.find('a', href=True)
        title = heading.get_text(' ', strip=True)
        if not title:
            raise RetrievalError('Google Scholar returned an empty title')
        row = {'title': title, 'result_id': block.get('data-cid'),
               'link': link['href'] if link else '', 'inline_links': {}}
        for anchor in block.select('.gs_fl a[href]'):
            params = parse_qs(urlparse(anchor['href']).query)
            if params.get('cites'):
                row['inline_links']['cited_by'] = {'cites_id': params['cites'][0]}
                break
        results.append(row)
    if not results and not any(marker in text for marker in [
        'did not match any articles', 'did not match any documents', 'no articles found', '没有找到', '未找到'
    ]):
        raise RetrievalError('Google Scholar returned no parseable results without an explicit no-results message')
    next_start = None
    for anchor in soup.select('#gs_n a[href]'):
        params = parse_qs(urlparse(anchor['href']).query)
        if anchor.select_one('.gs_ico_nav_next') or anchor.get('aria-label', '').lower() in {'next', '下一页'}:
            if not params.get('start', [''])[0].isdigit():
                raise RetrievalError('Google Scholar next-page offset is invalid')
            next_start = int(params['start'][0])
            break
    return {'organic_results': results, 'pagination': {'next': next_start is not None},
            'next_start': next_start}
