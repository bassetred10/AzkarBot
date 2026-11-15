import unittest

from app.games import GameCatalogue


class GameCatalogueTests(unittest.TestCase):
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
