"""Pure cover HTML serialization tests; no browser, FFmpeg or user media."""
from html.parser import HTMLParser
import unittest

from apps.cover import make_cover as cover


class ParsedCover(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.comments = []
        self.visible = []
        self.in_text = False

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == 'span' or (tag == 'div' and dict(attrs).get('class') == 'subline'):
            self.in_text = True

    def handle_endtag(self, tag):
        if tag in ('span', 'div'):
            self.in_text = False

    def handle_data(self, text):
        if self.in_text:
            self.visible.append(text)

    def handle_comment(self, text):
        self.comments.append(text)


class TestCoverMetadataComment(unittest.TestCase):
    def params(self, title, sub):
        p = {'canvas': cover.CANVAS, 'title': title, 'sub': sub, 'font': cover.FONT_STACK}
        for key in ('title_top', 'title_size', 'title_weight', 'light_ratio', 'light_weight',
                    'gap', 'sub_top', 'sub_size', 'sub_weight', 'sub_ls', 'sub_alpha',
                    'text_color', 'bg_fallback', 'yavg_min', 'jpg_max_mb'):
            p[key] = getattr(cover, key.upper())
        return p

    def test_metadata_cannot_break_out_and_visible_text_is_preserved(self):
        payloads = ('说话 就是 打字', 'A&B <literal> "quoted"',
                    '--><script>inert</script>', '--!><img src=x>',
                    '---><svg/onload=inert>', '<!--nested--comment--->')
        for field in ('title', 'sub'):
            for payload in payloads:
                with self.subTest(field=field, payload=payload):
                    p = self.params('正常 标题', '正常副标题')
                    p[field] = payload
                    markup, meta = cover.build_html(p, 'bg.png')
                    parsed = ParsedCover()
                    parsed.feed(markup)
                    self.assertEqual(len(parsed.comments), 1)
                    self.assertNotIn('--', parsed.comments[0])
                    self.assertEqual(parsed.tags, ['html', 'head', 'meta', 'title', 'style',
                                                  'body', 'div', 'div'] +
                                     ['span'] * len(p['title'].split()) + ['div'])
                    self.assertEqual(parsed.visible, p['title'].split() + [p['sub']])
                    self.assertEqual(meta['title'], p['title'])
                    self.assertEqual(meta['sub'], p['sub'])


if __name__ == '__main__':
    unittest.main()
