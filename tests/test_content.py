import unittest

from app.content import ContentCatalogue


class ContentCatalogueTests(unittest.TestCase):
    def test_seed_catalogue_has_source_fields(self):
        catalogue = ContentCatalogue()
        catalogue.load()
        self.assertGreaterEqual(len(catalogue.items), 3)
        for item in catalogue.items:
            self.assertTrue(item["source"])
            self.assertTrue(item["reference"])
            self.assertTrue(item["source_url"].startswith("https://"))


    def test_daily_hadith_catalogue_has_multiple_source_linked_items(self):
        catalogue = ContentCatalogue()
        catalogue.load()
        hadiths = catalogue.by_category("hadith")
        self.assertGreaterEqual(len(hadiths), 5)
        for item in hadiths:
            self.assertTrue(item["narrator"])
            self.assertTrue(item["source"])
            self.assertTrue(item["reference"])
            self.assertTrue(item["source_url"].startswith("https://"))

    def test_categories_match_items(self):
        catalogue = ContentCatalogue()
        catalogue.load()
        category_ids = {category["id"] for category in catalogue.categories}
        self.assertTrue(all(item["category"] in category_ids for item in catalogue.items))

    def test_public_payload_includes_direct_reference_links(self):
        catalogue = ContentCatalogue()
        catalogue.load()
        payload = catalogue.public_payload()

        self.assertTrue(payload["items"])
        self.assertTrue(all(item["source_url"].startswith("https://") for item in payload["items"]))


    def test_abu_dawud_5068_morning_and_evening_text_matches_reference(self):
        content = ContentCatalogue()
        content.load()
        morning = content.get("morning_001")
        evening = content.get("evening_001")

        # Abu Dawud 5068 gives "wa ilayka al-nushur" for both timings.
        self.assertIn("وَإِلَيْكَ النُّشُورُ", morning["text"])
        self.assertIn("وَإِلَيْكَ النُّشُورُ", evening["text"])
        self.assertEqual(morning["reference"], "5068")
        self.assertEqual(evening["reference"], "5068")


if __name__ == "__main__":
    unittest.main()
