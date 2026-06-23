from dataclasses import FrozenInstanceError
from types import SimpleNamespace

from django.test import SimpleTestCase

from src.rankings.services.divisions import Division, build_divisions


def _rows(n):
    # rows já ordenadas por posição (1..n), objeto mínimo com .position
    return [SimpleNamespace(position=i) for i in range(1, n + 1)]


def _sizes(divisions):
    return [(d.key, d.label, d.color, len(d.rows)) for d in divisions]


class BuildDivisionsTests(SimpleTestCase):
    def test_seven_or_fewer_returns_single_plain_division(self):
        for n in (1, 3, 7):
            divisions = build_divisions(_rows(n))
            self.assertEqual(len(divisions), 1, f"N={n}")
            self.assertEqual(divisions[0].key, "plain")
            self.assertEqual(divisions[0].color, "plain")
            self.assertEqual(len(divisions[0].rows), n)

    def test_eight_participants_one_middle_serie(self):
        divisions = build_divisions(_rows(8))
        self.assertEqual(
            _sizes(divisions),
            [
                ("liga", "Liga dos Campeões", "gold", 3),
                ("serie", "Série A", "blue", 1),
                ("zona", "Zona de Rebaixamento", "red", 4),
            ],
        )

    def test_ten_participants_three_middle_series(self):
        divisions = build_divisions(_rows(10))
        self.assertEqual(
            _sizes(divisions),
            [
                ("liga", "Liga dos Campeões", "gold", 3),
                ("serie", "Série A", "blue", 1),
                ("serie", "Série B", "gray", 1),
                ("serie", "Série C", "yellow", 1),
                ("zona", "Zona de Rebaixamento", "red", 4),
            ],
        )

    def test_fifteen_participants_middle_split_3_3_2(self):
        divisions = build_divisions(_rows(15))
        middle = [d for d in divisions if d.key == "serie"]
        self.assertEqual([len(d.rows) for d in middle], [3, 3, 2])
        # Liga = posições 1..3, Zona = 12..15
        self.assertEqual([r.position for r in divisions[0].rows], [1, 2, 3])
        self.assertEqual([r.position for r in divisions[-1].rows], [12, 13, 14, 15])

    def test_thirtyseven_keeps_three_middle_of_ten(self):
        middle = [d for d in build_divisions(_rows(37)) if d.key == "serie"]
        self.assertEqual([len(d.rows) for d in middle], [10, 10, 10])

    def test_thirtyeight_grows_to_four_middle(self):
        middle = [d for d in build_divisions(_rows(38)) if d.key == "serie"]
        self.assertEqual([len(d.rows) for d in middle], [8, 8, 8, 7])
        self.assertEqual([d.label for d in middle], ["Série A", "Série B", "Série C", "Série D"])

    def test_sixty_caps_each_middle_at_ten(self):
        middle = [d for d in build_divisions(_rows(60)) if d.key == "serie"]
        self.assertTrue(all(len(d.rows) <= 10 for d in middle))
        self.assertEqual([len(d.rows) for d in middle], [9, 9, 9, 9, 9, 8])

    def test_series_colors_cycle_after_seven(self):
        middle = [d for d in build_divisions(_rows(7 + 8 * 10 + 5)) if d.key == "serie"]
        # 8+ séries: a 8ª (índice 7) volta para "blue"
        self.assertGreaterEqual(len(middle), 8)
        self.assertEqual(middle[7].color, "blue")

    def test_position_range_labels(self):
        divisions = build_divisions(_rows(15))
        self.assertEqual(divisions[0].position_range, "top 3")
        self.assertEqual(divisions[-1].position_range, "últimos 4")
        serie_a = divisions[1]
        self.assertEqual(serie_a.position_range, "4º–6º")

    def test_custom_position_getter_for_dict_rows(self):
        rows = [{"position": i} for i in range(1, 11)]
        divisions = build_divisions(rows, position_getter=lambda row: row["position"])
        self.assertEqual(divisions[0].key, "liga")
        self.assertEqual(len(divisions[0].rows), 3)
        self.assertIs(divisions[0].rows[0], rows[0])

    def test_division_is_frozen(self):
        with self.assertRaises(FrozenInstanceError):
            Division(key="x", label="y", color="z", position_range="w", rows=[]).key = "mutated"
