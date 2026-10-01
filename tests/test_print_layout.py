from __future__ import annotations

import unittest

from app.backend.print_layout import (
    PAGE_HEIGHT_MM,
    PAGE_WIDTH_MM,
    calculate_fit,
    calculate_layout,
    cut_guide_lines,
    mm_to_points,
)


class PrintLayoutTests(unittest.TestCase):
    def test_default_two_by_six_five_strip_count_fits_but_six_does_not(self) -> None:
        settings = {"stripsPerPage": 5}
        five = calculate_fit(settings)
        six = calculate_fit({**settings, "stripsPerPage": 6})

        self.assertTrue(five["valid"])
        self.assertEqual(five["maximumCount"], 5)
        self.assertFalse(six["valid"])
        self.assertEqual(six["requestedCount"], 6)
        self.assertEqual(six["maximumCount"], 5)
        self.assertGreater(six["verticalOverflowMm"], 0)

    def test_partial_final_page_is_centered_and_keeps_physical_size(self) -> None:
        layout = calculate_layout({"cutGuides": False}, 13)

        self.assertEqual((layout["pageWidthMm"], layout["pageHeightMm"]), (297.0, 210.0))
        self.assertEqual([len(page["placements"]) for page in layout["pages"]], [5, 5, 3])
        last = layout["pages"][-1]["placements"]
        self.assertEqual([p["widthMm"] for p in last], [50.8] * 3)
        self.assertEqual([p["heightMm"] for p in last], [152.4] * 3)
        self.assertAlmostEqual(last[0]["xMm"], 69.3, places=5)
        self.assertAlmostEqual(last[-1]["xMm"] + last[-1]["widthMm"], 227.7, places=5)
        self.assertTrue(all(last[i]["xMm"] < last[i + 1]["xMm"] for i in range(2)))

    def test_pages_are_deterministic_and_keep_strip_ids(self) -> None:
        strips = [{"id": f"strip-{i}"} for i in range(7)]
        first = calculate_layout({"stripsPerPage": 5}, strips)
        second = calculate_layout({"stripsPerPage": 5}, strips)

        self.assertEqual(first, second)
        self.assertEqual(first["pages"][0]["placements"][0]["stripId"], "strip-0")
        self.assertEqual(first["pages"][1]["placements"][1]["stripId"], "strip-6")
        for page in first["pages"]:
            for placement in page["placements"]:
                self.assertEqual(placement["widthMm"], 50.8)
                self.assertEqual(placement["heightMm"], 152.4)

    def test_invalid_count_is_not_scaled_to_fit(self) -> None:
        layout = calculate_layout({"stripsPerPage": 6}, 6)

        self.assertFalse(layout["fit"]["valid"])
        self.assertEqual([len(page["placements"]) for page in layout["pages"]], [5, 1])
        self.assertTrue(all(p["xMm"] >= 5 for page in layout["pages"] for p in page["placements"]))
        self.assertTrue(all(p["widthMm"] == 50.8 for page in layout["pages"] for p in page["placements"]))

    def test_auto_capacity_and_portrait_keep_strips_upright(self) -> None:
        landscape = calculate_layout({}, 8)
        portrait = calculate_layout({"orientation": "portrait"}, 8)
        self.assertEqual(landscape["fit"]["maximumCount"], 5)
        self.assertEqual(portrait["fit"]["maximumCount"], 3)
        self.assertEqual([len(page["placements"]) for page in portrait["pages"]], [3, 3, 2])
        self.assertEqual((portrait["pageWidthMm"], portrait["pageHeightMm"]), (210, 297))
        self.assertTrue(all(p["widthMm"] == 50.8 and p["heightMm"] == 152.4
                            for page in portrait["pages"] for p in page["placements"]))

    def test_short_legacy_strip_can_use_two_rows_without_rotation(self) -> None:
        layout = calculate_layout({"stripWidthMm": 54, "stripHeightMm": 72}, 10)
        self.assertEqual(layout["fit"]["maximumCount"], 10)
        self.assertEqual(len(layout["pages"][0]["placements"]), 10)
        self.assertEqual(len({p["yMm"] for p in layout["pages"][0]["placements"]}), 2)

    def test_mm_to_points_is_exact_for_two_by_six_inches(self) -> None:
        self.assertAlmostEqual(mm_to_points(50.8), 144)
        self.assertAlmostEqual(mm_to_points(152.4), 432)

    def test_vertical_overflow_has_zero_valid_capacity(self) -> None:
        fit = calculate_fit({"stripHeightMm": PAGE_HEIGHT_MM + 1})

        self.assertFalse(fit["valid"])
        self.assertEqual(fit["maximumCount"], 0)
        self.assertGreater(fit["verticalOverflowMm"], 0)

    def test_cut_guides_are_outside_strip_bounds(self) -> None:
        x, y, width, height = 20.0, 30.0, 50.0, 150.0
        guides = cut_guide_lines(x, y, width, height)

        self.assertEqual(len(guides), 8)
        for line in guides:
            points = ((line["x1Mm"], line["y1Mm"]), (line["x2Mm"], line["y2Mm"]))
            for px, py in points:
                on_vertical_edge = (abs(px - x) < 1e-6 or abs(px - (x + width)) < 1e-6) and (py < y or py > y + height)
                on_horizontal_edge = (abs(py - y) < 1e-6 or abs(py - (y + height)) < 1e-6) and (px < x or px > x + width)
                self.assertTrue(on_vertical_edge or on_horizontal_edge)

    def test_cut_guides_are_shared_in_layout_geometry(self) -> None:
        layout = calculate_layout({"cutGuides": True}, 1)

        self.assertIn("cutGuides", layout["pages"][0]["placements"][0])
        self.assertEqual(len(layout["pages"][0]["placements"][0]["cutGuides"]), 8)

    def test_adjacent_cut_guides_stop_inside_the_inter_strip_gap(self) -> None:
        layout = calculate_layout({"cutGuides": True, "stripsPerPage": 5}, 5)
        placements = layout["pages"][0]["placements"]

        for left_strip, right_strip in zip(placements, placements[1:]):
            shared_y = left_strip["yMm"]
            left_edge = left_strip["xMm"] + left_strip["widthMm"]
            right_edge = right_strip["xMm"]
            left_mark = next(
                line for line in left_strip["cutGuides"]
                if line["y1Mm"] == shared_y and line["y2Mm"] == shared_y
                and line["x1Mm"] > left_edge
            )
            right_mark = next(
                line for line in right_strip["cutGuides"]
                if line["y1Mm"] == shared_y and line["y2Mm"] == shared_y
                and line["x2Mm"] < right_edge
            )
            self.assertGreater(left_mark["x1Mm"], left_edge)
            self.assertLess(left_mark["x2Mm"], right_edge)
            self.assertGreater(right_mark["x1Mm"], left_edge)
            self.assertLess(right_mark["x2Mm"], right_edge)
            self.assertLessEqual(left_mark["x2Mm"], right_mark["x1Mm"])


if __name__ == "__main__":
    unittest.main()
