from font_support import TEST_FONT  # Complete font fixture; no network access.

import dataclasses
import io
import unittest

from PIL import Image

from tarkov_core.matching import Datacenter, MatchingResult
from tarkov_core.matching_image import display_name, render_matching_image, sections, wait_text

START = 1794036611


def center(region, name, wait, history=None):
    return Datacenter(region, name, ' '.join(name.split()), wait,
                      tuple(history) if history is not None else (wait, None, wait))


def result(**changes):
    centers = (center('ASI', 'China', 99), center('ASI', 'Japan', 30), center('ASI', 'Korea', None),
               center('ASI', 'Singapore', 61), center('RUS', 'Russia Central', 59), center('EUR', 'Europe East', 80),
               center('SAM', 'Sa   Colombia', 512), center('ME', 'Me   Dubai', 377), center('ME', 'Middle East', 524),
               center('USA', 'North America Central', 54), center('USA', 'North America West', 60),
               center('USA', 'North America South', 111), center('OCE', 'Australia', 158),
               center('EUR', 'Europe South', None), center('AF', 'Af Nigeria', 298, history=None),
               center('RUS', 'Russia East', 62), center('EUR', 'Europe West', 83))
    base = MatchingResult(24, START+86400, START+86300, START+86500, START, 300, centers)
    return dataclasses.replace(base, **changes)


class SectionTests(unittest.TestCase):
    def test_fixed_rows_ranked_fastest_and_region_summaries(self):
        fixed, ranked, regions = sections(result())
        self.assertEqual([(label, c.name if c else None) for label, c in fixed],
                         [('香港', 'China'), ('日本', 'Japan'), ('韩国', 'Korea')])
        # Eight shortest current waits, fastest first; fixed servers and missing data are left out.
        self.assertEqual([c.name for c in ranked],
                         ['North America Central', 'Russia Central', 'North America West', 'Singapore',
                          'Russia East', 'Europe East', 'Europe West', 'North America South'])
        self.assertEqual([code for code, _ in regions], ['ASI', 'USA', 'RUS', 'EUR', 'OCE', 'AF', 'ME', 'SAM'])
        self.assertEqual(dict(regions)['ASI'].name, 'Japan')
        self.assertEqual(dict(regions)['ME'].name, 'Me   Dubai')

    def test_missing_fixed_server_and_all_missing_region(self):
        centers = tuple(c for c in result().datacenters if c.name != 'Korea')
        centers += (center('XX', 'Nowhere', None),)
        fixed, ranked, regions = sections(result(datacenters=centers))
        self.assertIsNone(fixed[2][1])
        self.assertEqual(regions[-1][0], 'XX')
        self.assertNotIn('Nowhere', [c.name for c in ranked])

    def test_wait_text_and_names(self):
        self.assertEqual([wait_text(v) for v in (0, 59.6, 95, 646, None)], ['0:00', '1:00', '1:35', '10:46', '—'])
        names = {c.name: display_name(c) for c in result().datacenters}
        self.assertEqual(names['Sa   Colombia'], 'Colombia')
        self.assertEqual(names['Me   Dubai'], 'Dubai')
        self.assertEqual(names['Af Nigeria'], 'Nigeria')
        self.assertEqual(names['Middle East'], 'Middle East')
        self.assertEqual(display_name(center('ASI', 'Sa Paulo', 1)), 'Sa Paulo')


class RenderTests(unittest.TestCase):
    def test_renders_jpeg_with_logo_for_fresh_stale_and_sparse_results(self):
        for changes in ({}, {'stale': True, 'from_cache': True},
                        {'datacenters': (center('ASI', 'China', None, history=()),)}):
            with self.subTest(changes=list(changes)):
                image = Image.open(io.BytesIO(render_matching_image(result(**changes))))
                self.assertEqual((image.format, image.width), ('JPEG', 1600))
                # The Atlas logo's blue map sits in the top-right corner of every picture.
                corner = image.convert('RGB').crop((1300, 20, 1580, 110)).getdata()
                self.assertGreater(sum(1 for r, g, b in corner if b > 190 and b > r+80), 300)
