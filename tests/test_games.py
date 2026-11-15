import unittest

from app.games import GameCatalogue


class GameCatalogueTests(unittest.TestCase):

    def test_catalogue_covers_all_seven_topics(self):
        games = GameCatalogue()
        games.load()
        counts = games.counts()
        self.assertEqual(set(counts), {
            "quran", "seerah", "hadith", "prophets",
            "worship", "companions", "manners",
        })
        self.assertEqual(sum(counts.values()), len(games.questions))
        self.assertTrue(all(count >= 5 for count in counts.values()))

    def test_random_question_respects_selected_topic(self):
        games = GameCatalogue()
        games.load()
        for category_id in games.counts():
            for _ in range(5):
                question = games.random_question(category_id)
                self.assertEqual(question["category_id"], category_id)

    def test_questions_have_valid_answer_indexes(self):
        games = GameCatalogue()
        games.load()
        self.assertGreaterEqual(len(games.questions), 8)
        for question in games.questions:
            self.assertGreaterEqual(question["answer"], 0)
            self.assertLess(question["answer"], len(question["options"]))
            self.assertTrue(question["source"])


if __name__ == "__main__":
    unittest.main()
